"""Política condicional de testes da Ada Harness 2.0."""

from __future__ import annotations

from typing import Final


TDD: Final = "tdd"
STYLE: Final = "dispensa-estilo"
LIBRARY: Final = "dispensa-biblioteca"
VALID_CLASSES: Final[frozenset[str]] = frozenset({TDD, STYLE, LIBRARY})


class PolicyValidationError(ValueError):
    pass


def classify_file(classification: str) -> str:
    if not isinstance(classification, str) or classification not in VALID_CLASSES:
        raise PolicyValidationError(f"classificação inválida: {classification}")
    return classification


def resolve_gates(
    *,
    classifications: dict[str, str],
    domain_sources: list[str] | None = None,
    e2e_criteria: list[str] | None = None,
    mutation_criteria: bool | None = None,
    visual_review: bool = True,
    second_opinion_required: bool = False,
) -> dict[str, bool]:
    """Resolve gates aplicáveis pelo tipo de arquivo e domínio."""
    if type(visual_review) is not bool or type(second_opinion_required) is not bool:
        raise PolicyValidationError("opções de revisão precisam ser booleanas")
    if mutation_criteria is not None and type(mutation_criteria) is not bool:
        raise PolicyValidationError("mutation_criteria precisa ser boolean")
    normalized = {path: classify_file(value) for path, value in classifications.items()}
    if any(not isinstance(path, str) or not path.strip() for path in normalized):
        raise PolicyValidationError("caminho classificado ausente")
    if domain_sources is not None and any(
        not isinstance(path, str) or path not in normalized for path in domain_sources
    ):
        raise PolicyValidationError("domain_sources precisa referir arquivos classificados")
    tdd = [path for path, value in normalized.items() if value == TDD]
    visual = [path for path, value in normalized.items() if value in {STYLE, LIBRARY}]

    return {
        "expect_red": bool(tdd),
        "tests_reviewed": bool(tdd),
        "coverage": bool(tdd),
        "mutation": bool(domain_sources) and (
            mutation_criteria if mutation_criteria is not None else True
        ),
        "e2e": bool(e2e_criteria),
        "visual_review": bool(visual) and visual_review,
        "second_opinion": second_opinion_required,
        "feature": bool(normalized),
    }


def validate_visual_extraction(
    classifications: dict[str, str],
    domain_markers: set[str] | None = None,
) -> list[str]:
    """Impede TDD no layout e exige extração se houver marcadores de domínio."""
    errors: list[str] = []
    markers = domain_markers or set()
    normalized = {path: classify_file(value) for path, value in classifications.items()}

    for path, classification in normalized.items():
        if classification == TDD:
            continue
        if classification in {STYLE, LIBRARY} and path in markers:
            errors.append(
                f"{path}: contém domínio; extraia regra, cálculo ou validação para arquivo tdd"
            )

    return errors


def stage_policy(gates: dict[str, bool]) -> dict[str, bool]:
    """Mapeia gates de teste aos estágios que só existem para executá-los."""
    return {
        "tests": gates.get("expect_red", False),
        "testReview": gates.get("tests_reviewed", False),
        "mutation_e2e": gates.get("mutation", False) or gates.get("e2e", False),
    }


def resolve_feature_gates(
    plan: dict[str, object],
    *,
    visual_review: bool = True,
    second_opinion_required: bool = False,
) -> dict[str, bool]:
    sources = plan.get("sources")
    criteria = plan.get("criteria")
    if not isinstance(sources, list) or not isinstance(criteria, list):
        raise PolicyValidationError("plano precisa declarar sources e criteria")

    classifications: dict[str, str] = {}
    domain_sources: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            raise PolicyValidationError("source precisa ser mapa")
        path, classification = source.get("file"), source.get("classification")
        if not isinstance(path, str) or not path.strip() or not isinstance(classification, str):
            raise PolicyValidationError("source precisa declarar file e classification")
        classifications[path] = classify_file(classification)
        if source.get("domain") is True:
            domain_sources.append(path)
        elif source.get("domain", False) is not False:
            raise PolicyValidationError(f"{path}: domain precisa ser boolean")

    e2e_criteria: list[str] = []
    mutation_required = False
    for criterion in criteria:
        if not isinstance(criterion, dict):
            raise PolicyValidationError("criterion precisa ser mapa")
        criterion_id, layers = criterion.get("id"), criterion.get("layers")
        if not isinstance(criterion_id, str) or not isinstance(layers, list):
            raise PolicyValidationError("criterion precisa declarar id e layers")
        if "e2e" in layers:
            e2e_criteria.append(criterion_id)
        if "mut" in layers:
            mutation_required = True

    return resolve_gates(
        classifications=classifications,
        domain_sources=domain_sources,
        e2e_criteria=e2e_criteria,
        mutation_criteria=mutation_required,
        visual_review=visual_review,
        second_opinion_required=second_opinion_required,
    )
