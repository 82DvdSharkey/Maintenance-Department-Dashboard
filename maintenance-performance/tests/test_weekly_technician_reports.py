from dataclasses import replace
from datetime import datetime, timedelta
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
from reporting.weekly_technician import (
    LOCAL_ZONE,
    build_weekly_technician_report,
    most_recent_completed_week,
)
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
