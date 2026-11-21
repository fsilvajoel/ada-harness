"""Molde `harness.project.example.yaml`: precisa ser YAML válido e carregável pelo parser do harness."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.feature_contract import load_contract_config
from script.pipeline import STAGES
from script.project_config import parse_config_text, parse_simple_yaml
from script.visual_review import load_visual_config

EXAMPLE = ROOT / "harness.project.example.yaml"
FEATURE = "E1-F01"


def _example_text() -> str:
    return EXAMPLE.read_text(encoding="utf-8")


def test_example_is_valid_yaml_mapping() -> None:
    loaded: object = yaml.safe_load(_example_text())
    assert isinstance(loaded, dict)


def test_simple_reader_yields_same_top_level_keys() -> None:
    loaded: object = yaml.safe_load(_example_text())
    assert isinstance(loaded, dict)
    simple = parse_simple_yaml(_example_text())
    assert set(simple) == {str(key) for key in loaded}


def test_project_parser_loads_example() -> None:
    config = parse_config_text(_example_text())
    assert "visual_review" in config
    assert "feature_contract" in config


def test_visual_review_section_loads() -> None:
    config = parse_config_text(_example_text())
    visual = load_visual_config(config)
    assert visual.command
    assert visual.timeout_s == 120


def test_feature_contract_loads_all_stages_in_order() -> None:
    config = parse_config_text(_example_text())
    contract = load_contract_config(config, FEATURE)
    assert tuple(contract.stages) == tuple(STAGES[1:])
    refinement = contract.stages["refinement"]
    assert refinement.allowed_paths == (f"docs/plano/{FEATURE}/refinement.md",)
