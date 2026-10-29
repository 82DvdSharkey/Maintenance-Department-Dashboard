"""One compact, individual PDF used by preview and manual download."""
from __future__ import annotations

from html import escape
from io import BytesIO

from matplotlib.figure import Figure
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from kpi.definitions import KPI_DEFS
from reporting.weekly_technician import WeeklyTechnicianReport
from utils.dates import week_label


def _value(value, unit: str = "") -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, (float, int)):
        return f"{value:,.0f}" if unit == "count" else f"{value:,.1f}{' ' if unit == 'min' else ''}{unit}"
    return str(value)


def build_technician_pdf(report: WeeklyTechnicianReport) -> bytes:
    output = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportCell", fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="ReportNote", fontSize=8, leading=10, spaceAfter=5))
    doc = SimpleDocTemplate(output, pagesize=landscape(letter), leftMargin=36, rightMargin=36,
                            topMargin=30, bottomMargin=30, title="Maintenance Technician Performance Report",
                            author="Maintenance Department")
    story = []

    def heading(text):
        story.extend([Paragraph(escape(text), styles["Heading2"]), Spacer(1, 3)])

    def note(text):
        story.append(Paragraph(escape(text), styles["ReportNote"]))

    def table(headers, rows, widths=None, padding=4):
        values = [[Paragraph(escape(str(value)), styles["ReportCell"]) for value in row]
                  for row in [headers, *rows]]
        result = Table(values, colWidths=widths or [doc.width / len(headers)] * len(headers), repeatRows=1)
        result.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dce4ec")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c8cdd2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), padding),
            ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
        ]))
        story.extend([result, Spacer(1, 6)])

    def compact(frame, columns, limit):
        selected = frame[list(columns)].rename(columns=columns)
        table(list(selected.columns), [[_value(v) for v in row]
                                      for row in selected.head(limit).itertuples(index=False, name=None)])
        if len(frame) > limit:
            note(f"Showing {limit} of {len(frame)} rows. Full detail is available in the dashboard.")
        elif frame.empty:
            note("No records for this technician/week.")

    story.append(Paragraph("Maintenance Technician Performance Report", styles["Title"]))
    note(f"Technician: {report.technician_name} | Shift: {report.shift} | Calendar Week: {week_label(report.week_key)}")
    note(f"Crew: {report.crew}")
    note(f"Reporting Period: {report.period_start:%Y-%m-%d %H:%M} to "
         f"{report.period_end:%Y-%m-%d %H:%M} (end exclusive, America/Detroit)")
    note(f"Generated: {report.generated:%Y-%m-%d %H:%M %Z} | Confidential - individual performance information")
    heading("Weekly KPI Scorecard")
    table(["KPI", "Current Week", "Previous Week", "4-Week Average", "Target", "Status"],
          [[r["KPI"], *[_value(r[c], r["Unit"]) for c in
                       ("Current Week", "Previous Week", "4-Week Average", "Target")], r["Status"]]
           for r in report.metrics.to_dict("records")],
          [doc.width * v for v in (0.25, 0.14, 0.14, 0.16, 0.12, 0.19)], padding=3)
    heading("Technician Workload Summary")
    table(["Measure", "Value"], [[r.Measure, _value(r.Value, r.Unit)] for r in report.workload.itertuples()],
          [doc.width * 0.65, doc.width * 0.35])
    for text in report.notices:
        note(text)

    story.append(PageBreak())
    heading("Fiix Labor Breakdown")
    compact(report.labor, {"classification": "WO Category", "work_orders": "WO Count",
                            "logged_hours": "Logged Hours"}, 8)
    note("Labor hours include the same qualifying WO types and time validation used by the dashboard.")
    heading("First-Time Fix")
    table(list(report.first_time_fix["Measure"]),
          [[_value(r.Value, "%" if r.Measure.endswith("%") else "count")
            for r in report.first_time_fix.itertuples()]])
    compact(report.repeats, {"wo": "Original WO", "tl": "TL", "asset": "Asset",
                             "repeat_wo": "Repeat WO", "days_to_repeat": "Days Until Repeat"}, 5)
    heading("Preventive Maintenance")
    pm_count = report.workload.loc[report.workload["Measure"] == "PM Work Orders Completed", "Value"].iloc[0]
    table(["PM Assigned", "PM Completed", "PM Remaining", "PM Completion Share (Shift/Crew)"],
          [["N/A", _value(pm_count, "count"), "N/A", _value(report.pm_peer_share, "%")]])
    heading("Ignition Performance by Area (minutes)")
    compact(report.areas, {"ign_area_name": "IgnAreaName", "maintenance_calls": "Calls",
                           "avg_response_duration_min": "Avg Response",
                           "avg_repair_duration_min": "Avg Repair"}, 6)

    story.append(PageBreak())
    heading("12-Week Technician Performance History")
    note("Weekly actuals and the dashboard's pooled 4-week averages; gaps indicate unavailable KPI values.")
    fig = Figure(figsize=(10, 6), layout="constrained")
    axes = fig.subplots(2, 4)
    for axis, key in zip(axes.flat, ("labor_efficiency", "first_time_fix", "pm_peer_share",
                                    "pm_closed", "avg_response_min", "avg_repair_min", "call_peer_share")):
        definition = KPI_DEFS[key]
        trend = report.trend
        x = list(range(len(trend)))
        axis.plot(x, trend[definition.column], marker="o", markersize=3, label="Current week")
        axis.plot(x, trend[f"{definition.column}_r4"], linestyle="--", label="4-week avg")
        target = report.targets.get(key, {}).get("target")
        if target is not None and pd.notna(target):
            axis.axhline(target, linestyle=":", color="#c41230", label="Target")
        axis.set_title(definition.label, fontsize=10)
        axis.set_ylabel(definition.unit)
        axis.set_xticks(x[::2], trend["week_label"].iloc[::2], rotation=30, fontsize=7)
        axis.tick_params(axis="y", labelsize=8)
        axis.grid(alpha=0.2)
        axis.legend(fontsize=6)
    for axis in axes.flat[7:]:
        axis.set_visible(False)
    image = BytesIO()
    fig.savefig(image, format="png", dpi=130)
    story.append(Image(BytesIO(image.getvalue()), width=doc.width, height=doc.width * 0.60))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(36, 16, f"Confidential | {report.technician_name} | {week_label(report.week_key)}")
        canvas.drawRightString(756, 16, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
