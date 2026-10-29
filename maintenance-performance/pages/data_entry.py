"""Manual weekly Dayforce hours and PM Open vs Closed entry."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from database import repository as repo
from kpi.labor_efficiency import total_hours_worked
from kpi.pm_completion import pm_completion_pct
from utils.dates import production_week, shift_week, split_week_key, week_key, week_label
from utils.names import build_alias_index, clean_text, name_key

DAYFORCE_FIELDS = ["regular_hours", "overtime_hours", "other_paid_hours", "excluded_hours", "total_override", "comments"]
DAYFORCE_CSV = {"RegularHours": "regular_hours", "OvertimeHours": "overtime_hours", "OtherPaidHours": "other_paid_hours",
                "ExcludedHours": "excluded_hours", "TotalHoursWorked": "total_override", "Comments": "comments"}
PM_FIELDS = ["area", "pm_open", "pm_closed", "comments"]
PM_CSV = {"Area": "area", "Open": "pm_open", "Closed": "pm_closed", "Comments": "comments"}


def render(ctx) -> None:
    st.title("Data Entry")
    t1, t2 = st.tabs(["Dayforce Hours Worked", "PM Open vs Closed"])
    with t1:
        if ctx.base.technicians.empty:
            st.warning("Add technicians in Administration → Technicians before entering hours.")
        else:
            _dayforce_entry(ctx)
    with t2:
        _pm_entry(ctx)


def _week_picker(ctx, prefix: str) -> tuple[int, int]:
    default = ctx.filters.end_week or week_key(*production_week(pd.Timestamp.now().to_pydatetime()))
    y0, w0 = split_week_key(default)
    c1, c2, _ = st.columns([1, 1, 3])
    year = c1.number_input("Year", 2020, 2100, value=y0, key=f"{prefix}_year")
    week = c2.number_input("Calendar week", 1, 53, value=w0, key=f"{prefix}_week")
    try:
        date.fromisocalendar(int(year), int(week), 1)
    except ValueError:
        st.error(f"{year} has no ISO week {week}.")
        st.stop()
    return int(year), int(week)


def _done(message: str, warning: bool = False) -> None:
    st.session_state["entry_nonce"] = st.session_state.get("entry_nonce", 0) + 1
    st.session_state["entry_message"] = message
    st.session_state["entry_warning"] = warning
    st.rerun()


def _week_header(ctx, dataset: str, year: int, week: int) -> bool:
    locked = repo.is_week_locked(ctx.engine, dataset, year, week)
    if msg := st.session_state.pop("entry_message", None):
        warning = st.session_state.pop("entry_warning", False)
        (st.warning if warning else st.success)(msg)
    if locked:
        st.info(f"{week_label(week_key(year, week))} is locked (finalized). Unlock it to make changes.",
                icon=":material/lock:")
    return locked


def _lock_button(ctx, col, dataset: str, year: int, week: int, locked: bool) -> None:
    if col.button("Unlock week" if locked else "Lock finalized week", key=f"{dataset}_lock",
                  icon=":material/lock_open:" if locked else ":material/lock:"):
        repo.set_week_lock(ctx.engine, dataset, year, week, not locked)
        _done(f"{week_label(week_key(year, week))} {'unlocked' if locked else 'locked'}.")


# ---------- Dayforce ----------

def _dayforce_grid(ctx, wk: int) -> pd.DataFrame:
    techs = ctx.base.technicians
    source = ctx.base.dayforce_manual
    existing = source[source["week_key"] == wk].drop(columns=["shift", "technician"], errors="ignore")
    imported = ctx.base.dayforce_imported_week(wk)
    ids = (set(techs.loc[techs["active"], "technician_id"]) | set(existing["technician_id"].dropna())
           | set(imported.index))
    grid = techs[techs["technician_id"].isin(ids)][["technician_id", "technician_name", "shift"]]
    grid = grid.astype({"technician_id": "Int64"}).merge(existing, on="technician_id", how="left")
    grid["imported_hours"] = grid["technician_id"].map(imported).astype("float64")
    overridden = grid["total_overridden"].fillna(0).astype(bool)
    grid["total_override"] = grid["total_hours_worked"].where(overridden)
    cols = ["imported_hours", "regular_hours", "overtime_hours", "other_paid_hours", "excluded_hours",
            "total_override", "total_hours_worked"]
    for c in cols:
        grid[c] = pd.to_numeric(grid[c], errors="coerce").astype("float64")
    grid["comments"] = grid["comments"].astype("object")
    return grid[["technician_id", "technician_name", "shift"] + cols + ["comments"]].sort_values(["shift", "technician_name"])


def _dayforce_entry(ctx) -> None:
    st.caption("Hours are imported automatically from Dayforce reports (*Dayforce Import* column: REG + OT hours "
               "worked; premium, OT memo, PTO and Tool & Die hours excluded). Enter values here only to correct or "
               "replace the import — a saved manual row overrides the imported hours for that technician and week. "
               "TotalHoursWorked = Regular + Overtime + OtherPaid − Excluded, or *Total Override*.")
    year, week = _week_picker(ctx, "dayforce_entry")
    wk = week_key(year, week)
    locked = _week_header(ctx, "dayforce", year, week)

    numeric = st.column_config.NumberColumn
    config = {
        "technician_id": None,
        "technician_name": st.column_config.TextColumn("Technician", disabled=True),
        "shift": st.column_config.TextColumn("Shift", disabled=True),
        "comments": st.column_config.TextColumn("Comments"),
        "imported_hours": numeric("Dayforce Import (h)", disabled=True, format="%.2f"),
        "regular_hours": numeric("Regular Hours", min_value=0.0, step=0.25, format="%.2f"),
        "overtime_hours": numeric("Overtime Hours", min_value=0.0, step=0.25, format="%.2f"),
        "other_paid_hours": numeric("Other Paid Hours", min_value=0.0, step=0.25, format="%.2f"),
        "excluded_hours": numeric("Excluded Hours", min_value=0.0, step=0.25, format="%.2f"),
        "total_override": numeric("Total Override", min_value=0.0, step=0.25, format="%.2f"),
        "total_hours_worked": numeric("Saved Total Hours Worked", disabled=True, format="%.2f"),
    }
    edited = st.data_editor(_dayforce_grid(ctx, wk), column_config=config, hide_index=True, disabled=locked,
                            key=f"dayforce_editor_{wk}_{st.session_state.get('entry_nonce', 0)}")
    totals = [r["total_override"] if pd.notna(r["total_override"]) else total_hours_worked(
        r["regular_hours"], r["overtime_hours"], r["other_paid_hours"], r["excluded_hours"])
        for r in edited.to_dict("records")]
    manual = [r["technician_id"] for r in edited.to_dict("records")
              if any(pd.notna(r[c]) for c in ("regular_hours", "overtime_hours", "other_paid_hours", "total_override"))]
    effective = sum(t for t, r in zip(totals, edited.to_dict("records")) if r["technician_id"] in manual)
    effective += edited.loc[~edited["technician_id"].isin(manual), "imported_hours"].fillna(0).sum()
    st.caption(f"Hours worked used for Labor Efficiency this week (unsaved edits included): **{effective:,.2f} h**")

    fields = DAYFORCE_FIELDS
    value_cols = [f for f in fields if f != "comments"]
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Save", type="primary", disabled=locked, key="dayforce_save", icon=":material/save:"):
        try:
            n = repo.save_dayforce_week(ctx.engine, year, week, edited[["technician_id", "shift"] + fields].to_dict("records"))
            _done(f"Saved {n} row(s) for {week_label(wk)}.")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))

    if c2.button("Copy prior week", disabled=locked, key="dayforce_copy", icon=":material/content_copy:"):
        prior = _dayforce_grid(ctx, shift_week(wk, -1))
        prior = prior[prior[value_cols].notna().any(axis=1)]
        empty_now = edited[edited[value_cols].isna().all(axis=1)]["technician_id"]
        rows = prior[prior["technician_id"].isin(empty_now)][["technician_id", "shift"] + fields]
        if rows.empty:
            st.warning("Nothing to copy: prior week has no entries for technicians that are blank this week.")
        else:
            repo.save_dayforce_week(ctx.engine, year, week, rows.to_dict("records"))
            _done(f"Copied {len(rows)} row(s) from {week_label(shift_week(wk, -1))}.")

    with c3.popover("Bulk fill", disabled=locked, icon=":material/format_paint:"):
        reg = st.number_input("Regular hours", 0.0, 100.0, 40.0, 0.25, key="bulk_reg")
        ot = st.number_input("Overtime hours", 0.0, 100.0, 0.0, 0.25, key="bulk_ot")
        scope = st.radio("Apply to", ["Blank rows only", "All rows"], key="dayforce_bulk_scope")
        if st.button("Apply bulk fill", key="dayforce_bulk_apply"):
            rows = edited.copy()
            target = rows[value_cols].isna().all(axis=1) if scope == "Blank rows only" else pd.Series(True, index=rows.index)
            rows.loc[target, "regular_hours"] = reg
            rows.loc[target, "overtime_hours"] = ot
            repo.save_dayforce_week(ctx.engine, year, week,
                                    rows.loc[target, ["technician_id", "shift"] + fields].to_dict("records"))
            _done(f"Bulk filled {int(target.sum())} row(s).")

    _lock_button(ctx, c4, "dayforce", year, week, locked)

    with st.expander("Import from CSV", icon=":material/upload_file:"):
        _dayforce_csv(ctx)


def _dayforce_csv(ctx) -> None:
    st.caption(f"Required columns: Year, CalendarWeek, Technician. Optional: {', '.join(DAYFORCE_CSV)}. "
               "Technicians are matched by name, Dayforce name or Dayforce alias (exact, case-insensitive).")
    df = _read_csv_upload("dayforce_csv", ["Year", "CalendarWeek", "Technician"])
    if df is None:
        return
    index, _ = build_alias_index(ctx.base.technicians, ctx.base.aliases, "dayforce")
    df["technician_id"] = df["Technician"].map(name_key).map(index).astype("Int64")
    df["Match"] = df["technician_id"].map(lambda v: "Matched" if pd.notna(v) else "Unmatched")
    st.dataframe(df, hide_index=True)
    valid = df[df["technician_id"].notna() & df["Year"].notna() & df["CalendarWeek"].notna()]
    st.caption(f"{len(valid)} of {len(df)} rows can be imported. Unmatched names can be mapped in Administration.")
    if st.button(f"Import {len(valid)} row(s)", disabled=valid.empty, key="dayforce_csv_go"):
        shifts = ctx.base.technicians.set_index("technician_id")["shift"]
        rows_by_week = {}
        for r in valid.to_dict("records"):
            row = {"technician_id": int(r["technician_id"]), "shift": shifts.get(r["technician_id"])}
            row.update({dst: r.get(src) for src, dst in DAYFORCE_CSV.items() if src in r})
            rows_by_week.setdefault((int(r["Year"]), int(r["CalendarWeek"])), []).append(row)
        _import_weeks(rows_by_week, lambda y, w, rows: repo.save_dayforce_week(ctx.engine, y, w, rows))


# ---------- PM Open vs Closed ----------

def _pm_grid(ctx, wk: int) -> pd.DataFrame:
    areas = list(ctx.base.app_settings.get("pm_areas") or ["Final Assembly", "SMT"])
    existing = ctx.base.pm_status[ctx.base.pm_status["week_key"] == wk]
    areas += [a for a in existing["area"] if a not in areas]
    grid = pd.DataFrame({"area": areas}).merge(existing[["area", "pm_open", "pm_closed", "comments"]],
                                               on="area", how="left")
    for c in ("pm_open", "pm_closed"):
        grid[c] = pd.to_numeric(grid[c], errors="coerce").astype("float64")
    grid["comments"] = grid["comments"].astype("object")
    return grid


def _pm_entry(ctx) -> None:
    st.caption("Enter the number of PMs **Open** (opened / due) and how many of them were **Closed** for each area. "
               "PM Completion % = Closed ÷ Open (% of open PMs closed), reported separately for each area.")
    year, week = _week_picker(ctx, "pm_entry")
    wk = week_key(year, week)
    locked = _week_header(ctx, "pm", year, week)

    numeric = st.column_config.NumberColumn
    edited = st.data_editor(
        _pm_grid(ctx, wk), hide_index=True, disabled=locked,
        key=f"pm_editor_{wk}_{st.session_state.get('entry_nonce', 0)}",
        column_config={
            "area": st.column_config.TextColumn("Area", disabled=True),
            "pm_open": numeric("PMs Open", min_value=0.0, step=1.0, format="%.0f"),
            "pm_closed": numeric("PMs Closed", min_value=0.0, step=1.0, format="%.0f"),
            "comments": st.column_config.TextColumn("Comments"),
        },
    )

    preview = []
    for r in edited.to_dict("records"):
        remaining = max((r["pm_open"] or 0) - (r["pm_closed"] or 0), 0) if pd.notna(r["pm_open"]) else None
        preview.append({"Area": r["area"], "PMs Remaining": remaining,
                        "PM Completion %": pm_completion_pct(r["pm_closed"], r["pm_open"])})
        if pd.notna(r["pm_open"]) and pd.notna(r["pm_closed"]) and r["pm_closed"] > r["pm_open"]:
            st.warning(f"{r['area']}: PMs Closed ({r['pm_closed']:.0f}) is more than PMs Open ({r['pm_open']:.0f}).")
    st.dataframe(pd.DataFrame(preview), hide_index=True,
                 column_config={"PMs Remaining": st.column_config.NumberColumn(format="%.0f"),
                                "PM Completion %": st.column_config.NumberColumn(format="%.1f%%")})

    c1, c2, c3, _ = st.columns(4)
    if c1.button("Save", type="primary", disabled=locked, key="pm_save", icon=":material/save:"):
        try:
            n = repo.save_pm_status_week(ctx.engine, year, week, edited[PM_FIELDS].to_dict("records"))
            _done(f"Saved {n} area(s) for {week_label(wk)}.")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))

    if c2.button("Copy prior week", disabled=locked, key="pm_copy", icon=":material/content_copy:"):
        prior = _pm_grid(ctx, shift_week(wk, -1))
        prior = prior[prior[["pm_open", "pm_closed"]].notna().any(axis=1)]
        blank_now = edited[edited[["pm_open", "pm_closed"]].isna().all(axis=1)]["area"]
        rows = prior[prior["area"].isin(blank_now)]
        if rows.empty:
            st.warning("Nothing to copy: prior week has no entries for areas that are blank this week.")
        else:
            repo.save_pm_status_week(ctx.engine, year, week, rows[PM_FIELDS].to_dict("records"))
            _done(f"Copied {len(rows)} area(s) from {week_label(shift_week(wk, -1))}.")

    _lock_button(ctx, c3, "pm", year, week, locked)

    with st.expander("Import from CSV", icon=":material/upload_file:"):
        st.caption("Required columns: Year, CalendarWeek, Area, Open, Closed. Optional: Comments. "
                   "Area should match an area name (e.g. Final Assembly, SMT).")
        df = _read_csv_upload("pm_csv", ["Year", "CalendarWeek", "Area", "Open", "Closed"])
        if df is None:
            return
        st.dataframe(df, hide_index=True)
        valid = df[df["Year"].notna() & df["CalendarWeek"].notna() & df["Area"].map(clean_text).notna()]
        if st.button(f"Import {len(valid)} row(s)", disabled=valid.empty, key="pm_csv_go"):
            rows_by_week = {}
            for r in valid.to_dict("records"):
                row = {dst: r.get(src) for src, dst in PM_CSV.items() if src in r}
                rows_by_week.setdefault((int(r["Year"]), int(r["CalendarWeek"])), []).append(row)
            _import_weeks(rows_by_week, lambda y, w, rows: repo.save_pm_status_week(ctx.engine, y, w, rows))


# ---------- CSV helpers ----------

def _read_csv_upload(key: str, required: list[str]) -> pd.DataFrame | None:
    upload = st.file_uploader("CSV file", type=["csv"], key=key)
    if upload is None:
        return None
    df = pd.read_csv(upload, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Missing columns: {', '.join(missing)}")
        return None
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce").astype("Int64")
    df["CalendarWeek"] = pd.to_numeric(df["CalendarWeek"], errors="coerce").astype("Int64")
    return df


def _import_weeks(rows_by_week: dict, save) -> None:
    imported, skipped = 0, []
    for (y, w), rows in rows_by_week.items():
        try:
            imported += save(y, w, rows)
        except (PermissionError, ValueError) as exc:
            skipped.append(str(exc))
    note = f" Skipped: {'; '.join(skipped)}" if skipped else ""
    _done(f"Imported {imported} row(s) from CSV.{note}", warning=bool(skipped))
