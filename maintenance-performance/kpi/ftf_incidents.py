"""Incident-based first-time-fix policy and aggregation helpers.

The source exports do not provide structured failure modes or confirmed incident identity;
these helpers therefore never auto-confirm a repeat or auto-classify an incident as successful.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from utils.dates import week_key, production_week

STATUS_NEEDS_CLASSIFICATION = "Needs classification"
STATUS_OPEN = "Open / in progress"
STATUS_PENDING = "Pending verification"
STATUS_SUCCESS = "Successful first-time fix"
STATUS_FAILED = "Failed first-time fix"
STATUS_EXCLUDED = "Excluded"
STATUS_UNRESOLVED = "Unresolved review"
FAILURE_REASONS = ["Diagnosis", "Workmanship", "Parts unavailable", "Temporary repair",
                   "Tools/information unavailable", "Other", "Unknown/pending review"]


def normalize_tl(value) -> str | None:
    """Normalize only whitespace and case; preserve punctuation and leading zeros."""
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    return value.casefold() if value else None


def incident_match_key(tl, component=None, failure_mode=None) -> tuple[str, str | None, str] | None:
    tl_key = normalize_tl(tl)
    mode = normalize_tl(failure_mode)
    if not tl_key or not mode:
        return None
    return tl_key, normalize_tl(component), mode


def potential_repeat(a: dict, b: dict) -> tuple[bool, str]:
    """Explain a candidate match, strictly requiring individual TL and same failure mode."""
    a_tl, b_tl = normalize_tl(a.get("asset_tl")), normalize_tl(b.get("asset_tl"))
    if not a_tl or not b_tl:
        return False, "Missing individual asset TL#; mapping/review required."
    if a_tl != b_tl:
        return False, "Different individual asset TL# values."
    a_mode, b_mode = normalize_tl(a.get("failure_mode")), normalize_tl(b.get("failure_mode"))
    if not a_mode or not b_mode:
        return False, "Failure mode is missing; relationship cannot be suggested."
    if a_mode != b_mode:
        return False, "Different failure modes; treat as independent unless a reviewer confirms a connection."
    a_comp, b_comp = normalize_tl(a.get("component")), normalize_tl(b.get("component"))
    if a_comp and b_comp and a_comp != b_comp:
        return False, "Different component/sub-asset values."
    if not a_comp or not b_comp:
        return True, "Same TL# and failure mode; component missing on at least one incident, reviewer confirmation required."
    return True, "Same normalized TL#, component/sub-asset, and failure mode; reviewer confirmation required."


def cohort_week(value) -> int | None:
    if value is None or pd.isna(value):
        return None
    ts = pd.Timestamp(value).to_pydatetime()
    year, week = production_week(ts, 1.0)
    return week_key(year, week)


def verification_deadline(return_to_service, method: str, value: float) -> datetime | None:
    if return_to_service is None or pd.isna(return_to_service):
        return None
    if method != "Calendar days":
        return None  # No reliable operating-hour or cycle meter is present in these sources.
    return pd.Timestamp(return_to_service).to_pydatetime() + timedelta(days=float(value))


def evaluate_incident(row: dict, as_of: datetime, coverage: dict[str, date], unresolved_review: bool = False,
                      confirmed_repeat: bool = False) -> dict:
    """Return explicit state and finalization eligibility without guessing missing evidence."""
    if row.get("manual_status") == STATUS_EXCLUDED:
        return {"status": STATUS_EXCLUDED, "assessed": False, "success": False, "coverage_sufficient": False}
    if row.get("manual_status") == STATUS_OPEN or not row.get("intervention_end"):
        return {"status": STATUS_OPEN, "assessed": False, "success": False, "coverage_sufficient": False}
    required = (normalize_tl(row.get("asset_tl")), row.get("symptom"), row.get("failure_mode"),
                row.get("return_to_service"), row.get("intervention_start"))
    if any(value is None or (isinstance(value, str) and not value.strip()) for value in required):
        return {"status": STATUS_NEEDS_CLASSIFICATION, "assessed": False, "success": False,
                "coverage_sufficient": False}
    if not bool(row.get("return_documented")):
        return {"status": STATUS_NEEDS_CLASSIFICATION, "assessed": False, "success": False,
                "coverage_sufficient": False}
    if unresolved_review:
        return {"status": STATUS_UNRESOLVED, "assessed": False, "success": False, "coverage_sufficient": False}
    deadline = row.get("verification_deadline")
    if deadline is None or pd.isna(deadline):
        deadline = verification_deadline(row.get("return_to_service"), row.get("verification_method", "Calendar days"),
                                         row.get("verification_value", 7))
    if deadline is None:
        return {"status": STATUS_NEEDS_CLASSIFICATION, "assessed": False, "success": False,
                "coverage_sufficient": False}
    covered_to = [coverage.get(s) for s in ("fiix", "ignition")]
    enough = all(d is not None and pd.Timestamp(d).date() >= pd.Timestamp(deadline).date() for d in covered_to)
    if confirmed_repeat:
        mature = pd.Timestamp(as_of) >= pd.Timestamp(deadline)
        return {"status": STATUS_FAILED, "assessed": bool(enough and mature), "success": False,
                "coverage_sufficient": bool(enough)}
    if not enough:
        return {"status": STATUS_PENDING, "assessed": False, "success": False, "coverage_sufficient": False}
    if pd.Timestamp(as_of) < pd.Timestamp(deadline):
        return {"status": STATUS_PENDING, "assessed": False, "success": False, "coverage_sufficient": True}
    return {"status": STATUS_SUCCESS, "assessed": True, "success": True, "coverage_sufficient": True}


def summarize_incidents(incidents: pd.DataFrame) -> dict:
    """Pooled incident counts; never average group percentages."""
    if incidents.empty:
        return {"assessed": 0, "successes": 0, "ftf_pct": None}
    assessed = incidents[incidents["assessed"].astype(bool)]
    n, s = len(assessed), int(assessed["success"].astype(bool).sum())
    return {"assessed": n, "successes": s, "ftf_pct": s / n * 100.0 if n else None}


def pooled_four_week_baseline(incidents: pd.DataFrame, selected_week: int) -> dict:
    from utils.dates import shift_week

    weeks = {shift_week(selected_week, -n) for n in range(1, 5)}
    subset = incidents[incidents["cohort_week"].isin(weeks)] if not incidents.empty else incidents
    result = summarize_incidents(subset)
    result["weeks"] = sorted(weeks)
    return result
