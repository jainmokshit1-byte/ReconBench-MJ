"""Stored-role capabilities (accuracy plan WI-PV3, O-18): only ``ai.manage`` is enforced."""

from __future__ import annotations

from reconbench_app.authorization import (
    CAPABILITY_ORDER,
    STORED_ROLE_CAPABILITIES,
    resolve_effective_role,
    stored_role_grants,
    visible_capabilities,
)


def test_only_ai_manage_is_enforced_from_the_stored_role() -> None:
    assert frozenset({"ai.manage"}) == STORED_ROLE_CAPABILITIES
    assert stored_role_grants("manager", "ai.manage")
    assert not stored_role_grants("ap_executive", "ai.manage")
    assert not stored_role_grants("auditor", "ai.manage")
    assert not stored_role_grants(None, "ai.manage")
    assert not stored_role_grants("owner", "ai.manage")  # unknown role grants nothing enforced


def test_every_other_capability_keeps_the_p0_policy() -> None:
    for role in ("manager", "ap_executive", "auditor", None, "unknown"):
        assert resolve_effective_role(role) == "manager"
        for capability in CAPABILITY_ORDER:
            if capability != "ai.manage":
                assert stored_role_grants(role, capability), (role, capability)


def test_visible_capabilities_drop_ai_manage_for_a_non_manager_only() -> None:
    everything = tuple(CAPABILITY_ORDER)
    assert visible_capabilities("manager") == everything
    assert visible_capabilities(None) == everything  # no stored role known: P0 list
    for role in ("ap_executive", "auditor"):
        assert visible_capabilities(role) == tuple(c for c in everything if c != "ai.manage")
