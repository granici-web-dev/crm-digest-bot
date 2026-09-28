import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

from digest.mefi.models import MefiSearchPage

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]

# Реальный лимит mefi ~1 req/s по IP, а не 600/мин из X-RateLimit-*
# (docs/mefi-api-notes.md, «Лимиты и правило паузы»).
MEFI_MIN_REQUEST_INTERVAL_SECONDS = 1.2
# Без Retry-After ждём целое окно burst: IP-лимит mefi 10 запросов за 10 с (там же).
RETRY_AFTER_FALLBACK_SECONDS = 10.0
# Retry-After от сервера не должен повесить ежедневный снапшот на час.
RETRY_AFTER_CAP_SECONDS = 120.0
# Одиночный 429 прогон не валит (docs/mefi-api-notes.md), но пять подряд при паузе
# 1.2 с и ожидании Retry-After значат бан IP; дальше пусть решает повтор в 19:10.
MAX_CONSECUTIVE_RATE_LIMITS = 5
SEARCH_PER_PAGE = 100


class MefiRateLimitExceeded(Exception):
    def __init__(self, rate_limited_count: int) -> None:
        super().__init__(f"mefi вернул 429 {rate_limited_count} раз подряд")
        self.rate_limited_count = rate_limited_count


class MefiSearchUnsuccessful(Exception):
    def __init__(self, page_number: int) -> None:
        super().__init__(f"mefi вернул success: false на странице {page_number}")


class RequestPacer:
    def __init__(
        self,
        min_interval_seconds: float,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._clock = clock
        self._lock = asyncio.Lock()
        self._next_request_at = float("-inf")

    async def wait_turn(self) -> None:
        async with self._lock:
            delay = self._next_request_at - self._clock()
            if delay > 0:
                await self._sleep(delay)
            self._next_request_at = self._clock() + self._min_interval_seconds

    def hold(self, seconds: float) -> None:
        self._next_request_at = max(self._next_request_at, self._clock() + seconds)


# Один на процесс: IP-лимит mefi общий для всех задач, темп нельзя держать по клиенту.
process_request_pacer = RequestPacer(MEFI_MIN_REQUEST_INTERVAL_SECONDS)


@dataclass(frozen=True)
class MefiLeadsDump:
    leads: list[dict[str, Any]]
    api_total: int
    rate_limited_count: int


@dataclass(frozen=True)
class MefiClientsDump:
    clients: list[dict[str, Any]]
    api_total: int
    rate_limited_count: int


def retry_after_seconds(response: httpx.Response) -> float:
    header_value = response.headers.get("Retry-After")
    if header_value is None or not header_value.isdigit():
        return RETRY_AFTER_FALLBACK_SECONDS
    return min(float(header_value), RETRY_AFTER_CAP_SECONDS)


def create_mefi_http_client(base_url: str, api_key: SecretStr) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
        timeout=30.0,
    )


class MefiClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        pacer: RequestPacer = process_request_pacer,
    ) -> None:
        self._http_client = http_client
        self._pacer = pacer

    async def search_all_leads(self) -> MefiLeadsDump:
        leads_by_id: dict[int, dict[str, Any]] = {}
        leads_without_int_id: list[dict[str, Any]] = []
        rate_limited_count = 0
        page_number = 1
        while True:
            # Пустой body отдаёт только lifecycle active (CLAUDE.md, ловушки mefi).
            page, page_rate_limited_count = await self._search_page(
                "/leads/search",
                {"lifecycle": ["active", "lost", "junk"]},
                page_number,
                rate_limited_count,
            )
            rate_limited_count = page_rate_limited_count
            for lead in page.data:
                lead_id = lead.get("id")
                if isinstance(lead_id, int):
                    leads_by_id[lead_id] = lead
                else:
                    leads_without_int_id.append(lead)
            if page_number >= page.meta.total_pages:
                return MefiLeadsDump(
                    leads=[*leads_by_id.values(), *leads_without_int_id],
                    api_total=page.meta.total,
                    rate_limited_count=rate_limited_count,
                )
            page_number += 1

    async def search_all_clients(self) -> MefiClientsDump:
        clients_by_id: dict[int, dict[str, Any]] = {}
        clients_without_int_id: list[dict[str, Any]] = []
        rate_limited_count = 0
        page_number = 1
        while True:
            # У клиентов, в отличие от лидов, пустой filters.state отдаёт все состояния
            # (docs/mefi-clients-notes.md, «По state»).
            page, page_rate_limited_count = await self._search_page(
                "/clients/search", {}, page_number, rate_limited_count
            )
            rate_limited_count = page_rate_limited_count
            for client in page.data:
                client_id = client.get("id")
                if isinstance(client_id, int):
                    clients_by_id[client_id] = client
                else:
                    clients_without_int_id.append(client)
            if page_number >= page.meta.total_pages:
                return MefiClientsDump(
                    clients=[*clients_by_id.values(), *clients_without_int_id],
                    api_total=page.meta.total,
                    rate_limited_count=rate_limited_count,
                )
            page_number += 1

    async def _search_page(
        self, path: str, filters: dict[str, Any], page_number: int, rate_limited_count: int
    ) -> tuple[MefiSearchPage, int]:
        # created_at asc: новые записи во время выгрузки уходят в конец и не сдвигают страницы;
        # сортировки по id в mefi нет. Удаление записи во время выгрузки всё же сдвигает страницы
        # назад и теряет одну запись; такой пропуск ловит проверка полноты снапшота.
        request_body = {
            "filters": filters,
            "sort": "created_at",
            "order": "asc",
            "page": page_number,
            "per_page": SEARCH_PER_PAGE,
        }
        consecutive_rate_limits = 0
        while True:
            await self._pacer.wait_turn()
            started_at = time.monotonic()
            response = await self._http_client.post(path, json=request_body)
            duration_ms = round((time.monotonic() - started_at) * 1000)
            request_id = response.headers.get("x-request-id")
            if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                rate_limited_count += 1
                consecutive_rate_limits += 1
                logger.warning(
                    "mefi 429",
                    extra={
                        "path": path,
                        "page": page_number,
                        "request_id": request_id,
                        "attempt": consecutive_rate_limits,
                    },
                )
                if consecutive_rate_limits >= MAX_CONSECUTIVE_RATE_LIMITS:
                    raise MefiRateLimitExceeded(rate_limited_count)
                # Бан по IP держит весь процесс, поэтому ожидание идёт через общий пейсер.
                self._pacer.hold(retry_after_seconds(response))
                continue
            logger.info(
                "mefi search page",
                extra={
                    "path": path,
                    "page": page_number,
                    "status_code": response.status_code,
                    "request_id": request_id,
                    "duration_ms": duration_ms,
                },
            )
            response.raise_for_status()
            page = MefiSearchPage.model_validate_json(response.content)
            if not page.success:
                raise MefiSearchUnsuccessful(page_number)
            return page, rate_limited_count
