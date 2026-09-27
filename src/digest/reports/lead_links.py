from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Self
from urllib.parse import urlsplit

from markupsafe import Markup

from digest.config import LeadLinkSettings

LINK_SEPARATOR = Markup(", ")


@dataclass(frozen=True)
class LeadLinks:
    # Инвариант 7: id лида в группу только ссылкой #1234 на карточку в mefi, без имён и контактов.
    web_origin: str
    path: str
    limit: int

    @classmethod
    def from_mefi_base_url(cls, mefi_base_url: str, settings: LeadLinkSettings) -> Self:
        # MEFI_BASE_URL это адрес API (…/api/v1): карточка лида живёт на том же хосте, путь
        # API отбрасывается целиком, а не срезается строкой.
        parts = urlsplit(mefi_base_url)
        return cls(f"{parts.scheme}://{parts.netloc}", settings.path, settings.limit)

    def url(self, lead_id: int) -> str:
        return self.web_origin + self.path.format(lead_id=lead_id)

    def link(self, lead_id: int) -> Markup:
        return Markup('<a href="{}">#{}</a>').format(self.url(lead_id), lead_id)

    def line(self, visible_ids: Sequence[int], hidden_count: int) -> Markup:
        links = LINK_SEPARATOR.join(self.link(lead_id) for lead_id in visible_ids)
        return links + Markup(" și încă {}").format(hidden_count) if hidden_count else links

    def capped_line(self, ordered_ids: Sequence[int]) -> Markup:
        return self.line(ordered_ids[: self.limit], max(len(ordered_ids) - self.limit, 0))

    def block_ids(self, ordered_ids: Sequence[int]) -> frozenset[int]:
        # Лимит на весь блок отчёта: ссылки получают самые старые лиды блока.
        return frozenset(ordered_ids[: self.limit])

    def group_line(self, group_ids: Sequence[int], block_ids: Collection[int]) -> Markup:
        visible = [lead_id for lead_id in group_ids if lead_id in block_ids]
        if not visible:
            return Markup()
        return self.line(visible, len(group_ids) - len(visible))

    def block_lines(
        self, block_ordered_ids: Sequence[int], groups_ids: Sequence[Sequence[int]]
    ) -> list[Markup]:
        block_ids = self.block_ids(block_ordered_ids)
        return [self.group_line(group_ids, block_ids) for group_ids in groups_ids]
