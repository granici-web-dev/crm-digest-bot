import re

from digest.reports.render import TEMPLATES_DIR

CYRILLIC = re.compile(r"[Ѐ-ӿ]")
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
YAML_COMMENT_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)


def test_templates_contain_no_cyrillic() -> None:
    # WHY-комментарии в шаблонах по-русски (CLAUDE.md «Язык»), в группу они не попадают.
    offending = {}
    for path in sorted(TEMPLATES_DIR.iterdir()):
        source = path.read_text(encoding="utf-8")
        without_comments = (
            JINJA_COMMENT.sub("", source)
            if path.suffix == ".j2"
            else YAML_COMMENT_LINE.sub("", source)
        )
        found = CYRILLIC.findall(without_comments)
        if found:
            offending[path.name] = "".join(found)

    assert offending == {}
