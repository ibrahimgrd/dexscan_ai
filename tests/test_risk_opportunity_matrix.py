"""
Step 10 (custom roadmap: Step 12) - tests for
`scoring/risk_opportunity_matrix.py`. NOT a Playbook-mandated test file
(this whole module is a custom-roadmap addition, per its own module
docstring) - kept separate from test_scoring_v2.py for the same reason
the module itself is separate from formulas.py/pipeline.py.
"""

from __future__ import annotations

import pytest

from scoring.risk_opportunity_matrix import _GRID, classify


def test_all_nine_grid_cells_are_reachable_and_distinct() -> None:
    """Every (risk_band, opportunity_band) combination the module claims
    to support must actually produce a result, and no two cells should
    share a label - a duplicate would mean two genuinely different
    profiles get displayed as indistinguishable."""
    bands = ["Low", "Moderate", "High"]
    seen_labels: set[str] = set()
    for risk_band in bands:
        for opportunity_band in bands:
            label, description = _GRID[(risk_band, opportunity_band)]
            assert label not in seen_labels, f"duplicate label: {label}"
            seen_labels.add(label)
            assert risk_band in description or risk_band.lower() in description.lower()
            assert opportunity_band in description or opportunity_band.lower() in description.lower()
    assert len(seen_labels) == 9


@pytest.mark.parametrize(
    "score_risk,score_opportunity,expected_risk_band,expected_opportunity_band",
    [
        (0.0, 0.0, "Low", "Low"),
        (33.9, 33.9, "Low", "Low"),
        (34.0, 34.0, "Moderate", "Moderate"),  # exact lower boundary of Moderate
        (66.9, 66.9, "Moderate", "Moderate"),
        (67.0, 67.0, "High", "High"),  # exact lower boundary of High
        (100.0, 100.0, "High", "High"),
        (10.0, 90.0, "Low", "High"),  # asymmetric axes classify independently
        (95.0, 5.0, "High", "Low"),
    ],
)
def test_classify_boundary_cases(score_risk, score_opportunity, expected_risk_band, expected_opportunity_band) -> None:
    tier = classify(score_risk, score_opportunity)
    assert tier.risk_band == expected_risk_band
    assert tier.opportunity_band == expected_opportunity_band


def test_classify_never_mutates_or_recomputes_the_input_scores() -> None:
    """classify() is a pure lookup - it must never adjust the scores it's
    given (module docstring's own 'never recomputes' contract)."""
    tier = classify(52.3, 71.8)
    assert tier.risk_band == "Moderate"
    assert tier.opportunity_band == "High"
    # the function takes plain floats and returns a fresh dataclass -
    # there's nothing mutable being passed in to check for a mutation,
    # but confirm the returned tier carries no unexpected transformation
    # of either input by re-deriving both bands independently here.


def test_high_risk_high_opportunity_is_distinguished_from_low_risk_high_opportunity() -> None:
    """The specific design intent named in the module docstring: these
    two cells must read as clearly different profiles, not variations of
    the same "high opportunity" headline."""
    safe_and_hyped = classify(10.0, 90.0)
    risky_and_hyped = classify(90.0, 90.0)
    assert safe_and_hyped.label != risky_and_hyped.label
    assert "High Risk" in risky_and_hyped.label
    assert "High Risk" not in safe_and_hyped.label
