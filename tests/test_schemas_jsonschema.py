"""Validates schema/*.json with jsonschema against the artifacts the code generates.

The hand-written linters (lint_contract, lint_plan, lint_card ...) are canonical: when a schema diverges, the
schema is fixed and the case is recorded here. jsonschema is a test-only dependency (never imported by the runtime).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

try:
    import jsonschema
except ImportError:  # optional dependency
    if os.environ.get("ADA_REQUIRE_OPTIONAL") == "1":
        raise
    pytest.skip("jsonschema não instalado", allow_module_level=True)

from script.card_lint import lint_card
from script.evidence import write_manifest
from script.plan_lint import lint_feature_plan, lint_plan
from script.project_config import load_config
from script.feature_contract import lint_contract
from script.sdd import approve_document
from script.stage_runner import CommandResult
from tests.test_card_lint import valid_card
from tests.test_plan_lint import valid_plan
from tests.test_run_feature import FEATURE, RUN, git, project, set_modes, to_contract  # noqa: F401
from tests.test_visual_review import visual_plan

pytestmark = pytest.mark.schema_real

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"
SCHEMAS = sorted(SCHEMA_DIR.glob("*.schema.json"))


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def errors_for(name: str, instance: object) -> list[str]:
    validator = jsonschema.Draft202012Validator(load_schema(name))
    return [error.message for error in validator.iter_errors(instance)]


def test_there_are_seven_schemas() -> None:
    assert len(SCHEMAS) == 7


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12(path: Path) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


def test_card_matches_schema_and_linter() -> None:
    card = valid_card()
    assert lint_card(card) == [] and errors_for("card", card) == []
    broken = copy.deepcopy(card)
    del broken["allowedPaths"]
    assert lint_card(broken) != [] and errors_for("card", broken) != []


def test_feature_plan_matches_schema_and_linter() -> None:
    plan = visual_plan()
    assert lint_feature_plan(plan) == [] and errors_for("plan", plan) == []
    broken = copy.deepcopy(plan)
    del broken["scenarios"]
    assert lint_feature_plan(broken) != [] and errors_for("plan", broken) != []


def test_state_matches_schema_and_linter() -> None:
    scenarios = [
        {"id": f"C{i}", "criterio": "A1", "camada": layer, "arquivo": "src/search.test.ts", "teste": "busca"}
        for i, layer in enumerate(("unit", "prop"), start=1)
    ]
    state = {**valid_plan(), "schemaVersion": 2, "scenarios": scenarios}
    assert lint_plan(state, strict=True) == [] and errors_for("state", state) == []
    broken = copy.deepcopy(state)
    del broken["sources"]
    assert lint_plan(broken, strict=True) != [] and errors_for("state", broken) != []


def test_evidence_manifest_matches_schema(tmp_path: Path) -> None:
    directory = tmp_path / "ev"
    directory.mkdir()
    output = directory / "out.txt"
    output.write_text("ok\n", encoding="utf-8")
    manifest = write_manifest(
        directory, gate="tests", status="PASS", exit_code=0, command=["true"], output_file=output
    )
    assert errors_for("evidence", manifest) == []
    on_disk = json.loads(next((tmp_path / "ev").glob("*.json")).read_text(encoding="utf-8"))
    assert errors_for("evidence", on_disk) == []
    assert errors_for("evidence", {**manifest, "exit_code": "0"}) != []
    assert errors_for("evidence", {k: v for k, v in manifest.items() if k != "output_sha256"}) != []


def test_engine_result_matches_schema() -> None:
    result = CommandResult(exit_code=0, output="feito", usage={"input_tokens": 3, "output_tokens": 4})
    payload: dict[str, object] = {"exit_code": result.exit_code, "output": result.output, "usage": result.usage}
    assert errors_for("engine-result", payload) == []
    assert errors_for("engine-result", {"exit_code": 0}) != []


def test_approval_matches_schema(tmp_path: Path) -> None:
    doc = tmp_path / "prd.md"
    doc.write_text("# PRD\n", encoding="utf-8")
    approval = approve_document(tmp_path / "prd.approval.json", doc, kind="prd", reviewer="Joel")
    assert errors_for("approval", approval) == []
    assert errors_for("approval", {**approval, "status": 1}) != []
    assert errors_for("approval", {**approval, "hash": "abc"}) != []


def test_feature_contract_matches_schema_and_linter(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, control = project
    set_modes(control, {"plan": "drafts", "refinement": "fail", "default": "ok", "root": str(repo)})
    to_contract(repo, capsys, control)
    contract = json.loads(
        git(repo, "show", f"harness/{FEATURE}/{RUN}/plan/2:docs/plano/{FEATURE}/contract.json")
    )
    config = load_config(repo, required=True)
    assert lint_contract(contract, config) == [] and errors_for("feature-contract", contract) == []
    extra = {**contract, "comando": "rm -rf /"}
    assert lint_contract(extra, config) != [] and errors_for("feature-contract", extra) != []
    missing = {k: v for k, v in contract.items() if k != "cardOrder"}
    assert lint_contract(missing, config) != [] and errors_for("feature-contract", missing) != []
