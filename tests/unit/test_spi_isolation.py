import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "digest"
SPI_MODULE = SOURCE_ROOT / "metrics" / "spi.py"


def imported_modules(source: str, package: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.rsplit(".", node.level - 1)[0] if node.level else ""
            if node.module:
                base = f"{base}.{node.module}" if base else node.module
            modules.add(base)
            modules.update(f"{base}.{alias.name}" for alias in node.names)
    return modules


def package_of(path: Path) -> str:
    return ".".join(path.relative_to(SOURCE_ROOT.parent).parent.parts)


# ADR-002: баллы и SPI предварительные; отчёты MVP и чат не должны до них дотянуться.
def test_nothing_in_src_imports_provisional_spi() -> None:
    importers = sorted(
        str(path.relative_to(SOURCE_ROOT))
        for path in SOURCE_ROOT.rglob("*.py")
        if path != SPI_MODULE
        and "digest.metrics.spi"
        in imported_modules(path.read_text(encoding="utf-8"), package_of(path))
    )

    assert importers == []


@pytest.mark.parametrize(
    "source",
    [
        "import digest.metrics.spi",
        "from digest.metrics import spi",
        "from digest.metrics.spi import spi_level",
        "from .spi import spi_level",
        "from . import spi",
        "from ..metrics.spi import spi_level",
    ],
)
def test_import_scan_detects_every_spi_import_form(source: str) -> None:
    assert "digest.metrics.spi" in imported_modules(source, "digest.metrics")
