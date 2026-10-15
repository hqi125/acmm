from pathlib import Path

import pytest

from acmm.contract.loader import contract_load_error, load_contract


def test_load_contract_reads_yaml_mapping(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text("schema_version: \"0.1\"\n", encoding="utf-8")

    contract = load_contract(contract_path)

    assert contract["schema_version"] == "0.1"


def test_load_contract_rejects_non_mapping(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(contract_load_error):
        load_contract(contract_path)


def test_load_contract_wraps_yaml_parse_error(tmp_path: Path) -> None:
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text("broken: [\n", encoding="utf-8")

    with pytest.raises(contract_load_error):
        load_contract(contract_path)
