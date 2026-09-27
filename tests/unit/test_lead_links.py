import re

from digest.config import AppConfig
from digest.reports.lead_links import LeadLinks
from factories import make_lead_links

LINK = re.compile(r'<a href="([^"]+)">([^<]*)</a>')


def test_url_is_web_origin_of_api_base_plus_configured_path(app_config: AppConfig) -> None:
    links = LeadLinks.from_mefi_base_url(
        "https://x.meficrm.com/api/v1", app_config.status_mapping.lead_links
    )

    assert links.url(1234) == "https://x.meficrm.com/admin/leads/index/1234"


def test_link_text_is_only_hash_and_number(app_config: AppConfig) -> None:
    line = make_lead_links(app_config.status_mapping).capped_line([1234, 7, 99])

    texts = [text for _, text in LINK.findall(line)]
    assert texts == ["#1234", "#7", "#99"]
    assert LINK.sub("", line) == ", , "


def test_more_than_limit_ids_end_with_si_inca_n(app_config: AppConfig) -> None:
    links = make_lead_links(app_config.status_mapping)

    thirteen = links.capped_line(list(range(1, 14)))
    ten = links.capped_line(list(range(1, 11)))

    assert len(LINK.findall(thirteen)) == 10
    assert thirteen.endswith("</a> și încă 3")
    assert len(LINK.findall(ten)) == 10
    assert "și încă" not in ten
    assert links.capped_line([]) == ""


def test_block_limit_goes_to_oldest_leads_of_the_whole_block(app_config: AppConfig) -> None:
    links = make_lead_links(app_config.status_mapping)
    oldest_group = list(range(1, 13))
    younger_group = [20, 21]

    lines = links.block_lines([*oldest_group, *younger_group], [younger_group, oldest_group])

    assert lines[0] == ""
    assert [text for _, text in LINK.findall(lines[1])] == [f"#{n}" for n in range(1, 11)]
    assert lines[1].endswith("și încă 2")
