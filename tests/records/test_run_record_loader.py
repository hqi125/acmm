from pathlib import Path

import pytest

from acmm.record.loader import load_run_record, run_record_load_error


def test_load_run_record_reads_yaml_mapping(tmp_path: Path) -> None:
    record_path = tmp_path / "record.yaml"
    record_path.write_text(
        "schema_version: \"0.1\"\n",
        encoding="utf-8",
    )

    record = load_run_record(record_path)

    assert record["schema_version"] == "0.1"


def test_load_run_record_rejects_non_mapping(tmp_path: Path) -> None:
    record_path = tmp_path / "record.yaml"
    record_path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(run_record_load_error):
        load_run_record(record_path)


def test_load_run_record_wraps_yaml_parse_error(tmp_path: Path) -> None:
    record_path = tmp_path / "record.yaml"
    record_path.write_text("broken: [\n", encoding="utf-8")

    with pytest.raises(run_record_load_error):
        load_run_record(record_path)
