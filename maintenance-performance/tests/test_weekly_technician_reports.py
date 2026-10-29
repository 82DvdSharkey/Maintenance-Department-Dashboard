from dataclasses import replace
from datetime import date, datetime, timedelta
from io import BytesIO

import pandas as pd
import pytest
from pypdf import PdfReader

from database import repository as repo
from database.database import create_db_engine, get_app_settings, init_db
from database.models import DayforceHours, FiixWorkOrder, IgnitionCall, PMAreaStatus, Technician
from kpi.dataset import FilterState, apply_filters, prepare_base
from kpi.definitions import KPI_DEFS, TECHNICIAN_KPIS
from kpi.ignition_metrics import summarize_calls
from kpi.scorecard import technician_scorecard
from kpi.targets import evaluate_status
from reporting.technician_pdf import build_technician_pdf
from reporting.technician_review import build_technician_review, build_technician_review_pdf
from reporting.shift_review import build_shift_review, build_shift_review_pdf
from reporting.weekly_technician import (
    LOCAL_ZONE,
    build_weekly_technician_report,
    most_recent_completed_week,
)
from pages.executive_dashboard import _context_for_ff_group, _labor_context_for_ff_group
from pages.technician_dashboard import period_snapshot, year_to_date_context
from ui.context import DashboardContext
from utils.dates import week_bounds

NOW = datetime(2026, 10, 19, 8, tzinfo=LOCAL_ZONE)


@pytest.fixture
def report_context(tmp_path):
    engine = create_db_engine(tmp_path / "reports.db")
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(Technician.__table__.insert(), [
            {"technician_id": 1, "technician_name": "Alice Example", "shift": "1st Shift",
             "crew": "Assembly", "active": True},
            {"technician_id": 2, "technician_name": "Bob Confidential", "shift": "1st Shift",
             "crew": "Assembly", "active": True},
        ])
        for week in range(37, 41):
            start, _ = week_bounds(2026, week)
            stamp = start + timedelta(days=1, hours=9)
            for tech, name, hours in ((1, "Alice Example", 30.0), (2, "Bob Confidential", 2.0)):
                for index, wo_type in enumerate(("Reactive", "Preventative (PM)")):
                    conn.execute(FiixWorkOrder.__table__.insert().values(
                        wo=f"{tech}-{week}-{index}", tl=f"{tech}{week}{index}", asset=f"Asset {tech}",
                        parent_asset="Line", completed_by=name, completed_by_key=name.casefold(),
                        date_created=stamp, date_completed=stamp + timedelta(hours=1),
                        status="Closed", wo_type=wo_type, time_spent_hours=hours / 2,
                        task_description=f"{name} private task", week_key=202600 + week,
                        year=2026, calendar_week=week))
                conn.execute(DayforceHours.__table__.insert().values(
                    technician_id=tech, year=2026, calendar_week=week,
                    week_key=202600 + week, total_hours_worked=40.0))
                conn.execute(IgnitionCall.__table__.insert().values(
                    id=tech * 100 + week, tech_name=name, tech_key=name.casefold(),
                    call_type_norm="maintenance", call_time=stamp, year=2026, calendar_week=week,
                    week_key=202600 + week, ign_area_name=f"Area {tech}",
                    call_duration_min=25.0 * tech, ack_duration_min=2.0 * tech,
                    response_duration_min=3.0 * tech, repair_duration_min=20.0 * tech))
            conn.execute(PMAreaStatus.__table__.insert().values(
                year=2026, calendar_week=week, week_key=202600 + week,
                area="Final Assembly", pm_open=1000.0, pm_closed=999.0))
        start, _ = week_bounds(2026, 40)
        conn.execute(FiixWorkOrder.__table__.insert().values(
            wo="REPEAT-LINK", tl="1400", asset="Bob Secret Repeat Asset",
            completed_by="Bob Confidential", completed_by_key="bob confidential",
            date_created=start + timedelta(days=2), date_completed=start + timedelta(days=2, hours=1),
            status="Closed", wo_type="Reactive", time_spent_hours=1.0,
            task_description="Bob secret repeat task", year=2026, calendar_week=40, week_key=202640))
    base = prepare_base(
        fiix=repo.load_fiix(engine), calls=repo.load_maintenance_calls(engine),
        dayforce=repo.load_dayforce(engine), pm_status=repo.load_pm_status(engine),
        technicians=repo.load_technicians(engine), aliases=repo.load_aliases(engine),
        shifts=repo.load_shifts(engine), wo_types=repo.load_wo_types(engine),
        app_settings=get_app_settings(engine), dayforce_entries=repo.load_dayforce_entries(engine))
    targets = {r["kpi_key"]: r for r in repo.load_kpi_targets(engine).to_dict("records")}
    state = FilterState(week_keys=[202640])
    ctx = DashboardContext(engine, {"week_start_offset_hours": 1}, base, state,
                           apply_filters(base, state), targets, 1)
    yield ctx
    engine.dispose()


def _report(ctx, **kwargs):
    return build_weekly_technician_report(ctx.base, 1, 202640, ctx.targets, now=NOW, **kwargs)


def _assert_value(actual, expected):
    if expected is None or pd.isna(expected):
        assert actual is None or pd.isna(actual)
    else:
        assert actual == pytest.approx(expected)


def test_all_report_kpis_match_dashboard_current_previous_and_rolling(report_context):
    ctx = report_context
    report = _report(ctx)
    dashboard = ctx.trend(weeks=12, technician_ids=[1])
    for key, row in zip(TECHNICIAN_KPIS, report.metrics.to_dict("records")):
        definition = KPI_DEFS[key]
        _assert_value(row["Current Week"], dashboard.iloc[-1][definition.column])
        _assert_value(row["Previous Week"], dashboard.iloc[-2][definition.column])
        _assert_value(row["4-Week Average"], dashboard.iloc[-1][f"{definition.column}_r4"])
        target = ctx.targets.get(key, {})
        assert row["Status"] == evaluate_status(row["Current Week"], target.get("target"),
                                                definition.direction, target.get("watch_band"))
    scorecard = technician_scorecard(ctx.inputs_for(technician_ids=[1]), ctx.base.technicians).iloc[0]
    _assert_value(report.workload.iloc[0]["Value"], scorecard["worked_hours"])
    _assert_value(report.workload.iloc[1]["Value"], scorecard["logged_hours"])
    assert len(report.trend) == 12
    assert report.trend["week_key"].max() == 202640
    assert "pm_completion_pct" not in report.trend


def test_individual_pdf_is_three_pages_and_contains_required_sections(report_context):
    report = _report(report_context)
    pdf = build_technician_pdf(report)
    assert pdf.startswith(b"%PDF-")
    pages = PdfReader(BytesIO(pdf)).pages
    assert 2 <= len(pages) <= 4
    text = "\n".join(page.extract_text() for page in pages)
    for required in ("Alice Example", "2026-CW40", "Weekly KPI Scorecard", "Previous Week",
                     "4-Week Average", "75.0%", "Fiix Labor Breakdown", "First-Time Fix",
                     "Ignition Performance", "12-Week Technician Performance History",
                     "PM Assigned", "N/A"):
        assert required in text
    assert "Page 3" in text
    assert report.filename == "Technician_Performance_Alice_Example_CW40_2026.pdf"


def test_no_other_technician_details_or_department_ranking_in_pdf(report_context):
    report = _report(report_context)
    pdf = build_technician_pdf(report)
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    for forbidden in ("Bob Confidential", "Bob Secret Repeat Asset", "Bob secret repeat task",
                      "Area 2", "ranking", "2-40-0", "2-40-1"):
        assert forbidden not in text
    assert "REPEAT-LINK" in text  # The repeat WO identifier is required, not its technician's details.
    assert report.repeats.iloc[0]["wo"] == "1-40-0"
    assert report.repeats.iloc[0]["tl"] == "1400"
    assert report.repeats.iloc[0]["days_to_repeat"] > 0
    assert list(report.repeats.columns) == ["wo", "tl", "asset", "repeat_wo", "days_to_repeat"]
    assert report.areas["ign_area_name"].tolist() == ["Area 1"]


def test_report_areas_and_ftf_use_existing_calculations(report_context):
    ctx = report_context
    inputs = ctx.inputs_for(technician_ids=[1])
    report = _report(ctx)
    expected = summarize_calls(inputs.calls, ["ign_area_name"])
    pd.testing.assert_frame_equal(report.areas.reset_index(drop=True), expected.reset_index(drop=True))
    assert report.first_time_fix.iloc[0]["Value"] == len(inputs.ftf)
    assert report.first_time_fix.iloc[1]["Value"] == inputs.ftf["ftf_success"].sum()
    assert report.first_time_fix.iloc[2]["Value"] == inputs.ftf["repeat_failure"].sum()


def test_report_ignores_unrelated_sidebar_filters(report_context):
    ctx = report_context
    ctx.filters = replace(ctx.filters, technician_ids=[2], shifts=["2nd Shift"],
                          areas=["Area 2"], wo_types=["Training"])
    report = _report(ctx)
    assert report.technician_id == 1
    assert report.workload.iloc[1]["Value"] == 30.0
    assert report.areas["ign_area_name"].tolist() == ["Area 1"]


def test_partial_week_is_blocked_unless_explicitly_allowed(report_context):
    now = datetime(2026, 9, 30, 8, tzinfo=LOCAL_ZONE)
    with pytest.raises(ValueError, match="not complete"):
        build_weekly_technician_report(report_context.base, 1, 202640, report_context.targets, now=now)
    report = build_weekly_technician_report(report_context.base, 1, 202640, report_context.targets,
                                           now=now, allow_partial=True)
    assert any("PARTIAL WEEK" in text for text in report.notices)


def test_empty_technician_week_has_explicit_missing_data_notices(report_context):
    ctx = report_context
    report = build_weekly_technician_report(ctx.base, 1, 202630, ctx.targets, now=NOW)
    assert sum(text.startswith("No ") for text in report.notices) == 3
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(build_technician_pdf(report))).pages)
    assert "No Dayforce data" in text
    assert "N/A" in text
    assert len(PdfReader(BytesIO(build_technician_pdf(report))).pages) <= 4


def test_report_rejects_unknown_technician_and_invalid_week(report_context):
    ctx = report_context
    with pytest.raises(ValueError, match="technician"):
        build_weekly_technician_report(ctx.base, 999, 202640, ctx.targets, now=NOW)
    with pytest.raises(ValueError):
        build_weekly_technician_report(ctx.base, 1, 202654, ctx.targets, now=NOW)


@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 9, 27, 22, 59, tzinfo=LOCAL_ZONE), 202638),
    (datetime(2026, 9, 27, 23, 0, tzinfo=LOCAL_ZONE), 202639),
    (datetime(2026, 10, 19, 8, tzinfo=LOCAL_ZONE), 202642),
    (datetime(2021, 1, 4, 8, tzinfo=LOCAL_ZONE), 202053),
])
def test_completed_week_uses_existing_production_calendar(now, expected):
    assert most_recent_completed_week(now=now) == expected


def test_custom_offset_is_used(report_context):
    report = _report(report_context, offset_hours=2)
    assert report.period_start == datetime(2026, 9, 27, 22)
    assert report.period_end == datetime(2026, 10, 4, 22)


def test_detail_limits_are_explicit_and_pdf_remains_compact(report_context):
    report = _report(report_context)
    report = replace(report, repeats=pd.concat([report.repeats] * 40, ignore_index=True),
                     areas=pd.concat([report.areas] * 40, ignore_index=True))
    pages = PdfReader(BytesIO(build_technician_pdf(report))).pages
    text = "\n".join(page.extract_text() for page in pages)
    assert "Showing 5 of 40 rows" in text
    assert "Showing 6 of 40 rows" in text
    assert len(pages) <= 4


def test_preview_and_download_use_identical_pdf_and_selection_clears_cache(report_context):
    from unittest.mock import patch

    import streamlit as st
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "import streamlit as st\n"
        "from ui.technician_reports import render_technician_reports\n"
        "render_technician_reports(st.session_state['report_test_context'])\n")
    app.session_state["report_test_context"] = report_context
    app.run()
    assert not app.exception
    app.selectbox(key="weekly_report_week").select(202640).run()
    with patch("ui.technician_reports.st.download_button", wraps=st.download_button) as download_call, \
            patch("ui.technician_reports.st.pdf", wraps=st.pdf) as preview_call:
        app.button(key="weekly_report_preview").click().run()
        assert preview_call.call_args.args[0] == download_call.call_args.args[1]
    assert not app.exception
    preview = app.session_state["_weekly_technician_pdf"]
    assert preview[3] is True
    assert preview[2].startswith(b"%PDF-")
    download = app.get("download_button")[0]
    assert download.proto.url
    assert "Alice_Example" in preview[1]
    app.selectbox(key="weekly_report_technician").select(2).run()
    assert "_weekly_technician_pdf" not in app.session_state
    assert not app.get("download_button")
    app.button(key="weekly_report_generate").click().run()
    assert not app.exception
    generated = app.session_state["_weekly_technician_pdf"]
    assert generated[3] is False
    assert "Bob_Confidential" in generated[1]
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(generated[2])).pages)
    assert "Alice Example" not in text


def test_report_ui_invalidates_download_when_source_data_changes(report_context):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "import streamlit as st\n"
        "from ui.technician_reports import render_technician_reports\n"
        "render_technician_reports(st.session_state['report_test_context'])\n")
    app.session_state["report_test_context"] = report_context
    app.run()
    app.button(key="weekly_report_generate").click().run()
    assert not app.exception
    assert "_weekly_technician_pdf" in app.session_state
    report_context.version += 1
    app.run()
    assert "_weekly_technician_pdf" not in app.session_state
    assert not app.get("download_button")


def test_peer_share_is_shared_by_report_dashboard_and_export_scorecard(report_context):
    ctx = report_context
    report = _report(ctx)
    assert report.pm_peer_share == pytest.approx(50.0)
    assert report.crew == "Assembly"
    current = ctx.trend(technician_ids=[1]).iloc[-1]
    assert current["pm_peer_share_pct"] == pytest.approx(50.0)
    assert current["pm_peer_share_pct_r4"] == pytest.approx(50.0)
    scorecard = ctx.technician_scorecard().set_index("technician_id")
    assert scorecard.loc[1, "pm_peer_share_pct"] == pytest.approx(50.0)
    # Person filters must not make the selected technician's share become 100%.
    ctx.filters = replace(ctx.filters, technician_ids=[1])
    ctx.inputs = apply_filters(ctx.base, ctx.filters)
    assert ctx.technician_scorecard().iloc[0]["pm_peer_share_pct"] == pytest.approx(50.0)
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(build_technician_pdf(report))).pages)
    assert "PM Completion Share" in text
    assert "50.0%" in text
    assert "Bob Confidential" not in text


def test_peer_share_rolling_is_pooled_not_mean_of_percentages(report_context):
    ctx = report_context
    # Only Alice has a PM in CW40; prior weeks both technicians have one.
    base = ctx.base
    base.fiix = base.fiix[base.fiix["wo"] != "2-40-1"].copy()
    trend = ctx.trend(technician_ids=[1])
    assert trend.iloc[-1]["pm_peer_share_pct"] == pytest.approx(100.0)
    assert trend.iloc[-1]["pm_peer_share_pct_r4"] == pytest.approx(4 / 7 * 100)
    assert _report(ctx).pm_peer_share == pytest.approx(100.0)


def test_report_explains_missing_crew_and_no_peers(report_context):
    ctx = report_context
    ctx.base.technicians.loc[ctx.base.technicians["technician_id"] == 1, "crew"] = None
    report = _report(ctx)
    assert pd.isna(report.pm_peer_share)
    assert any("shift and crew must both be assigned" in text for text in report.notices)
    ctx.base.technicians.loc[ctx.base.technicians["technician_id"] == 1, "crew"] = "Different"
    report = _report(ctx)
    assert pd.isna(report.pm_peer_share)
    assert any("no other active" in text for text in report.notices)


@pytest.mark.parametrize(
    ("selection", "expected_group"),
    [("FA", "FA"), ("SMT", "SMT"), ("Post SMT", "POST SMT")],
)
def test_executive_ff_group_filter_uses_exact_group_and_overrides_sidebar(
    report_context, selection, expected_group
):
    ctx = report_context
    groups = ["FA", "FA1", "FA2", "SMT", "POST SMT"]
    ctx.base.fiix["ff_group"] = [groups[i % len(groups)] for i in range(len(ctx.base.fiix))]
    group_by_wo = ctx.base.fiix.set_index("wo")["ff_group"]
    ctx.base.ftf_eval["ff_group"] = ctx.base.ftf_eval["wo"].map(group_by_wo)
    ctx.filters = replace(ctx.filters, ff_groups=["FA1"])

    filtered = _context_for_ff_group(ctx, selection)

    assert filtered.filters.ff_groups == [expected_group]
    assert set(filtered.inputs.fiix["ff_group"]) == {expected_group}
    assert ctx.filters.ff_groups == ["FA1"]


def test_executive_all_ff_groups_ignores_sidebar_group_filter(report_context):
    ctx = report_context
    groups = ["FA", "FA1", "FA2", "SMT", "POST SMT"]
    ctx.base.fiix["ff_group"] = [groups[i % len(groups)] for i in range(len(ctx.base.fiix))]
    group_by_wo = ctx.base.fiix.set_index("wo")["ff_group"]
    ctx.base.ftf_eval["ff_group"] = ctx.base.ftf_eval["wo"].map(group_by_wo)
    ctx.filters = replace(ctx.filters, ff_groups=["FA1"])

    filtered = _context_for_ff_group(ctx, "All FF Groups")

    assert filtered.filters.ff_groups == []
    assert set(filtered.inputs.fiix["ff_group"]) == set(groups)


def test_executive_fa_labor_efficiency_uses_final_assembly_crew_across_ff_groups(report_context):
    ctx = report_context
    ctx.base.technicians.loc[ctx.base.technicians["technician_id"] == 1, "crew"] = "Final Assembly"
    ctx.base.technicians.loc[ctx.base.technicians["technician_id"] == 2, "crew"] = "SMT"
    ctx.base.fiix["ff_group"] = ctx.base.fiix["technician_id"].map({1: "FA1", 2: "FA"})
    group_by_wo = ctx.base.fiix.set_index("wo")["ff_group"]
    ctx.base.ftf_eval["ff_group"] = ctx.base.ftf_eval["wo"].map(group_by_wo)
    fa_group_context = _context_for_ff_group(ctx, "FA")

    labor_context = _labor_context_for_ff_group(fa_group_context, "FA")
    summary = labor_context.department_summary()

    assert set(fa_group_context.inputs.fiix["technician_id"].dropna().astype(int)) == {2}
    assert labor_context.filters.ff_groups == []
    assert labor_context.filters.technician_ids == [1]
    assert set(labor_context.inputs.labor_rows["technician_id"].dropna().astype(int)) == {1}
    assert summary["logged_hours"] == pytest.approx(30.0)
    assert summary["worked_hours"] == pytest.approx(40.0)
    assert summary["labor_efficiency_pct"] == pytest.approx(75.0)


@pytest.mark.parametrize("selection", ["SMT", "Post SMT"])
def test_executive_smt_labor_efficiency_uses_smt_crew_across_both_ff_groups(report_context, selection):
    ctx = report_context
    ctx.base.technicians.loc[ctx.base.technicians["technician_id"] == 1, "crew"] = "Final Assembly"
    ctx.base.technicians.loc[ctx.base.technicians["technician_id"] == 2, "crew"] = "Team Lead SMT"
    tech2_rows = ctx.base.fiix["technician_id"].eq(2)
    ctx.base.fiix["ff_group"] = "FA"
    ctx.base.fiix.loc[tech2_rows, "ff_group"] = [
        "SMT" if index % 2 == 0 else "POST SMT"
        for index in range(int(tech2_rows.sum()))
    ]
    group_by_wo = ctx.base.fiix.set_index("wo")["ff_group"]
    ctx.base.ftf_eval["ff_group"] = ctx.base.ftf_eval["wo"].map(group_by_wo)
    filtered = _context_for_ff_group(ctx, selection)

    labor_context = _labor_context_for_ff_group(filtered, selection)
    summary = labor_context.department_summary()

    assert set(filtered.inputs.fiix["ff_group"]) == {filtered.filters.ff_groups[0]}
    assert labor_context.filters.ff_groups == []
    assert labor_context.filters.technician_ids == [2]
    assert set(labor_context.inputs.labor_rows["ff_group"]) == {"SMT", "POST SMT"}
    assert summary["logged_hours"] == pytest.approx(3.0)
    assert summary["worked_hours"] == pytest.approx(40.0)
    assert summary["labor_efficiency_pct"] == pytest.approx(7.5)


def test_all_ff_groups_labor_efficiency_keeps_department_scope(report_context):
    ctx = _context_for_ff_group(report_context, "All FF Groups")

    labor_context = _labor_context_for_ff_group(ctx, "All FF Groups")

    assert labor_context is ctx


def test_multi_week_technician_cards_use_period_values_not_idle_final_week(report_context):
    ctx = report_context
    base = ctx.base
    idle_week = 202640
    base.fiix = base.fiix[~((base.fiix["technician_id"] == 1) & (base.fiix["week_key"] == idle_week))].copy()
    base.calls = base.calls[~((base.calls["technician_id"] == 1) & (base.calls["week_key"] == idle_week))].copy()
    base.dayforce = base.dayforce[~((base.dayforce["technician_id"] == 1)
                                    & (base.dayforce["week_key"] == idle_week))].copy()
    ctx.filters = FilterState(week_keys=[202637, 202638, 202639, idle_week], label="range")
    ctx.inputs = apply_filters(base, ctx.filters)
    defs = [KPI_DEFS[k] for k in TECHNICIAN_KPIS]

    trend = ctx.trend(technician_ids=[1])
    snapshot = period_snapshot(ctx.technician_scorecard(), 1, trend.iloc[-1], defs)

    assert pd.isna(trend.iloc[-1]["labor_efficiency_pct"])
    assert snapshot["labor_efficiency_pct"] == pytest.approx(75.0)
    assert snapshot["call_peer_share_pct"] == pytest.approx(3 / 7 * 100)
    assert ctx.technician_scorecard().set_index("technician_id").loc[1, "maintenance_calls"] == 3
    assert snapshot["pm_completed"] == pytest.approx(3.0)
    assert pd.isna(snapshot["labor_efficiency_pct_r4"])


def test_period_snapshot_is_blank_only_when_technician_has_no_period_row(report_context):
    ctx = report_context
    defs = [KPI_DEFS[k] for k in TECHNICIAN_KPIS]
    template = ctx.trend(technician_ids=[1]).iloc[-1]

    snapshot = period_snapshot(ctx.technician_scorecard().iloc[0:0], 1, template, defs)

    assert snapshot[[d.column for d in defs]].isna().all()


def test_year_to_date_context_spans_week_one_through_end_week_and_keeps_filters(report_context):
    ctx = report_context
    ctx.filters = FilterState(week_keys=[202639, 202640], shifts=["1st Shift"], technician_ids=[1], label="range")

    ytd = year_to_date_context(ctx)

    assert ytd.filters.week_keys[0] == 202601
    assert ytd.filters.week_keys[-1] == 202640
    assert len(ytd.filters.week_keys) == 40
    assert ytd.filters.shifts == ["1st Shift"] and ytd.filters.technician_ids == [1]
    assert ytd.filters.date_start is None
    assert "year to date" in ytd.filters.label
    assert set(ytd.inputs.fiix["week_key"].dropna().astype(int)) >= {202637, 202640}
    assert ctx.filters.week_keys == [202639, 202640]


def test_technician_pm_completion_share_has_33_percent_target():
    from database.database import DEFAULT_KPI_TARGETS
    from kpi.definitions import KPI_DEFS

    assert DEFAULT_KPI_TARGETS['pm_peer_share'][1] == 33.0
    assert KPI_DEFS['pm_peer_share'].direction == 'higher'


def test_call_share_target_is_seeded_and_shared_by_dashboard_report_and_pdf(report_context):
    ctx = report_context
    assert ctx.targets["call_peer_share"]["target"] == 33.0
    assert KPI_DEFS["call_peer_share"].direction == "higher"
    assert ctx.trend(technician_ids=[1]).iloc[-1]["call_peer_share_pct"] == pytest.approx(50)
    assert ctx.technician_scorecard().iloc[0]["call_peer_share_pct"] == pytest.approx(50)
    report = _report(ctx)
    metric = report.metrics.set_index("KPI").loc[KPI_DEFS["call_peer_share"].label]
    assert metric["Current Week"] == pytest.approx(50)
    assert metric["Target"] == 33
    assert metric["Status"] == "On Target"
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(build_technician_pdf(report))).pages)
    assert "Maintenance Call Share" in text
    assert "Bob Confidential" not in text


def test_call_share_person_filters_keep_peers_and_area_filters_apply_to_both_counts(report_context):
    ctx = report_context
    ctx.filters = replace(ctx.filters, technician_ids=[1], shifts=["1st Shift"])
    ctx.inputs = apply_filters(ctx.base, ctx.filters)
    assert ctx.technician_scorecard().iloc[0]["call_peer_share_pct"] == pytest.approx(50)
    ctx.filters = replace(ctx.filters, areas=["Area 2"])
    ctx.inputs = apply_filters(ctx.base, ctx.filters)
    assert ctx.technician_scorecard().iloc[0]["call_peer_share_pct"] == 0
    assert ctx.trend(technician_ids=[1]).iloc[-1]["call_peer_share_pct"] == 0
    ctx.filters = replace(ctx.filters, areas=["Missing area"])
    ctx.inputs = apply_filters(ctx.base, ctx.filters)
    row = ctx.technician_scorecard().iloc[0]
    assert pd.isna(row["call_peer_share_pct"])
    assert "no maintenance calls" in row["call_peer_reason"]


def test_call_share_rolling_and_ytd_pool_counts_not_percentages(report_context):
    ctx = report_context
    ctx.base.calls = ctx.base.calls[~ctx.base.calls["id"].eq(240)].copy()
    current = ctx.trend(technician_ids=[1]).iloc[-1]
    assert current["call_peer_share_pct"] == pytest.approx(100)
    assert current["call_peer_share_pct_r4"] == pytest.approx(4 / 7 * 100)
    ctx = year_to_date_context(ctx)
    row = ctx.technician_scorecard().iloc[0]
    assert row["call_peer_share_pct"] == pytest.approx(4 / 7 * 100)
    snapshot = period_snapshot(ctx.technician_scorecard(), 1, current,
                               [KPI_DEFS[k] for k in TECHNICIAN_KPIS])
    assert snapshot["call_peer_share_pct"] == pytest.approx(4 / 7 * 100)
    assert _report(ctx).metrics.set_index("KPI").loc[
        KPI_DEFS["call_peer_share"].label, "4-Week Average"] == pytest.approx(4 / 7 * 100)


def test_call_share_date_range_uses_only_calls_inside_range(report_context):
    ctx = report_context
    start, _ = week_bounds(2026, 40)
    ctx.filters = replace(ctx.filters, week_keys=[202639, 202640], date_start=start,
                          date_end=start + timedelta(days=3))
    ctx.inputs = apply_filters(ctx.base, ctx.filters)
    row = ctx.technician_scorecard().iloc[0]
    assert row["call_peer_own_completed"] == 1
    assert row["call_peer_group_completed"] == 2
    assert row["call_peer_share_pct"] == pytest.approx(50)


def test_technician_page_renders_call_share_with_target_in_week_and_ytd_modes(report_context):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "import streamlit as st\n"
        "from pages.technician_dashboard import render\n"
        "render(st.session_state['test_context'])\n")
    app.session_state["test_context"] = report_context
    app.run(timeout=30)
    assert not app.exception
    metric = next(m for m in app.metric if m.label == KPI_DEFS["call_peer_share"].label)
    assert metric.value == "50.0%"
    assert not any(m.label == "Maintenance Calls" for m in app.metric)
    assert any("Target: 33.0%" in c.value for c in app.caption)
    app.toggle(key="tech_ytd").set_value(True).run(timeout=30)
    assert not app.exception
    metric = next(m for m in app.metric if m.label == KPI_DEFS["call_peer_share"].label)
    assert metric.value == "50.0%"
    assert not any(m.label == "Maintenance Calls" for m in app.metric)


def test_target_reload_seeds_missing_defaults_with_cached_engine_and_preserves_edits(report_context):
    from sqlalchemy import delete, update

    from database.models import KPITarget
    from ui.context import kpi_targets_dict

    engine = report_context.engine
    with engine.begin() as conn:
        conn.execute(delete(KPITarget).where(KPITarget.kpi_key == "call_peer_share"))
        conn.execute(update(KPITarget).where(KPITarget.kpi_key == "pm_peer_share").values(target=42.0))
    targets = kpi_targets_dict(engine)
    assert targets["call_peer_share"]["target"] == 33.0
    assert targets["pm_peer_share"]["target"] == 42.0
    saved = repo.load_kpi_targets(engine).set_index("kpi_key")
    assert saved.loc["call_peer_share", "target"] == 33.0
    assert kpi_targets_dict(engine) == targets


def _review(ctx, **kwargs):
    return build_technician_review(ctx.base, 1, 2026, ctx.targets, as_of=date(2026, 10, 27), **kwargs)


def test_review_pools_quarters_and_ytd_and_keeps_only_selected_individual(report_context):
    ctx = report_context
    report = _review(ctx)
    metrics = report.metrics.set_index("KPI")
    assert list(report.periods["Period"]) == ["Q1", "Q2", "Q3", "Q4", "YTD"]
    assert metrics.loc["Labor Efficiency", "Q3"] == pytest.approx(75)
    assert metrics.loc["Labor Efficiency", "YTD"] == pytest.approx(75)
    assert metrics.loc["PMs Closed", "YTD"] == 4
    assert metrics.loc[KPI_DEFS["call_peer_share"].label, "YTD"] == pytest.approx(50)
    assert metrics.loc[KPI_DEFS["call_peer_share"].label, "Target"] == 33
    assert metrics.loc[KPI_DEFS["call_peer_share"].label, "YTD Status"] == "On Target"
    assert metrics.loc["MTTR", "YTD"] == pytest.approx(20)
    assert report.periods.set_index("Period").loc["Q4", "Calendar status"] == "Partial - through cutoff"
    assert report.periods.set_index("Period").loc["YTD", "Through date (inclusive)"] == "2026-10-27"
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(build_technician_review_pdf(report))).pages)
    for required in ("Alice Example", "Q1", "Q2", "Q3", "Q4", "YTD", "33.0%", "75.0%",
                     "full weekly totals", "2026-10-27"):
        assert required in text
    for forbidden in ("Bob Confidential", "Bob Secret Repeat Asset", "Area 2", "REPEAT-LINK",
                      "Technician scorecard", "by Technician", "Recent reactive"):
        assert forbidden not in text
    assert report.filename == "Technician_Review_Alice_Example_2026_as_of_2026-10-27.pdf"


def test_review_ignores_sidebar_filters_and_uses_actual_selected_subject(report_context):
    ctx = report_context
    ctx.filters = replace(ctx.filters, technician_ids=[2], shifts=["Missing"], areas=["Missing"],
                          ff_groups=["Missing"], wo_types=["Training"])
    ctx.inputs = apply_filters(ctx.base, ctx.filters)
    report = _review(ctx)
    assert report.technician_name == "Alice Example"
    assert report.metrics.set_index("KPI").loc["Labor Efficiency", "YTD"] == pytest.approx(75)
    other = build_technician_review(ctx.base, 2, 2026, ctx.targets, as_of=date(2026, 10, 27))
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(build_technician_review_pdf(other))).pages)
    assert "Bob Confidential" in text
    assert "Alice Example" not in text


def test_review_exact_calendar_boundaries_exclude_outside_year_and_after_today(report_context):
    ctx = report_context
    base = ctx.base
    own = base.calls["technician_id"].eq(1)
    stamps = pd.to_datetime(["2025-12-31 23:59:59", "2026-03-31 23:59:59",
                             "2026-04-01 00:00:00", "2026-10-28 00:00:00"])
    base.calls.loc[own, "call_time"] = stamps
    base.calls.loc[own, "repair_duration_min"] = [999, 10, 30, 999]
    metrics = _review(ctx).metrics.set_index("KPI")
    assert metrics.loc["MTTR", "Q1"] == 10
    assert metrics.loc["MTTR", "Q2"] == 30
    assert metrics.loc["MTTR", "YTD"] == 20
    assert pd.isna(metrics.loc["MTTR", "Q4"])
    # Fiix uses completion dates, not the source week or creation date.
    pm = base.fiix["wo"].eq("1-37-1")
    base.fiix.loc[pm, "date_completed"] = datetime(2026, 4, 1)
    metrics = _review(ctx).metrics.set_index("KPI")
    assert metrics.loc["PMs Closed", "Q2"] == 1
    assert metrics.loc["PMs Closed", "Q3"] == 3
    assert metrics.loc["PMs Closed", "YTD"] == 4


def test_review_future_quarters_are_not_started_and_missing_kpis_are_na(report_context):
    ctx = report_context
    report = build_technician_review(ctx.base, 1, 2026, ctx.targets, as_of=date(2026, 2, 15))
    periods = report.periods.set_index("Period")
    assert periods.loc["Q1", "Calendar status"] == "Partial - through cutoff"
    assert periods.loc["Q2", "Calendar status"] == "Not started"
    assert report.metrics[["Q2", "Q3", "Q4"]].isna().all().all()
    assert any("no Dayforce hours" in n for n in report.notices)
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(build_technician_review_pdf(report))).pages)
    assert "Not started" in text
    assert "N/A" in text


def test_review_calendar_boundary_includes_full_overlapping_week_hours(report_context):
    ctx = report_context
    # CW40 overlaps September and October. Whole 40-hour week belongs to each overlapping period.
    report = _review(ctx)
    metrics = report.metrics.set_index("KPI")
    assert metrics.loc["Labor Efficiency", "Q3"] == pytest.approx(120 / 160 * 100)
    assert metrics.loc["Labor Efficiency", "Q4"] == 0
    assert metrics.loc["Labor Efficiency", "YTD"] == pytest.approx(120 / 160 * 100)
    assert any("Quarter hours can overlap" in n for n in report.notices)


def test_review_export_ui_invalidates_pdf_when_subject_year_targets_or_data_change(report_context):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "import streamlit as st\n"
        "from ui.technician_reports import render_review_download\n"
        "render_review_download(st.session_state['ctx'], st.session_state['tech'], "
        "st.session_state['year'], st.container())\n")
    app.session_state["ctx"] = report_context
    app.session_state["tech"] = 1
    app.session_state["year"] = 2026
    for change in ("tech", "year", "target", "version"):
        app.run()
        app.button(key="pdf_technician_prepare").click().run(timeout=30)
        assert not app.exception
        assert "_technician_review_pdf" in app.session_state
        if change == "tech":
            app.session_state["tech"] = 2
        elif change == "year":
            app.session_state["year"] = 2025
        elif change == "target":
            report_context.targets["call_peer_share"]["target"] = 40
        else:
            report_context.version += 1
        app.run()
        assert not app.exception
        assert "_technician_review_pdf" not in app.session_state
        assert not app.get("download_button")


def test_review_completed_past_year_and_invalid_technician(report_context):
    report = build_technician_review(report_context.base, 1, 2025, report_context.targets,
                                     as_of=date(2026, 10, 27))
    assert report.periods["Calendar status"].eq("Complete").all()
    assert report.periods.iloc[-1]["Through date (inclusive)"] == "2025-12-31"
    with pytest.raises(ValueError, match="existing, unique technician"):
        build_technician_review(report_context.base, -1, 2026, report_context.targets)


def test_review_ytd_recomputes_from_underlying_counts_not_quarter_percentages(report_context):
    ctx = report_context
    own = ctx.base.calls["technician_id"].eq(1)
    peer = ctx.base.calls["technician_id"].eq(2)
    ctx.base.calls.loc[own, "call_time"] = pd.to_datetime(
        ["2026-03-01", "2026-06-01", "2026-06-01", "2026-06-01"])
    ctx.base.calls.loc[peer, "call_time"] = pd.to_datetime(
        ["2026-03-01", "2026-03-01", "2026-03-01", "2026-03-01"])
    ctx.base.calls.loc[own, "repair_duration_min"] = [100, 20, 20, 20]
    metrics = _review(ctx).metrics.set_index("KPI")
    share = metrics.loc[KPI_DEFS["call_peer_share"].label]
    assert share["Q1"] == 20
    assert share["Q2"] == 100
    assert share["YTD"] == 50
    assert metrics.loc["MTTR", "Q1"] == 100
    assert metrics.loc["MTTR", "Q2"] == 20
    assert metrics.loc["MTTR", "YTD"] == 40


def test_technician_page_export_contains_detail_subject_not_all_scorecard_rows(report_context):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "import streamlit as st\n"
        "from pages.technician_dashboard import render\n"
        "render(st.session_state['ctx'])\n")
    app.session_state["ctx"] = report_context
    app.run(timeout=30)
    app.selectbox(key="tech_detail").set_value(2).run(timeout=30)
    app.button(key="pdf_technician_prepare").click().run(timeout=30)
    assert not app.exception
    cached = app.session_state["_technician_review_pdf"]
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(cached[2])).pages)
    assert "Bob Confidential" in text
    assert "Alice Example" not in text
    assert "Quarterly and year-to-date performance KPIs" in text
    assert "Technician scorecard" not in text


def _shift_review(ctx, shift="1st Shift", area="All areas", year=2026, as_of=date(2026, 10, 27)):
    return build_shift_review(ctx.base, shift, area, year, ctx.targets, as_of=as_of)


def test_shift_review_team_matches_existing_shift_kpis_and_individual_reviews(report_context):
    from kpi.scorecard import shift_scorecard

    ctx = report_context
    report = _shift_review(ctx)
    year_state = FilterState(week_keys=list(range(202601, 202645)),
                             date_start=datetime(2026, 1, 1), date_end=datetime(2026, 10, 28),
                             shifts=["1st Shift"])
    expected = shift_scorecard(apply_filters(ctx.base, year_state), ctx.base.technicians).iloc[0]
    metrics = report.metrics.set_index("KPI")
    from kpi.definitions import PEOPLE_KPIS
    for key in PEOPLE_KPIS:
        definition = KPI_DEFS[key]
        _assert_value(metrics.loc[definition.label, "YTD"], expected[definition.column])
    assert metrics.loc["Labor Efficiency", "YTD"] == pytest.approx(129 / 320 * 100)
    assert metrics.loc["PMs Closed", "YTD"] == 8
    assert metrics.loc["Maintenance Calls", "YTD"] == 8
    pd.testing.assert_frame_equal(report.individuals[1], _review(ctx).metrics)
    other = build_technician_review(ctx.base, 2, 2026, ctx.targets, as_of=date(2026, 10, 27))
    pd.testing.assert_frame_equal(report.individuals[2], other.metrics)
    assert report.periods["Period"].tolist() == ["Q1", "Q2", "Q3", "Q4", "YTD"]
    assert report.periods.set_index("Period").loc["Q4", "Calendar status"] == "Partial - through cutoff"


def test_shift_review_scopes_selected_shift_and_crew_and_ignores_sidebar_filters(report_context):
    ctx = report_context
    ctx.base.app_settings["crew_areas"] = {"Assembly": "Assembly", "SMT": "SMT"}
    ctx.base.technicians.loc[ctx.base.technicians["technician_id"].eq(2), "crew"] = "SMT"
    ctx.filters = replace(ctx.filters, technician_ids=[2], shifts=["Missing"], areas=["Missing"],
                          ff_groups=["Missing"], wo_types=["Training"])
    report = _shift_review(ctx, area="Assembly")
    assert set(report.individuals) == {1}
    assert report.roster["Technician"].tolist() == ["Alice Example"]
    assert report.metrics.set_index("KPI").loc["Labor Efficiency", "YTD"] == 75
    assert report.metrics.set_index("KPI").loc["Maintenance Calls", "YTD"] == 4
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(build_shift_review_pdf(report))).pages)
    assert "Alice Example" in text
    assert "Bob Confidential" not in text
    assert "Team Lead Performance Review" in text
    assert "Q1" in text and "Q4" in text and "YTD" in text
    assert "full weekly totals" in text
    assert "Review periods and calculation notes" in text
    assert report.filename == "Shift_Review_1st_Shift_Assembly_2026_as_of_2026-10-27.pdf"
    # Move Bob's current assignment and attributed records to the other shift.
    ctx.base.technicians.loc[ctx.base.technicians["technician_id"].eq(2), "shift"] = "2nd Shift"
    for frame in (ctx.base.fiix, ctx.base.ftf_eval, ctx.base.calls, ctx.base.dayforce):
        frame.loc[frame["technician_id"].eq(2), "shift"] = "2nd Shift"
    report = _shift_review(ctx)
    assert set(report.individuals) == {1}
    assert report.metrics.set_index("KPI").loc["Maintenance Calls", "YTD"] == 4
    report = _shift_review(ctx, shift="2nd Shift")
    assert set(report.individuals) == {2}
    assert report.metrics.set_index("KPI").loc["Maintenance Calls", "YTD"] == 4


def test_shift_review_team_lead_roster_and_labor_exclusion_do_not_change_personal_kpis(report_context):
    ctx = report_context
    ctx.base.technicians.loc[ctx.base.technicians["technician_id"].eq(1), "is_lead"] = True
    report = _shift_review(ctx)
    assert report.roster.set_index("technician_id").loc[1, "Review role"] == "Team lead"
    assert report.metrics.set_index("KPI").loc["Labor Efficiency", "YTD"] == pytest.approx(9 / 160 * 100)
    assert report.individuals[1].set_index("KPI").loc["Labor Efficiency", "YTD"] == 75
    assert any("current technician master" in n for n in report.notices)
    assert any("not applied" in n for n in report.notices)


def test_shift_review_call_averages_are_weighted_and_calendar_boundaries_are_exact(report_context):
    ctx = report_context
    extra = pd.concat([ctx.base.calls[ctx.base.calls["technician_id"].eq(2)]] * 2, ignore_index=True)
    extra["id"] = range(1000, 1000 + len(extra))
    ctx.base.calls = pd.concat([ctx.base.calls, extra], ignore_index=True)
    report = _shift_review(ctx)
    assert report.metrics.set_index("KPI").loc["MTTR", "YTD"] == 35
    assert report.metrics.set_index("KPI").loc["Maintenance Calls", "YTD"] == 16
    # Four Alice calls: outside year, end Q1, start Q2 and after cutoff.
    own = ctx.base.calls["technician_id"].eq(1)
    ctx.base.calls.loc[own, "call_time"] = pd.to_datetime(
        ["2025-12-31 23:59:59", "2026-03-31 23:59:59",
         "2026-04-01 00:00:00", "2026-10-28 00:00:00"])
    report = _shift_review(ctx)
    calls = report.metrics.set_index("KPI").loc["Maintenance Calls"]
    assert calls["Q1"] == 1
    assert calls["Q2"] == 1
    assert calls["YTD"] == 14
    assert report.metrics.set_index("KPI").loc["Labor Efficiency", "Q4"] == 0


def test_shift_review_missing_future_and_past_periods_have_explicit_status(report_context):
    report = _shift_review(report_context, as_of=date(2026, 2, 15))
    assert report.metrics[["Q2", "Q3", "Q4"]].isna().all().all()
    assert report.periods.set_index("Period").loc["Q2", "Calendar status"] == "Not started"
    assert any("no Dayforce hours" in n for n in report.notices)
    assert any("no Ignition records" in n for n in report.notices)
    assert report.metrics.set_index("KPI").loc["Labor Efficiency", "YTD"] is None or pd.isna(
        report.metrics.set_index("KPI").loc["Labor Efficiency", "YTD"])
    past = _shift_review(report_context, year=2025)
    assert past.periods["Calendar status"].eq("Complete").all()
    assert past.periods.iloc[-1]["Through date (inclusive)"] == "2025-12-31"


@pytest.mark.parametrize("shift,area,match", [
    ("Missing", "All areas", "active reporting shift"),
    ("1st Shift", "Missing", "configured crew area"),
    ("1st Shift", "Tester Tech", "reserved"),
    ("3rd Shift", "All areas", "No maintenance technicians"),
])
def test_shift_review_rejects_invalid_or_unconfigured_scope(report_context, shift, area, match):
    with pytest.raises(ValueError, match=match):
        _shift_review(report_context, shift=shift, area=area)


def test_shift_review_ui_previews_and_invalidates_changed_scope_or_data(report_context):
    from streamlit.testing.v1 import AppTest

    ctx = report_context
    ctx.is_admin = True
    ctx.base.app_settings["crew_areas"] = {"Assembly": "Assembly"}
    ctx.base.week_keys = [202501, *ctx.base.week_keys]
    app = AppTest.from_string(
        "import streamlit as st\n"
        "from ui.shift_reviews import render_shift_review\n"
        "render_shift_review(st.session_state['ctx'], st.session_state['shift'], st.session_state['area'])\n")
    app.session_state["ctx"] = ctx
    app.session_state["shift"] = "1st Shift"
    app.session_state["area"] = "All areas"
    for change in ("area", "targets", "year", "data", "shift"):
        app.run()
        app.button(key="shift_review_generate").click().run(timeout=30)
        assert not app.exception
        assert app.get("download_button")
        assert len(app.dataframe) >= 5
        assert app.session_state["_shift_review"][1].shift == "1st Shift"
        if change == "area":
            app.session_state["area"] = "Assembly"
        elif change == "targets":
            ctx.targets["labor_efficiency"]["target"] = 70
        elif change == "year":
            app.selectbox(key="shift_review_year").set_value(2025)
        elif change == "data":
            ctx.version += 1
        else:
            app.session_state["shift"] = "2nd Shift"
        app.run()
        assert not app.exception
        assert "_shift_review" not in app.session_state
        assert not app.get("download_button")


def test_shift_review_individual_preview_and_download_are_admin_only(report_context):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "import streamlit as st\n"
        "from ui.shift_reviews import render_shift_review\n"
        "render_shift_review(st.session_state['ctx'], '1st Shift', 'All areas')\n")
    app.session_state["ctx"] = report_context
    app.run()
    assert not app.exception
    assert not app.button
    assert not app.dataframe
    assert not app.get("download_button")


def test_shift_dashboard_has_review_function_and_preserves_existing_dashboard(report_context):
    from streamlit.testing.v1 import AppTest

    report_context.is_admin = True
    app = AppTest.from_string(
        "import streamlit as st\n"
        "from pages.shift_dashboard import render\n"
        "render(st.session_state['ctx'])\n")
    app.session_state["ctx"] = report_context
    app.run(timeout=30)
    assert not app.exception
    assert app.button(key="shift_review_generate")
    assert app.metric
    assert any("Shift" in frame.value.columns for frame in app.dataframe)
    app.button(key="shift_review_generate").click().run(timeout=30)
    assert not app.exception
    assert app.get("download_button")
