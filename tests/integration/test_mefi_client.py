import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pydantic import SecretStr

from digest.mefi.client import (
    MefiClient,
    MefiRateLimitExceeded,
    RequestPacer,
    create_mefi_http_client,
)
from factories import make_lead, make_search_page, recorded_search_leads

BASE_URL = "https://mefi.test/api/v1"
SEARCH_URL = f"{BASE_URL}/leads/search"


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
async def mefi_client(fake_time: FakeTime) -> AsyncIterator[MefiClient]:
    async with create_mefi_http_client(BASE_URL, SecretStr("test-key")) as http_client:
        yield MefiClient(
            http_client,
            pacer=RequestPacer(1.2, sleep=fake_time.sleep, clock=fake_time.clock),
            sleep=fake_time.sleep,
        )


@respx.mock
async def test_search_body_requests_all_lifecycles_per_page_100(mefi_client: MefiClient) -> None:
    route = respx.post(SEARCH_URL).respond(
        json=make_search_page(recorded_search_leads(), total=2987)
    )

    dump = await mefi_client.search_all_leads()

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "filters": {"lifecycle": ["active", "lost", "junk"]},
        "sort": "created_at",
        "order": "asc",
        "page": 1,
        "per_page": 100,
    }
    assert [lead["id"] for lead in dump.leads] == [3028, 3027, 3026]
    assert dump.api_total == 2987


@respx.mock
async def test_requests_are_spaced_at_least_1_2s(
    mefi_client: MefiClient, fake_time: FakeTime
) -> None:
    respx.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=make_search_page([make_lead(id=1)], page=1, total_pages=2)),
            httpx.Response(200, json=make_search_page([make_lead(id=2)], page=2, total_pages=2)),
        ]
    )

    await mefi_client.search_all_leads()

    assert fake_time.sleeps == [pytest.approx(1.2)]


async def test_pacer_waits_only_the_remaining_interval(fake_time: FakeTime) -> None:
    pacer = RequestPacer(1.2, sleep=fake_time.sleep, clock=fake_time.clock)

    await pacer.wait_turn()
    fake_time.now += 0.5
    await pacer.wait_turn()

    assert fake_time.sleeps == [pytest.approx(0.7)]


@respx.mock
async def test_429_waits_retry_after_and_retries(
    mefi_client: MefiClient, fake_time: FakeTime
) -> None:
    route = respx.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=make_search_page([make_lead(id=1)])),
        ]
    )

    dump = await mefi_client.search_all_leads()

    assert route.call_count == 2
    assert fake_time.sleeps == [7]
    assert dump.rate_limited_count == 1
    assert [lead["id"] for lead in dump.leads] == [1]


@respx.mock
async def test_five_consecutive_429_fail_the_search(mefi_client: MefiClient) -> None:
    respx.post(SEARCH_URL).respond(429)

    with pytest.raises(MefiRateLimitExceeded) as raised:
        await mefi_client.search_all_leads()

    assert raised.value.rate_limited_count == 5


@respx.mock
async def test_pagination_dedupes_by_id(mefi_client: MefiClient) -> None:
    respx.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=make_search_page(
                    [make_lead(id=1), make_lead(id=2)], page=1, total_pages=2, total=3
                ),
            ),
            httpx.Response(
                200,
                json=make_search_page(
                    [make_lead(id=2), make_lead(id=3)], page=2, total_pages=2, total=3
                ),
            ),
        ]
    )

    dump = await mefi_client.search_all_leads()

    assert sorted(lead["id"] for lead in dump.leads) == [1, 2, 3]
