# SPDX-License-Identifier: MIT
"""Tests for the ratio-CI consistency check, 2x2 recomputation and evidence-wide check."""

from __future__ import annotations

import math

import pytest
from citeproof_mcp import store
from citeproof_mcp.tools import stats_checks

from tests.conftest import call

SOURCE = (
    "Methods: 240 adults were randomised. Results: all-cause mortality was lower with drug X "
    "(RR 0.82, 95% CI 0.70-0.96; n = 240). Blood pressure fell by a mean difference of −5.1 mmHg "
    "(95% CI −7.0 to −3.2)."
)
Z95 = 1.9599639845400536


async def _review() -> str:
    return (await call("review_create", title="Stats checks", question="Do the numbers agree?"))["review_id"]


# --- ratio CI consistency -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ratio_ci_accepts_a_consistent_published_interval() -> None:
    res = await call(
        "stats_check_ratio_ci", measure="risk ratio", estimate=0.82, ci_lower=0.70, ci_upper=0.96
    )
    assert res["ok"] is True
    assert res["status"] == "consistent"
    assert res["measure"] == "rr"
    assert res["saved"] is False and res["check_id"] is None
    assert res["geometric_midpoint"] == pytest.approx(math.sqrt(0.70 * 0.96), rel=1e-4)
    assert res["z"] == pytest.approx(Z95, abs=1e-5)
    assert res["se_log"] == pytest.approx(
        (math.log(0.96) - math.log(0.70)) / (2 * Z95), rel=1e-4
    )
    assert res["p_back_calculated"] is not None
    assert res["p_reported"] is None
    assert all(check["passed"] for check in res["checks"])
    assert res["assumptions"] and res["warnings"] == []


@pytest.mark.asyncio
async def test_ratio_ci_accepts_the_reference_interval() -> None:
    res = await call(
        "stats_check_ratio_ci", measure="rr", estimate=0.5, ci_lower=0.2466, ci_upper=1.0137
    )
    assert res["status"] == "consistent", res
    assert res["geometric_midpoint"] == pytest.approx(0.5, abs=0.001)


@pytest.mark.asyncio
async def test_ratio_ci_flags_shifted_limits() -> None:
    res = await call(
        "stats_check_ratio_ci", measure="rr", estimate=0.82, ci_lower=0.60, ci_upper=0.96
    )
    assert res["ok"] is True
    assert res["status"] == "inconsistent"
    assert any(
        "inconsistent with the reported interval under the stated rounding" in warning
        for warning in res["warnings"]
    )
    midpoint = next(check for check in res["checks"] if check["check"] == "geometric_midpoint")
    assert midpoint["passed"] is False
    assert "0.82" in midpoint["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"estimate": 0.0, "ci_lower": 0.7, "ci_upper": 0.96}, "non-positive"),
        ({"estimate": 0.82, "ci_lower": 0.0, "ci_upper": 0.96}, "non-positive"),
        ({"estimate": 0.82, "ci_lower": -0.1, "ci_upper": 0.96}, "non-positive"),
        ({"estimate": 0.82, "ci_lower": 0.96, "ci_upper": 0.60}, "ascending"),
        ({"estimate": 1.5, "ci_lower": 0.7, "ci_upper": 0.96}, "outside"),
        ({"estimate": 0.82, "ci_lower": 0.70, "ci_upper": 0.96, "p_value": 1.5}, "p_value"),
    ],
)
async def test_ratio_ci_marks_invalid_numbers_without_raising(args, needle) -> None:
    res = await call("stats_check_ratio_ci", measure="rr", **args)
    assert res["ok"] is True, res
    assert res["status"] == "invalid"
    assert any(needle in warning for warning in res["warnings"]), res["warnings"]
    assert res["saved"] is False


@pytest.mark.asyncio
async def test_ratio_ci_p_value_back_calculation_passes_and_flags() -> None:
    first = await call(
        "stats_check_ratio_ci", measure="rr", estimate=0.82, ci_lower=0.70, ci_upper=0.96
    )
    consistent = await call(
        "stats_check_ratio_ci",
        measure="rr",
        estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        p_value=first["p_back_calculated"],
        decimals=6,
    )
    assert consistent["status"] == "consistent", consistent
    assert any(check["check"] == "p_value" and check["passed"] for check in consistent["checks"])

    wrong = await call(
        "stats_check_ratio_ci",
        measure="rr",
        estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        p_value=0.5,
    )
    assert wrong["status"] == "inconsistent"
    assert any("p-value" in warning for warning in wrong["warnings"])


@pytest.mark.asyncio
async def test_ratio_ci_accepts_aliases_and_rejects_difference_measures() -> None:
    odds = await call(
        "stats_check_ratio_ci", measure="odds_ratio", estimate=0.4444, ci_lower=0.1965, ci_upper=1.0052
    )
    assert odds["ok"] is True and odds["measure"] == "or"

    bad = await call(
        "stats_check_ratio_ci", measure="md", estimate=1.0, ci_lower=0.5, ci_upper=1.5
    )
    assert bad["ok"] is False
    assert "rr" in bad["error"] and "rom" in bad["error"]


@pytest.mark.asyncio
async def test_ratio_ci_rejects_non_finite_numbers() -> None:
    res = await call(
        "stats_check_ratio_ci", measure="rr", estimate="inf", ci_lower=0.7, ci_upper=0.96
    )
    assert res["ok"] is False
    assert "finite" in res["error"]


@pytest.mark.asyncio
async def test_ratio_ci_saves_and_reads_back() -> None:
    rid = await _review()
    res = await call(
        "stats_check_ratio_ci",
        measure="rr",
        estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        review_id=rid,
        paper_ref="https://doi.org/10.1000/ABC",
        label="primary mortality",
    )
    assert res["ok"] is True and res["saved"] is True
    assert isinstance(res["check_id"], int)

    with store.review_db(rid) as conn:
        rows = stats_checks.stats_check_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "ratio_ci"
    assert row["paper_ref"] == "10.1000/abc"
    assert row["label"] == "primary mortality"
    assert row["status"] == "consistent"
    assert row["inputs"]["estimate"] == 0.82
    assert row["result"]["status"] == "consistent"
    assert row["checked_at"]


@pytest.mark.asyncio
async def test_ratio_ci_save_input_is_validated_before_writing() -> None:
    rid = await _review()
    too_long = await call(
        "stats_check_ratio_ci",
        measure="rr",
        estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        review_id=rid,
        label="x" * 201,
    )
    assert too_long["ok"] is False and "label" in too_long["error"]

    bad_ref = await call(
        "stats_check_ratio_ci",
        measure="rr",
        estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        review_id=rid,
        paper_ref="not a paper",
    )
    assert bad_ref["ok"] is False and "paper_ref" in bad_ref["error"]

    missing = await call(
        "stats_check_ratio_ci",
        measure="rr",
        estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        review_id="rev-missing1",
    )
    assert missing["ok"] is False
    with store.review_db(rid) as conn:
        assert stats_checks.stats_check_rows(conn) == []


# --- 2x2 recomputation ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_2x2_reference_values() -> None:
    res = await call(
        "stats_recompute_2x2", events_a=10, total_a=100, events_b=20, total_b=100
    )
    assert res["ok"] is True
    assert res["status"] == "recomputed"
    assert res["correction_applied"] is False

    rr = res["rr"]
    assert rr["estimable"] is True
    assert rr["estimate"] == pytest.approx(0.5, rel=1e-6)
    assert rr["ci_lower"] == pytest.approx(0.2466, abs=1e-3)
    assert rr["ci_upper"] == pytest.approx(1.0137, abs=1e-3)
    assert rr["se"] == pytest.approx(math.sqrt(0.13), rel=1e-6)

    odds = res["or"]
    assert odds["estimable"] is True
    assert odds["estimate"] == pytest.approx(0.4444, rel=1e-3)
    assert odds["ci_lower"] == pytest.approx(0.1965, abs=1e-3)
    assert odds["ci_upper"] == pytest.approx(1.0052, abs=2e-3)
    assert odds["se"] == pytest.approx(0.4167, rel=1e-3)

    rd = res["rd"]
    assert rd["estimate"] == pytest.approx(-0.10, abs=1e-9)
    assert rd["se"] == pytest.approx(math.sqrt(0.1 * 0.9 / 100 + 0.2 * 0.8 / 100), rel=1e-9)
    assert res["arm_a"]["risk"] == pytest.approx(0.1)
    assert res["arm_b"]["risk"] == pytest.approx(0.2)
    assert res["reported"] is None and res["comparison"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"events_a": 110, "total_a": 100, "events_b": 20, "total_b": 100}, "events_a"),
        ({"events_a": 10, "total_a": 100, "events_b": -1, "total_b": 100}, "events_b"),
        ({"events_a": 2.5, "total_a": 100, "events_b": 20, "total_b": 100}, "whole number"),
        ({"events_a": 10, "total_a": 0, "events_b": 20, "total_b": 100}, "total_a"),
        ({"events_a": True, "total_a": 100, "events_b": 20, "total_b": 100}, "whole number"),
        ({"events_a": 10, "total_a": "abc", "events_b": 20, "total_b": 100}, "whole number"),
    ],
)
async def test_recompute_2x2_rejects_impossible_counts(kwargs, needle) -> None:
    res = await call("stats_recompute_2x2", **kwargs)
    assert res["ok"] is False, res
    assert needle in res["error"], (kwargs, res["error"])


@pytest.mark.asyncio
async def test_recompute_2x2_zero_cell_without_correction() -> None:
    res = await call(
        "stats_recompute_2x2", events_a=0, total_a=100, events_b=20, total_b=100
    )
    assert res["rr"]["estimate"] is None and res["rr"]["estimable"] is False
    assert "zero events" in res["rr"]["reason"]
    assert res["or"]["estimate"] is None and res["or"]["estimable"] is False
    assert "zero cell" in res["or"]["reason"]
    assert res["rd"]["estimate"] == pytest.approx(-0.2)
    assert any("At least one cell is zero" in warning for warning in res["warnings"])


@pytest.mark.asyncio
async def test_recompute_2x2_zero_cell_with_correction_is_explicit() -> None:
    res = await call(
        "stats_recompute_2x2",
        events_a=0,
        total_a=100,
        events_b=20,
        total_b=100,
        continuity_correction="add_0.5_if_zero",
    )
    assert res["correction_applied"] is True
    assert res["continuity_correction"] == "add_0.5_if_zero"
    assert res["rr"]["estimate"] == pytest.approx(0.5 / 20.5, rel=1e-5)
    assert res["or"]["estimate"] == pytest.approx((0.5 * 80.5) / (20.5 * 100.5), rel=1e-5)
    assert res["rd"]["estimate"] == pytest.approx(-0.2)  # RD always uses the raw counts
    assert res["cells_used_for_ratios"]["events_a"] == 0.5
    assert res["cells_used_for_ratios"]["non_events_a"] == 100.5
    assert any("Continuity correction add_0.5_if_zero applied" in warning for warning in res["warnings"])


@pytest.mark.asyncio
async def test_recompute_2x2_double_zero_is_not_estimable_anywhere() -> None:
    for policy in ("none", "add_0.5_if_zero"):
        res = await call(
            "stats_recompute_2x2",
            events_a=0,
            total_a=100,
            events_b=0,
            total_b=100,
            continuity_correction=policy,
        )
        assert res["rr"]["estimate"] is None and res["rr"]["estimable"] is False
        assert res["or"]["estimate"] is None and res["or"]["estimable"] is False
        assert "both arms have zero events" in res["rr"]["reason"]
        assert res["rd"]["estimate"] == 0.0
        assert res["rd"]["ci_lower"] == 0.0 and res["rd"]["ci_upper"] == 0.0
        assert res["status"] == "not_estimable"


@pytest.mark.asyncio
async def test_recompute_2x2_or_needs_non_event_cells() -> None:
    res = await call(
        "stats_recompute_2x2", events_a=100, total_a=100, events_b=20, total_b=100
    )
    assert res["rr"]["estimable"] is True
    assert res["or"]["estimate"] is None
    assert "non-events in arm A" in res["or"]["reason"]


@pytest.mark.asyncio
async def test_recompute_2x2_compares_reported_values() -> None:
    good = await call(
        "stats_recompute_2x2",
        events_a=10,
        total_a=100,
        events_b=20,
        total_b=100,
        reported={"measure": "rr", "estimate": 0.5, "ci_lower": 0.2466, "ci_upper": 1.0137},
    )
    assert good["status"] == "consistent"
    assert good["comparison"]["matches"] is True
    assert good["comparison"]["mismatches"] == []
    assert len(good["comparison"]["values"]) == 3
    assert all(value["matches"] for value in good["comparison"]["values"])

    bad = await call(
        "stats_recompute_2x2",
        events_a=10,
        total_a=100,
        events_b=20,
        total_b=100,
        reported={"measure": "risk ratio", "estimate": 0.4, "ci_lower": 0.2, "ci_upper": 0.9},
    )
    assert bad["status"] == "inconsistent"
    assert bad["comparison"]["matches"] is False
    assert "estimate" in bad["comparison"]["mismatches"]
    estimate_row = next(v for v in bad["comparison"]["values"] if v["value"] == "estimate")
    assert estimate_row["difference"] == pytest.approx(-0.1)
    assert any("inconsistent with the recomputed value" in warning for warning in bad["warnings"])


@pytest.mark.asyncio
async def test_recompute_2x2_reported_not_estimable_is_reported() -> None:
    res = await call(
        "stats_recompute_2x2",
        events_a=0,
        total_a=100,
        events_b=20,
        total_b=100,
        reported={"measure": "rr", "estimate": 0.5},
    )
    assert res["status"] == "inconsistent"
    assert res["comparison"]["estimable"] is False
    assert "not estimable" in res["comparison"]["note"]
    assert any("cannot be compared" in warning for warning in res["warnings"])


@pytest.mark.asyncio
async def test_recompute_2x2_rejects_bad_reported_object() -> None:
    bad_type = await call(
        "stats_recompute_2x2", events_a=10, total_a=100, events_b=20, total_b=100, reported="rr 0.5"
    )
    assert bad_type["ok"] is False and "reported" in bad_type["error"]

    bad_limits = await call(
        "stats_recompute_2x2",
        events_a=10,
        total_a=100,
        events_b=20,
        total_b=100,
        reported={"measure": "rr", "estimate": 0.5, "ci_lower": 1.0},
    )
    assert bad_limits["ok"] is False and "both" in bad_limits["error"]


@pytest.mark.asyncio
async def test_recompute_2x2_saves_and_reads_back() -> None:
    rid = await _review()
    res = await call(
        "stats_recompute_2x2",
        events_a=10,
        total_a=100,
        events_b=20,
        total_b=100,
        review_id=rid,
        paper_ref="pmid:123",
        label="trial arm counts",
    )
    assert res["saved"] is True and isinstance(res["check_id"], int)
    with store.review_db(rid) as conn:
        rows = stats_checks.stats_check_rows(conn)
    assert len(rows) == 1
    assert rows[0]["kind"] == "recompute_2x2"
    assert rows[0]["paper_ref"] == "pmid:123"
    assert rows[0]["status"] == "recomputed"
    assert rows[0]["inputs"]["events_a"] == 10
    assert rows[0]["result"]["rr"]["estimate"] == 0.5


# --- evidence-wide check --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_check_evidence_checks_ratio_rows_and_skips_others() -> None:
    rid = await _review()
    await call(
        "evidence_record",
        review_id=rid,
        paper_ref="10.1000/trial",
        outcome="All-cause mortality",
        study_design="RCT",
        effect_measure="rr",
        effect_estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        quote="all-cause mortality was lower with drug X (RR 0.82, 95% CI 0.70-0.96; n = 240)",
        source_text=SOURCE,
    )
    await call(
        "evidence_record",
        review_id=rid,
        paper_ref="10.1000/trial",
        outcome="Systolic blood pressure",
        study_design="RCT",
        effect_measure="md",
        effect_estimate=-5.1,
        ci_lower=-7.0,
        ci_upper=-3.2,
        quote="a mean difference of −5.1 mmHg (95% CI −7.0 to −3.2)",
        source_text=SOURCE,
    )

    res = await call("stats_check_evidence", review_id=rid)
    assert res["ok"] is True
    assert res["checked"] == 1
    assert res["skipped"] == 1
    assert res["counts"] == {"consistent": 1, "inconsistent": 0, "invalid": 0, "skipped": 1}
    assert res["truncated"] is False and len(res["rows"]) == 2
    by_outcome = {row["outcome"]: row for row in res["rows"]}
    assert by_outcome["All-cause mortality"]["status"] == "consistent"
    assert by_outcome["Systolic blood pressure"]["status"] == "skipped"
    assert "Not a ratio measure" in by_outcome["Systolic blood pressure"]["warnings"][0]
    assert res["note"]


@pytest.mark.asyncio
async def test_stats_check_evidence_flags_an_inconsistent_ratio_row() -> None:
    rid = await _review()
    quote = "Mortality was lower with drug X (RR 0.82, 95% CI 0.60-0.96)."
    await call(
        "evidence_record",
        review_id=rid,
        paper_ref="10.1000/trial",
        outcome="Mortality",
        study_design="RCT",
        effect_measure="rr",
        effect_estimate=0.82,
        ci_lower=0.60,
        ci_upper=0.96,
        quote=quote,
        source_text=quote,
    )
    res = await call("stats_check_evidence", review_id=rid)
    assert res["checked"] == 1 and res["counts"]["inconsistent"] == 1
    assert res["rows"][0]["status"] == "inconsistent"
    assert any(
        "inconsistent with the reported interval under the stated rounding" in warning
        for warning in res["rows"][0]["warnings"]
    )


@pytest.mark.asyncio
async def test_stats_check_evidence_skips_ratio_rows_without_limits() -> None:
    rid = await _review()
    await call(
        "evidence_record",
        review_id=rid,
        paper_ref="10.1000/trial",
        outcome="Mortality",
        study_design="RCT",
        effect_measure="or",
        effect_estimate=1.2,
        quote="mortality was lower with drug X",
        source_text=SOURCE,
    )
    res = await call("stats_check_evidence", review_id=rid)
    assert res["checked"] == 0 and res["skipped"] == 1
    assert res["counts"]["skipped"] == 1
    assert "ci_lower, ci_upper" in res["rows"][0]["warnings"][0]


@pytest.mark.asyncio
async def test_stats_check_evidence_skips_missing_ci_level_without_guessing() -> None:
    rid = await _review()
    await call(
        "evidence_record",
        review_id=rid,
        paper_ref="10.1000/trial",
        outcome="Mortality",
        study_design="RCT",
        effect_measure="rr",
        effect_estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        quote="all-cause mortality was lower with drug X (RR 0.82, 95% CI 0.70-0.96; n = 240)",
        source_text=SOURCE,
    )
    with store.review_db(rid, write=True) as conn:
        conn.execute("UPDATE evidence_rows SET ci_level = NULL")

    res = await call("stats_check_evidence", review_id=rid)
    assert res["checked"] == 0 and res["skipped"] == 1
    assert "not guessed" in res["rows"][0]["warnings"][0]


@pytest.mark.asyncio
async def test_stats_check_evidence_empty_review_and_bad_input() -> None:
    rid = await _review()
    res = await call("stats_check_evidence", review_id=rid)
    assert res["ok"] is True
    assert res["rows"] == [] and res["counts"]["consistent"] == 0

    assert (await call("stats_check_evidence", review_id=rid, decimals=9))["ok"] is False
    assert (await call("stats_check_evidence", review_id=rid, rel_tol=-0.1))["ok"] is False
    assert (await call("stats_check_evidence", review_id="rev-missing1"))["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [0.95, 50, 100, 150])
async def test_ci_level_is_a_percentage_like_evidence_record(level) -> None:
    ratio = await call("stats_check_ratio_ci", measure="rr", estimate=0.82, ci_lower=0.70, ci_upper=0.96, ci_level=level)
    counts = await call("stats_recompute_2x2", events_a=10, total_a=100, events_b=20, total_b=100, ci_level=level)
    assert ratio["ok"] is False and "percentage" in ratio["error"]
    assert counts["ok"] is False and "percentage" in counts["error"]
