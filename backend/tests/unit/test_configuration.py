"""Schema validation and rule-fingerprint behavior for Task 4.7."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError
from reconbench_app.configuration import ConfigurationBody, canonical_hash
from reconbench_app.db.seed_data import DEFAULT_CONFIG
from reconbench_core.recon.registry import rules_hash_for_engine


def test_seeded_configuration_satisfies_complete_schema_contract() -> None:
    body = ConfigurationBody.model_validate(DEFAULT_CONFIG)
    assert body.engine.tolerances.limit_mode == "or"
    assert body.app.security.login_email_max_failures == 10


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("engine", "tolerances", "limit_mode"), "loose"),
        (("engine", "tolerances", "unit_rate", "pct"), "not-a-decimal"),
        (("engine", "thresholds", "extraction_gate"), 0.75),
        (("app", "ui", "poll_seconds"), "2"),
    ],
)
def test_configuration_rejects_invalid_contract_values(
    path: tuple[str, ...], value: object
) -> None:
    body = deepcopy(DEFAULT_CONFIG)
    target = body
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        ConfigurationBody.model_validate(body)


def test_rules_hash_includes_engine_but_excludes_app_settings() -> None:
    original = deepcopy(DEFAULT_CONFIG)
    app_changed = deepcopy(original)
    app_changed["app"]["ui"]["poll_seconds"] = 5
    engine_changed = deepcopy(original)
    engine_changed["engine"]["thresholds"]["extraction_gate"] = "0.75"

    baseline_hash = rules_hash_for_engine(original["engine"])
    assert rules_hash_for_engine(app_changed["engine"]) == baseline_hash
    assert rules_hash_for_engine(engine_changed["engine"]) != baseline_hash
    assert canonical_hash(original) != canonical_hash(app_changed)


def test_step3_weight_changes_are_part_of_rules_hash() -> None:
    original = deepcopy(DEFAULT_CONFIG["engine"])
    changed = deepcopy(original)
    changed["weights"]["step3"]["line_coverage"] = "0.39"
    changed["weights"]["step3"]["qty_feasible"] = "0.21"

    assert rules_hash_for_engine(changed) != rules_hash_for_engine(original)
