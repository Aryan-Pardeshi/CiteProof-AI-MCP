# SPDX-License-Identifier: MIT
"""Deterministic arithmetic checks for reported effect estimates.

Two checks are provided: whether a ratio estimate sits near the geometric
midpoint of its confidence interval on the log scale, and whether RR, OR and RD
recomputed from arm-level 2x2 counts agree with the values a paper reports.
Every number used is returned; nothing is inferred about the paper's analysis
beyond the stated assumptions.

Table owned here: ``stats_checks``.
"""

import json
import math
import sqlite3
from statistics import NormalDist
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import store
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import extraction
from citeproof_mcp.tools.common import (
    cap,
    choice_arg,
    fail,
    float_arg,
    int_arg,
    paper_ref_arg,
    safe_tool,
    text_arg,
)

store.register_schema(
    (
        """CREATE TABLE IF NOT EXISTS stats_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            paper_ref TEXT,
            label TEXT,
            inputs TEXT NOT NULL,
            result TEXT NOT NULL,
            status TEXT NOT NULL,
            checked_at TEXT NOT NULL
        )""",
    )
)

KIND_RATIO_CI = "ratio_ci"
KIND_RECOMPUTE = "recompute_2x2"
CORRECTION_POLICIES = ("none", "add_0.5_if_zero")
CORRECTION_ALIASES = {
    "add_0_5_if_zero": "add_0.5_if_zero",
    "add_05_if_zero": "add_0.5_if_zero",
    "haldane": "add_0.5_if_zero",
    "none": "none",
}
RECOMPUTE_MEASURES = ("rr", "or", "rd")
REPORTED_VALUE_LABELS = {"estimate": "estimate", "ci_lower": "lower limit", "ci_upper": "upper limit"}
STATUSES = ("consistent", "inconsistent", "invalid", "skipped")

# A lower limit can be rounded below zero even though the true ratio limit is positive.
MIN_POSITIVE = 1e-12

ReviewId = Annotated[str, Field(description="The id returned by review_create.")]
ReviewIdOpt = Annotated[
    str | None,
    Field(description="If given, the check is saved to this review; read it back with export_bundle or stats_check_rows."),
]
Decimals = Annotated[
    int,
    Field(description="Decimals the paper reported for the estimate and CI (0 to 6, default 2). Sets the half-unit rounding tolerance."),
]
RelTol = Annotated[
    float,
    Field(description="Extra relative tolerance on top of the half-unit rounding, default 0.01 (1%)."),
]
Label = Annotated[str | None, Field(description="Short label for this check, e.g. 'primary mortality analysis' (max 200 chars).")]


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def stats_check_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Saved checks, oldest first, with ``inputs`` and ``result`` parsed from JSON."""
    rows = []
    for row in conn.execute(
        "SELECT id, kind, paper_ref, label, inputs, result, status, checked_at FROM stats_checks ORDER BY id"
    ):
        record = dict(row)
        record["inputs"] = json.loads(record["inputs"])
        record["result"] = json.loads(record["result"])
        rows.append(record)
    return rows


# ---------------------------------------------------------------------------
# Shared arithmetic helpers
# ---------------------------------------------------------------------------


def _round6(value: float | None) -> float | None:
    """Six significant digits, so very small p-values do not collapse to zero."""
    if value is None or not math.isfinite(value) or value == 0:
        return value
    places = 5 - math.floor(math.log10(abs(value)))
    return round(value, max(-300, min(300, places)))


def _fmt(value: float | None) -> str:
    rounded = _round6(value)
    return "null" if rounded is None else f"{rounded:g}"


def _decimals_arg(value: Any) -> int:
    decimals = int_arg(value, "decimals", min_value=0, max_value=6)
    if decimals is None:
        fail("decimals is required; send a whole number between 0 and 6")
    return decimals


def _rel_tol_arg(value: Any) -> float:
    rel_tol = float_arg(value, "rel_tol")
    if rel_tol is None:
        fail("rel_tol is required; send a number between 0 and 1, e.g. 0.01")
    if not 0 <= rel_tol <= 1:
        fail("rel_tol must be between 0 and 1, e.g. 0.01 for a 1% relative tolerance")
    return rel_tol


def _level_arg(value: Any) -> float:
    """Confidence level in percent, as evidence_record takes it; returned as a fraction."""
    level = float_arg(value, "ci_level")
    if level is None or not 50 < level < 100:
        fail("ci_level must be a percentage between 50 and 100, e.g. 95")
    return level / 100


def _optional_ref(value: Any) -> str | None:
    return paper_ref_arg(value) if value is not None and str(value).strip() else None


def _optional_label(value: Any) -> str | None:
    return text_arg(value, "label", required=False, max_len=200)


def _save_check(
    conn: sqlite3.Connection,
    kind: str,
    paper_ref: str | None,
    label: str | None,
    inputs: dict[str, Any],
    result: dict[str, Any],
) -> int:
    cursor = conn.execute(
        "INSERT INTO stats_checks (kind, paper_ref, label, inputs, result, status, checked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            kind,
            paper_ref,
            label,
            json.dumps(inputs, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            result["status"],
            store.now_iso(),
        ),
    )
    return int(cursor.lastrowid)


# ---------------------------------------------------------------------------
# Ratio CI check (#87)
# ---------------------------------------------------------------------------


def _ratio_assumptions(decimals: int, rel_tol: float, level: float | None) -> list[str]:
    level_text = (
        f"z is the two-sided {level * 100:g}% normal deviate."
        if level is not None
        else "z is the two-sided normal deviate for each row's stored confidence level."
    )
    return [
        (
            "The interval is treated as a two-sided Wald interval on the log scale, so "
            "SE = (ln upper - ln lower) / (2 z) with z from the normal distribution."
        ),
        level_text,
        (
            "The back-calculated p-value is two-sided and assumes a normal log-scale estimate: "
            "p = erfc(|ln estimate| / (SE * sqrt(2))). A reported p passes when it lies in the range of p over "
            "every estimate and limit within half a unit of the reported values, widened by rel_tol."
        ),
        (
            f"Reported values are rounded to {decimals} decimals. A value is consistent when it differs from the "
            f"computed value by at most h + rel_tol * |computed|, with h = 0.5 * 10^-{decimals} and rel_tol = {rel_tol:g}."
        ),
        (
            "The midpoint check compares the estimate with the geometric-midpoint range "
            "sqrt((lower-h)(upper-h)) to sqrt((lower+h)(upper+h)), widened by rel_tol."
        ),
        "This checks internal arithmetic consistency only, not whether the paper's analysis, data or model are correct.",
    ]


def _p_range(estimate: float, lower: float, upper: float, h: float, z: float) -> tuple[float, float]:
    """Smallest and largest two-sided p for unrounded values within +-h of the reported ones.

    p falls as |ln estimate| grows and as SE shrinks, so the extremes are: the estimate
    furthest from 1 with the narrowest interval, and the estimate closest to 1 with the widest.
    """
    e_lo, e_hi = max(MIN_POSITIVE, estimate - h), estimate + h
    far = max(abs(math.log(e_lo)), abs(math.log(e_hi)))
    near = 0.0 if e_lo <= 1 <= e_hi else min(abs(math.log(e_lo)), abs(math.log(e_hi)))
    se_narrow = (math.log(upper - h) - math.log(lower + h)) / (2 * z) if upper - h > lower + h else 0.0
    se_wide = (math.log(upper + h) - math.log(max(MIN_POSITIVE, lower - h))) / (2 * z)
    p_low = math.erfc(far / se_narrow / math.sqrt(2)) if se_narrow > 0 else 0.0
    return p_low, math.erfc(near / se_wide / math.sqrt(2))


def _ratio_ci_result(
    measure: str,
    estimate: float,
    lower: float,
    upper: float,
    level: float,
    p_value: float | None,
    decimals: int,
    rel_tol: float,
) -> dict[str, Any]:
    h = 0.5 * 10.0 ** (-decimals)
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    structural_ok = True

    positive = estimate > 0 and lower > 0 and upper > 0
    checks.append(
        {
            "check": "positive_values",
            "passed": positive,
            "detail": (
                f"estimate={_fmt(estimate)}, ci_lower={_fmt(lower)}, ci_upper={_fmt(upper)}; "
                "a ratio measure requires all three to be greater than 0"
            ),
        }
    )
    if not positive:
        structural_ok = False
        warnings.append(
            "A non-positive value was supplied. This check applies only to ratio measures; if the paper "
            "reports a difference (RD, MD or SMD), use a difference check instead. Send the values as reported "
            "when the estimate is a ratio."
        )

    ordered = lower < upper
    checks.append(
        {
            "check": "ordered_limits",
            "passed": ordered,
            "detail": f"ci_lower={_fmt(lower)} must be less than ci_upper={_fmt(upper)}",
        }
    )
    if not ordered:
        structural_ok = False
        warnings.append("ci_lower is not less than ci_upper; send the confidence limits in ascending order.")

    inside = lower <= estimate <= upper
    checks.append(
        {
            "check": "estimate_within_interval",
            "passed": inside,
            "detail": f"estimate={_fmt(estimate)} must lie between ci_lower={_fmt(lower)} and ci_upper={_fmt(upper)}",
        }
    )
    if not inside:
        structural_ok = False
        warnings.append(
            f"estimate {_fmt(estimate)} lies outside the interval {_fmt(lower)} to {_fmt(upper)}; "
            "re-check which number is the estimate and which are the limits."
        )

    if p_value is not None:
        p_range_ok = 0 <= p_value <= 1
        checks.append(
            {
                "check": "p_value_range",
                "passed": p_range_ok,
                "detail": f"p_value={_fmt(p_value)} must be between 0 and 1",
            }
        )
        if not p_range_ok:
            structural_ok = False
            warnings.append("p_value must be between 0 and 1; send the two-sided p-value as a proportion.")

    result: dict[str, Any] = {
        "measure": measure,
        "estimate": _round6(estimate),
        "ci_lower": _round6(lower),
        "ci_upper": _round6(upper),
        "ci_level": _round6(level * 100),
        "decimals": decimals,
        "rel_tol": rel_tol,
        "half_unit": _round6(h),
        "status": "invalid",
        "checks": checks,
        "geometric_midpoint": None,
        "geometric_midpoint_range": None,
        "se_log": None,
        "z": None,
        "p_back_calculated": None,
        "p_reported": _round6(p_value),
        "assumptions": _ratio_assumptions(decimals, rel_tol, level),
        "warnings": warnings,
    }
    if not structural_ok:
        return result

    z = NormalDist().inv_cdf(1 - (1 - level) / 2)
    se_log = (math.log(upper) - math.log(lower)) / (2 * z)
    midpoint = math.sqrt(lower * upper)
    midpoint_low = math.sqrt(max(MIN_POSITIVE, lower - h) * max(MIN_POSITIVE, upper - h))
    midpoint_high = math.sqrt((lower + h) * (upper + h))
    widened_low = midpoint_low * (1 - rel_tol)
    widened_high = midpoint_high * (1 + rel_tol)
    midpoint_ok = (estimate + h) >= widened_low and (estimate - h) <= widened_high
    checks.append(
        {
            "check": "geometric_midpoint",
            "passed": midpoint_ok,
            "detail": (
                f"estimate {_fmt(estimate)} with half-unit {_fmt(h)} compared with the geometric-midpoint range "
                f"{_fmt(midpoint_low)} to {_fmt(midpoint_high)} widened by {rel_tol:g} "
                f"to {_fmt(widened_low)} to {_fmt(widened_high)}"
            ),
        }
    )
    if not midpoint_ok:
        warnings.append(
            f"The estimate is inconsistent with the reported interval under the stated rounding: "
            f"{_fmt(estimate)} is not near the geometric midpoint {_fmt(midpoint)} of {_fmt(lower)} to {_fmt(upper)} "
            f"(half-unit {_fmt(h)}, rel_tol {rel_tol:g}). Re-check the transcribed estimate, limits and confidence "
            "level; this is a consistency signal, not proof that the paper is wrong."
        )

    p_back = None
    if se_log > 0:
        p_back = math.erfc(abs(math.log(estimate) / se_log) / math.sqrt(2))
    if p_value is not None:
        if p_back is None:
            checks.append(
                {
                    "check": "p_value",
                    "passed": False,
                    "detail": "the log-scale SE is zero, so a p-value cannot be back-calculated",
                }
            )
            warnings.append("The interval has zero width on the log scale; check the limits before using the p-value.")
        else:
            p_low, p_high = _p_range(estimate, lower, upper, h, z)
            p_low, p_high = p_low * (1 - rel_tol), min(1.0, p_high * (1 + rel_tol))
            p_ok = p_low <= p_value <= p_high
            checks.append(
                {
                    "check": "p_value",
                    "passed": p_ok,
                    "detail": (
                        f"reported p={_fmt(p_value)}, back-calculated p={_fmt(p_back)}, "
                        f"range over the rounding of estimate and limits={_fmt(p_low)} to {_fmt(p_high)}"
                    ),
                }
            )
            if not p_ok:
                warnings.append(
                    f"The reported p-value is inconsistent with the interval under the stated rounding "
                    f"(reported {_fmt(p_value)}; any estimate and limits that round to the reported ones give "
                    f"p between {_fmt(p_low)} and {_fmt(p_high)}). Check whether the p-value belongs to the same "
                    "estimate and analysis."
                )

    result.update(
        status="consistent" if all(check["passed"] for check in checks) else "inconsistent",
        geometric_midpoint=_round6(midpoint),
        geometric_midpoint_range={"low": _round6(midpoint_low), "high": _round6(midpoint_high)},
        se_log=_round6(se_log),
        z=_round6(z),
        p_back_calculated=_round6(p_back),
    )
    return result


@safe_tool
def stats_check_ratio_ci(
    measure: Annotated[
        str,
        Field(description="Ratio measure: rr, or, hr, irr or rom. Names such as 'risk ratio' and 'odds_ratio' are accepted."),
    ],
    estimate: Annotated[float, Field(description="The reported point estimate on the ratio scale.")],
    ci_lower: Annotated[float, Field(description="The reported lower confidence limit (greater than 0).")],
    ci_upper: Annotated[float, Field(description="The reported upper confidence limit.")],
    ci_level: Annotated[float, Field(description="Confidence level in percent: 95 for a 95% CI.")] = 95,
    p_value: Annotated[float | None, Field(description="Optional reported two-sided p-value, e.g. 0.045.")] = None,
    decimals: Decimals = 2,
    rel_tol: RelTol = 0.01,
    review_id: ReviewIdOpt = None,
    paper_ref: Annotated[str | None, Field(description="Paper key: a DOI, 'pmid:<digits>' or 'title:<title>|<year>'.")] = None,
    label: Label = None,
) -> dict[str, Any]:
    """Check whether a ratio estimate is consistent with its confidence interval and optional p-value.

    For RR, OR, HR, IRR and ratio of means, a symmetric interval on the log
    scale has the point estimate near the geometric midpoint of its limits.
    The log-scale SE is back-calculated as (ln upper - ln lower) / (2 z), and an
    optional reported p-value is compared with the two-sided p implied by that SE.
    The rounding-aware tolerance is h + rel_tol * |computed| with
    h = 0.5 * 10^-decimals.

    Returns: status ('consistent', 'inconsistent' or 'invalid'), measure,
    estimate, ci_lower, ci_upper, ci_level, decimals, rel_tol, half_unit, checks
    (check, passed, detail), geometric_midpoint, geometric_midpoint_range (low,
    high), se_log, z, p_back_calculated, p_reported, assumptions, warnings, saved,
    check_id.

    An 'inconsistent' status means the numbers do not agree under the stated
    rounding; it does not prove the paper is wrong. Invalid numbers are reported
    with status 'invalid' and the reasons in warnings.

    Does NOT read the paper, infer the analysis model, or change stored data.
    """
    measure_clean = choice_arg(measure, "measure", extraction.RATIO_MEASURES, extraction.MEASURE_ALIASES)
    est = float_arg(estimate, "estimate")
    lower = float_arg(ci_lower, "ci_lower")
    upper = float_arg(ci_upper, "ci_upper")
    if est is None:
        fail("estimate is required; send the reported point estimate")
    if lower is None:
        fail("ci_lower is required; send the reported lower confidence limit")
    if upper is None:
        fail("ci_upper is required; send the reported upper confidence limit")
    level = _level_arg(ci_level)
    p_reported = float_arg(p_value, "p_value")
    decimals_clean = _decimals_arg(decimals)
    rel_tol_clean = _rel_tol_arg(rel_tol)
    ref = _optional_ref(paper_ref)
    label_clean = _optional_label(label)

    result = _ratio_ci_result(
        measure_clean, est, lower, upper, level, p_reported, decimals_clean, rel_tol_clean
    )
    inputs = {
        "measure": measure_clean,
        "estimate": est,
        "ci_lower": lower,
        "ci_upper": upper,
        "ci_level": level * 100,
        "p_value": p_reported,
        "decimals": decimals_clean,
        "rel_tol": rel_tol_clean,
        "paper_ref": ref,
        "label": label_clean,
    }
    saved = False
    check_id = None
    if review_id is not None and str(review_id).strip():
        with store.review_db(review_id, write=True) as conn:
            check_id = _save_check(conn, KIND_RATIO_CI, ref, label_clean, inputs, result)
        saved = True
    return {"ok": True, **result, "saved": saved, "check_id": check_id}


# ---------------------------------------------------------------------------
# 2x2 recomputation (#88)
# ---------------------------------------------------------------------------


def _recompute_assumptions(
    policy: str, applied: bool, decimals: int, rel_tol: float, level: float
) -> list[str]:
    correction = (
        "The continuity correction adds 0.5 to each of the four cells when any cell is zero; it is used for "
        "RR and OR only, while RD always uses the raw counts."
        if policy == "add_0.5_if_zero"
        else "No continuity correction is used: a zero cell makes the affected ratio not estimable."
    )
    return [
        "Arm A is the intervention (a events in n1) and arm B the comparator (c events in n2); ratios are A vs B.",
        "RR = (a/n1) / (c/n2) with a log-scale Wald interval, SE = sqrt(1/a - 1/n1 + 1/c - 1/n2).",
        (
            "OR = (a/(n1-a)) / (c/(n2-c)) with the Woolf log-scale Wald interval, "
            "SE = sqrt(1/a + 1/(n1-a) + 1/c + 1/(n2-c))."
        ),
        "RD = a/n1 - c/n2 with the Wald interval, SE = sqrt(p1(1-p1)/n1 + p2(1-p2)/n2).",
        f"Continuity policy '{policy}' selected; applied to this input: {applied}. {correction}",
        f"Intervals use the two-sided {level * 100:g}% normal deviate z.",
        (
            f"Reported values are rounded to {decimals} decimals. A value is consistent when it differs from the "
            f"recomputed value by at most h + rel_tol * |recomputed|, with h = 0.5 * 10^-{decimals} and rel_tol = {rel_tol:g}."
        ),
        "The counts are treated as two independent simple-randomised arms; no strata, clusters or adjustment are modelled.",
    ]


def _measure_result(
    estimate: float | None,
    ci_lower: float | None,
    ci_upper: float | None,
    se: float | None,
    estimable: bool,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "estimate": _round6(estimate),
        "ci_lower": _round6(ci_lower),
        "ci_upper": _round6(ci_upper),
        "se": _round6(se),
        "estimable": estimable,
        "reason": reason,
    }


def _rr_reason(a: int, c: int, both_zero: bool) -> str:
    if both_zero:
        return "both arms have zero events, so the risk ratio is not estimable even with a continuity correction"
    zero_arms = [name for name, value in (("arm A", a), ("arm B", c)) if value == 0]
    return (
        f"zero events in {' and '.join(zero_arms)}, so the risk ratio is not estimable without a continuity "
        "correction; use continuity_correction='add_0.5_if_zero' if that correction is intended"
    )


def _or_reason(a: int, n1: int, c: int, n2: int, both_zero: bool) -> str:
    if both_zero:
        return "both arms have zero events, so the odds ratio is not estimable even with a continuity correction"
    zero_cells = [
        name
        for name, value in (
            ("events in arm A", a),
            ("non-events in arm A", n1 - a),
            ("events in arm B", c),
            ("non-events in arm B", n2 - c),
        )
        if value == 0
    ]
    return (
        f"a zero cell ({', '.join(zero_cells)}) makes the odds ratio not estimable without a continuity correction; "
        "use continuity_correction='add_0.5_if_zero' if that correction is intended"
    )


def _reported_arg(reported: Any) -> dict[str, Any] | None:
    if reported is None:
        return None
    if not isinstance(reported, dict):
        fail("reported must be an object such as {'measure': 'rr', 'estimate': 0.5, 'ci_lower': 0.25, 'ci_upper': 1.0}")
    measure = choice_arg(reported.get("measure"), "reported.measure", RECOMPUTE_MEASURES, extraction.MEASURE_ALIASES)
    estimate = float_arg(reported.get("estimate"), "reported.estimate")
    if estimate is None:
        fail("reported.estimate is required when reported is given")
    lower = float_arg(reported.get("ci_lower"), "reported.ci_lower")
    upper = float_arg(reported.get("ci_upper"), "reported.ci_upper")
    if (lower is None) != (upper is None):
        fail("give both reported.ci_lower and reported.ci_upper, or neither")
    if lower is not None and upper is not None and lower > upper:
        fail(
            f"reported.ci_lower ({_fmt(lower)}) is greater than reported.ci_upper ({_fmt(upper)}); "
            "send the limits in ascending order"
        )
    if measure in ("rr", "or") and any(value is not None and value <= 0 for value in (estimate, lower, upper)):
        fail(f"reported {measure.upper()} is a ratio: the estimate and CI limits must be greater than 0")
    return {"measure": measure, "estimate": estimate, "ci_lower": lower, "ci_upper": upper}


def _compare_reported(
    reported: dict[str, Any], measures: dict[str, dict[str, Any]], h: float, rel_tol: float
) -> dict[str, Any]:
    measure = reported["measure"]
    target = measures[measure]
    values: list[dict[str, Any]] = []
    for key in ("estimate", "ci_lower", "ci_upper"):
        if reported.get(key) is None:
            continue
        recomputed = target[key]
        if recomputed is None:
            values.append(
                {
                    "value": key,
                    "reported": _round6(reported[key]),
                    "recomputed": None,
                    "difference": None,
                    "tolerance": None,
                    "matches": False,
                }
            )
            continue
        tolerance = h + rel_tol * abs(recomputed)
        difference = reported[key] - recomputed
        values.append(
            {
                "value": key,
                "reported": _round6(reported[key]),
                "recomputed": _round6(recomputed),
                "difference": _round6(difference),
                "tolerance": _round6(tolerance),
                "matches": abs(difference) <= tolerance,
            }
        )
    mismatches = [value["value"] for value in values if not value["matches"]]
    note = None
    if not target["estimable"]:
        note = f"{measure.upper()} is not estimable from these counts: {target['reason']}"
    elif mismatches:
        note = "Reported value(s) differ from the recomputed value(s) beyond the stated rounding."
    else:
        note = "All reported value(s) agree with the recomputed value(s) within the stated rounding."
    return {
        "measure": measure,
        "estimable": target["estimable"],
        "values": values,
        "matches": not mismatches,
        "mismatches": mismatches,
        "note": note,
    }


@safe_tool
def stats_recompute_2x2(
    events_a: Annotated[Any, Field(description="Events in arm A (the intervention arm); a whole number.")],
    total_a: Annotated[Any, Field(description="Participants in arm A; a whole number of at least 1.")],
    events_b: Annotated[Any, Field(description="Events in arm B (the comparator arm); a whole number.")],
    total_b: Annotated[Any, Field(description="Participants in arm B; a whole number of at least 1.")],
    continuity_correction: Annotated[
        str,
        Field(description="'none' (zero cells make RR/OR not estimable) or 'add_0.5_if_zero' (add 0.5 to all four cells when any is zero)."),
    ] = "none",
    reported: Annotated[
        Any,
        Field(description="Optional reported result to compare: {measure: 'rr'|'or'|'rd', estimate, ci_lower?, ci_upper?}."),
    ] = None,
    ci_level: Annotated[float, Field(description="Confidence level in percent: 95 for a 95% CI.")] = 95,
    decimals: Decimals = 2,
    rel_tol: RelTol = 0.01,
    review_id: ReviewIdOpt = None,
    paper_ref: Annotated[str | None, Field(description="Paper key: a DOI, 'pmid:<digits>' or 'title:<title>|<year>'.")] = None,
    label: Label = None,
) -> dict[str, Any]:
    """Recompute RR, OR and RD from arm-level events and totals, optionally comparing a reported result.

    Arm A is the intervention and arm B the comparator; ratios are A vs B. RR
    and OR use log-scale Wald intervals (Woolf for OR); RD uses a Wald interval.
    A zero cell makes the affected ratio not estimable under
    continuity_correction='none'. With 'add_0.5_if_zero' 0.5 is added to each
    of the four cells when any cell is zero, and the output states that the
    correction was applied. Both arms zero events always leave RR and OR not
    estimable; RD is then 0 with its interval. RD always uses the raw counts.

    A reported object {measure, estimate, ci_lower?, ci_upper?} is compared with
    the recomputed measure using h + rel_tol * |recomputed|, where
    h = 0.5 * 10^-decimals.

    Returns: events_a, total_a, events_b, total_b, arm_a, arm_b,
    continuity_correction, correction_applied, cells_used_for_ratios,
    rr, or, rd (each with estimate, ci_lower, ci_upper, se, estimable, reason),
    reported, comparison (measure, estimable, values, matches, mismatches, note),
    status, assumptions, warnings, saved, check_id.

    Impossible counts are refused with a message naming the field. A comparison
    match is arithmetic agreement under the stated rounding, not proof that the
    paper is correct.

    Does NOT read the paper, pool studies, or adjust for strata or clusters.
    """
    a = int_arg(events_a, "events_a", min_value=0, max_value=1_000_000_000)
    n1 = int_arg(total_a, "total_a", min_value=1, max_value=1_000_000_000)
    c = int_arg(events_b, "events_b", min_value=0, max_value=1_000_000_000)
    n2 = int_arg(total_b, "total_b", min_value=1, max_value=1_000_000_000)
    if a is None or n1 is None or c is None or n2 is None:
        fail("events_a, total_a, events_b and total_b are required whole numbers")
    if a > n1:
        fail(f"events_a ({a}) cannot exceed total_a ({n1}); send the number with the event and the arm total")
    if c > n2:
        fail(f"events_b ({c}) cannot exceed total_b ({n2}); send the number with the event and the arm total")
    policy = choice_arg(continuity_correction, "continuity_correction", CORRECTION_POLICIES, CORRECTION_ALIASES)
    reported_clean = _reported_arg(reported)
    level = _level_arg(ci_level)
    decimals_clean = _decimals_arg(decimals)
    rel_tol_clean = _rel_tol_arg(rel_tol)
    ref = _optional_ref(paper_ref)
    label_clean = _optional_label(label)

    h = 0.5 * 10.0 ** (-decimals_clean)
    z = NormalDist().inv_cdf(1 - (1 - level) / 2)
    both_zero = a == 0 and c == 0
    zero_cell = a == 0 or c == 0 or (n1 - a) == 0 or (n2 - c) == 0
    correction_applied = policy == "add_0.5_if_zero" and zero_cell and not both_zero
    if correction_applied:
        a_used, n1_used = a + 0.5, n1 + 1.0
        c_used, n2_used = c + 0.5, n2 + 1.0
    else:
        a_used, n1_used = float(a), float(n1)
        c_used, n2_used = float(c), float(n2)

    warnings: list[str] = []
    if both_zero:
        warnings.append(
            "Both arms have zero events; RR and OR are not estimable whatever the continuity correction. RD is 0."
        )
    elif correction_applied:
        warnings.append(
            "Continuity correction add_0.5_if_zero applied: 0.5 was added to each of the four cells for RR and OR; "
            "RD uses the raw counts."
        )
    elif zero_cell and policy == "none":
        warnings.append(
            "At least one cell is zero, so the affected RR/OR values are null. Use "
            "continuity_correction='add_0.5_if_zero' only if you intend to apply that correction."
        )
    if policy == "add_0.5_if_zero" and zero_cell and both_zero:
        warnings.append(
            "The correction was not applied because both arms have zero events and a ratio cannot be estimated "
            "from a table with no events in either arm."
        )

    measures: dict[str, dict[str, Any]] = {}
    if not both_zero and a_used > 0 and c_used > 0:
        rr_estimate = (a_used / n1_used) / (c_used / n2_used)
        rr_se = math.sqrt(1 / a_used - 1 / n1_used + 1 / c_used - 1 / n2_used)
        measures["rr"] = _measure_result(
            rr_estimate,
            math.exp(math.log(rr_estimate) - z * rr_se),
            math.exp(math.log(rr_estimate) + z * rr_se),
            rr_se,
            True,
            None,
        )
    else:
        measures["rr"] = _measure_result(None, None, None, None, False, _rr_reason(a, c, both_zero))

    non_events_a = n1_used - a_used
    non_events_b = n2_used - c_used
    if not both_zero and a_used > 0 and non_events_a > 0 and c_used > 0 and non_events_b > 0:
        or_estimate = (a_used * non_events_b) / (c_used * non_events_a)
        or_se = math.sqrt(1 / a_used + 1 / non_events_a + 1 / c_used + 1 / non_events_b)
        measures["or"] = _measure_result(
            or_estimate,
            math.exp(math.log(or_estimate) - z * or_se),
            math.exp(math.log(or_estimate) + z * or_se),
            or_se,
            True,
            None,
        )
    else:
        measures["or"] = _measure_result(None, None, None, None, False, _or_reason(a, n1, c, n2, both_zero))

    p1 = a / n1
    p2 = c / n2
    rd_estimate = p1 - p2
    rd_se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    measures["rd"] = _measure_result(
        rd_estimate,
        rd_estimate - z * rd_se,
        rd_estimate + z * rd_se,
        rd_se,
        True,
        None,
    )

    comparison = (
        _compare_reported(reported_clean, measures, h, rel_tol_clean) if reported_clean is not None else None
    )
    if comparison is not None:
        for value in comparison["values"]:
            if value["matches"]:
                continue
            label_text = REPORTED_VALUE_LABELS[value["value"]]
            if value["recomputed"] is None:
                warnings.append(
                    f"Reported {reported_clean['measure'].upper()} {label_text} cannot be compared because the "
                    f"recomputed value is not estimable: {measures[reported_clean['measure']]['reason']}."
                )
            else:
                warnings.append(
                    f"Reported {reported_clean['measure'].upper()} {label_text} {_fmt(value['reported'])} is "
                    f"inconsistent with the recomputed value {_fmt(value['recomputed'])} under the stated "
                    f"rounding (tolerance {_fmt(value['tolerance'])}); re-check the 2x2 counts and the reported value."
                )

    if comparison is not None:
        status = "consistent" if comparison["matches"] else "inconsistent"
    elif measures["rr"]["estimable"] or measures["or"]["estimable"]:
        status = "recomputed"
    else:
        status = "not_estimable"

    result = {
        "events_a": a,
        "total_a": n1,
        "events_b": c,
        "total_b": n2,
        "arm_a": {"events": a, "total": n1, "risk": _round6(p1)},
        "arm_b": {"events": c, "total": n2, "risk": _round6(p2)},
        "continuity_correction": policy,
        "correction_applied": correction_applied,
        "cells_used_for_ratios": {
            "events_a": _round6(a_used),
            "non_events_a": _round6(non_events_a),
            "total_a": _round6(n1_used),
            "events_b": _round6(c_used),
            "non_events_b": _round6(non_events_b),
            "total_b": _round6(n2_used),
        },
        "rr": measures["rr"],
        "or": measures["or"],
        "rd": measures["rd"],
        "reported": reported_clean,
        "comparison": comparison,
        "status": status,
        "assumptions": _recompute_assumptions(policy, correction_applied, decimals_clean, rel_tol_clean, level),
        "warnings": warnings,
    }
    inputs = {
        "events_a": a,
        "total_a": n1,
        "events_b": c,
        "total_b": n2,
        "continuity_correction": policy,
        "reported": reported_clean,
        "ci_level": level * 100,
        "decimals": decimals_clean,
        "rel_tol": rel_tol_clean,
        "paper_ref": ref,
        "label": label_clean,
    }
    saved = False
    check_id = None
    if review_id is not None and str(review_id).strip():
        with store.review_db(review_id, write=True) as conn:
            check_id = _save_check(conn, KIND_RECOMPUTE, ref, label_clean, inputs, result)
        saved = True
    return {"ok": True, **result, "saved": saved, "check_id": check_id}


# ---------------------------------------------------------------------------
# Evidence-wide ratio check
# ---------------------------------------------------------------------------


@safe_tool
def stats_check_evidence(
    review_id: ReviewId,
    decimals: Decimals = 2,
    rel_tol: RelTol = 0.01,
) -> dict[str, Any]:
    """Run the ratio-CI consistency check over every stored evidence row that has a ratio measure.

    Evidence rows with a non-ratio measure, a missing estimate or a missing
    limit are listed with status 'skipped' and the reason. A stored ci_level is
    a percentage (95 means 95%) and is converted to a fraction. The same
    rounding-aware tolerance as stats_check_ratio_ci is used.

    Returns: rows (paper_ref, outcome, measure, estimate, ci_lower, ci_upper,
    ci_level, status, warnings; capped at 200), truncated, checked, skipped,
    counts (consistent, inconsistent, invalid, skipped), assumptions, note.

    Does NOT save anything, edit evidence rows, or judge the paper's analysis.
    """
    decimals_clean = _decimals_arg(decimals)
    rel_tol_clean = _rel_tol_arg(rel_tol)
    with store.review_db(review_id) as conn:
        evidence = extraction.evidence_rows(conn)

    rows: list[dict[str, Any]] = []
    counts = {status: 0 for status in STATUSES}
    for row in evidence:
        measure = row.get("effect_measure")
        base = {
            "paper_ref": row.get("paper_ref"),
            "outcome": row.get("outcome"),
            "measure": measure,
            "estimate": row.get("effect_estimate"),
            "ci_lower": row.get("ci_lower"),
            "ci_upper": row.get("ci_upper"),
            "ci_level": row.get("ci_level"),
        }
        if measure not in extraction.RATIO_MEASURES:
            counts["skipped"] += 1
            rows.append(
                {
                    **base,
                    "status": "skipped",
                    "warnings": [
                        (
                            "Not a ratio measure; this check applies to rr, or, hr, irr and rom. "
                            "Difference measures are checked elsewhere."
                        )
                    ],
                }
            )
            continue
        missing = [
            name
            for name, value in (
                ("effect_estimate", row.get("effect_estimate")),
                ("ci_lower", row.get("ci_lower")),
                ("ci_upper", row.get("ci_upper")),
            )
            if value is None
        ]
        if missing:
            counts["skipped"] += 1
            rows.append(
                {
                    **base,
                    "status": "skipped",
                    "warnings": [
                        (
                            f"Missing {', '.join(missing)}; record the estimate and both confidence limits "
                            "with evidence_record to check this row."
                        )
                    ],
                }
            )
            continue
        if row.get("ci_level") is None:
            counts["skipped"] += 1
            rows.append(
                {
                    **base,
                    "status": "skipped",
                    "warnings": [
                        (
                            "ci_level is missing, so the confidence level is unknown and is not guessed; "
                            "re-record the row with evidence_record and its ci_level."
                        )
                    ],
                }
            )
            continue
        level = float(row["ci_level"]) / 100.0
        checked = _ratio_ci_result(
            measure,
            float(row["effect_estimate"]),
            float(row["ci_lower"]),
            float(row["ci_upper"]),
            level,
            None,
            decimals_clean,
            rel_tol_clean,
        )
        counts[checked["status"]] += 1
        rows.append({**base, "status": checked["status"], "warnings": checked["warnings"]})

    listed, truncated = cap(rows)
    return {
        "ok": True,
        "rows": listed,
        "truncated": truncated,
        "checked": len(rows) - counts["skipped"],
        "skipped": counts["skipped"],
        "counts": counts,
        "assumptions": _ratio_assumptions(decimals_clean, rel_tol_clean, None),
        "note": (
            "Arithmetic consistency check only. An 'inconsistent' row means the stored estimate and limits do "
            "not agree under the stated rounding, not that the paper's result is wrong."
        ),
    }


def register(server: MCPServer) -> None:
    for tool in (stats_check_ratio_ci, stats_recompute_2x2, stats_check_evidence):
        server.tool()(tool)
