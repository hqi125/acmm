from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {".json", ".md", ".py", ".yaml", ".yml"}
MANAGED_ROOTS = ("src", "tests", "infrastructure", "docs", "tools")


def test_repository_managed_text_files_use_crlf() -> None:
    offenders: list[str] = []

    for root_name in MANAGED_ROOTS:
        root = ROOT / root_name
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            data = path.read_bytes()
            if b"\n" not in data:
                continue
            if b"\n" in data.replace(b"\r\n", b""):
                offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, "LF-only/mixed line endings: " + ", ".join(offenders)
