"""Synthetic generator — reproducibility, coverage, and detector-expectation agreement."""

from __future__ import annotations

import pytest

from revguard.detection import DetectionEngine
from revguard.domain import RiskLevel, WorkflowType
from revguard.synthetic import DEFAULT_SEED, Scenario, build_catalog, generate_events


def test_catalog_is_reproducible_for_a_seed():
    a = build_catalog(seed=123)
    b = build_catalog(seed=123)
    # Same seed -> identical event ids, amounts, timestamps.
    assert [e.model_dump() for s in a for e in s.events] == [
        e.model_dump() for s in b for e in s.events
    ]


def test_different_seed_changes_amounts():
    a = generate_events(seed=1)
    b = generate_events(seed=2)
    amounts_a = [e.amount for e in a if e.amount is not None]
    amounts_b = [e.amount for e in b if e.amount is not None]
    assert amounts_a != amounts_b


def test_generate_events_matches_catalog_total():
    catalog = build_catalog()
    flat = generate_events()
    assert len(flat) == sum(len(s.events) for s in catalog)


def test_catalog_covers_all_workflows_and_both_outcomes():
    catalog = build_catalog()
    workflows = {s.workflow for s in catalog}
    assert workflows == set(WorkflowType)

    # Each workflow has at least one positive and one no-signal scenario.
    for wf in WorkflowType:
        wf_scenarios = [s for s in catalog if s.workflow == wf]
        assert any(s.expects_signal for s in wf_scenarios)
        assert any(not s.expects_signal for s in wf_scenarios)


def test_catalog_exercises_multiple_severities():
    levels = {
        s.expected_risk_level for s in build_catalog() if s.expected_risk_level is not None
    }
    # Normal, clear-risk and boundary scenarios together span the full severity scale.
    assert levels == set(RiskLevel)


def test_scenario_names_are_unique():
    names = [s.name for s in build_catalog()]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("scenario", build_catalog(), ids=lambda s: s.name)
def test_detectors_agree_with_scenario_expectations(scenario: Scenario):
    """Running the engine over each scenario reproduces its declared expectation."""
    signals = DetectionEngine().run(scenario.events)

    if not scenario.expects_signal:
        assert signals == [], f"{scenario.name} should produce no signal"
        return

    assert len(signals) == 1, f"{scenario.name} should produce exactly one signal"
    signal = signals[0]
    assert signal.signal_type == scenario.workflow
    assert signal.risk_level == scenario.expected_risk_level
    assert signal.amount_at_risk == scenario.expected_amount_at_risk


def test_default_seed_is_stable():
    # Guard against accidental changes to the shipped default dataset shape.
    catalog = build_catalog(DEFAULT_SEED)
    assert len(catalog) == 27
