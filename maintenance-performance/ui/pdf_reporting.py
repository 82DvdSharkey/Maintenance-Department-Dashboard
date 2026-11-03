"""Shared, in-memory PDF reports for filtered Streamlit dashboards."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape, unescape
from io import BytesIO
import re
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import json
import hashlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from kpi.dataset import focus_factories_for_ff_groups
from utils.dates import week_label

_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:\\[^<>|?*;,\r\n]+"),
    re.compile(r"\\\\[^<>|?*;,\r\n]+"),
    re.compile(r"(?<![\w:])/(?:[^/\s,;()]+/)*[^,;()\r\n]+"),
)


@dataclass
class ReportMetric:
    label: str
    value: Any
    unit: str = ""
    previous: Any = None
    delta: Any = None
    rolling: Any = None
    target: Any = None
    note: str = ""


@dataclass
class ReportSection:
    title: str
    table: pd.DataFrame | None = None
    table_columns: dict[str, str] | None = None
    charts: list[go.Figure] = field(default_factory=list)
    note: str = ""


def metrics_from_kpis(definitions, current: pd.Series, previous: pd.Series | None, targets: dict) -> list[ReportMetric]:
    """Build report rows from the same values shown by the shared KPI cards."""
    metrics = []
    for definition in definitions:
        target = targets.get(definition.key, {})
        value = current.get(definition.column)
        previous_value = previous.get(definition.column) if previous is not None else None
        delta = None
        if value is not None and previous_value is not None and pd.notna(value) and pd.notna(previous_value):
            delta = value - previous_value
        metrics.append(ReportMetric(
            label=definition.label,
            value=value,
            unit=definition.unit or "",
            previous=previous_value,
            delta=delta,
            rolling=current.get(f"{definition.column}_r4"),
            target=target.get("target"),
        ))
    return metrics


def dashboard_filter_summary(ctx, extra_filters: dict[str, Any] | None = None) -> dict[str, str]:
    """Summarize active dashboard filters without exposing configured filesystem paths."""
    filters = {
        "Department": str(ctx.settings.get("department_name") or "All departments"),
        "Reporting period": str(ctx.filters.label or "All available periods"),
        "Calendar weeks": _week_range(ctx.filters.week_keys),
        "Date range": _date_range(ctx.filters.date_start, ctx.filters.date_end),
        "Area": _joined(ctx.filters.areas),
        "Shift": _joined(ctx.filters.shifts),
        "Technician": _joined([ctx.tech_name(i) for i in ctx.filters.technician_ids]),
        "FF Group": _joined(ctx.filters.ff_groups),
        "Ignition FocusFactory": _joined(getattr(ctx.filters, "focus_factories", [])
                         or focus_factories_for_ff_groups(ctx.filters.ff_groups)),
        "WO Type": _joined(ctx.filters.wo_types),
    }
    for label, value in (extra_filters or {}).items():
        filters[label] = _joined(value) if isinstance(value, (list, tuple, set)) else _safe_text(value)
    return filters


def render_pdf_download_button(ctx, title: str, metrics: list[ReportMetric], sections: list[ReportSection], *,
                               extra_filters: dict[str, Any] | None = None, notices: list[str] | None = None,
                               key: str, container=None) -> None:
    """Render a lazy in-memory PDF preparation action and download button."""
    period = str(ctx.filters.label or "report")
    filename = f"{_slug(title)}_{_slug(period)}.pdf"
    report_data = {
        "title": title,
        "period": period,
        "filters": dashboard_filter_summary(ctx, extra_filters),
        "data_version": ctx.version,
    }
    signature = hashlib.sha256(json.dumps(report_data, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    state_key = f"_pdf_report_{key}"
    target = container or st
    cached = st.session_state.get(state_key)
    if cached and cached[0] != signature:
        st.session_state.pop(state_key, None)
        cached = None
    if target.button("Export to PDF", key=f"{key}_prepare",
                     help="Generate a paginated PDF of this filtered dashboard."):
        with st.spinner("Generating PDF report…"):
            pdf_bytes = build_dashboard_pdf(title=title, period=period, filters=report_data["filters"],
                                            metrics=metrics, sections=sections, notices=notices or [])
        st.session_state[state_key] = (signature, pdf_bytes)
        cached = (signature, pdf_bytes)
    if cached and cached[0] == signature:
        target.download_button("Download PDF", data=cached[1], file_name=filename, mime="application/pdf",
                               key=key)


def build_dashboard_pdf(*, title: str, period: str, filters: dict[str, str],
                        metrics: list[ReportMetric], sections: list[ReportSection],
                        notices: list[str] | None = None) -> bytes:
    """Build a readable, multipage PDF fully in memory."""
    output = BytesIO()
    page_size = landscape(letter)
    page_width, page_height = page_size
    left_margin = right_margin = 0.5 * inch
    top_margin = 0.7 * inch
    bottom_margin = 0.55 * inch
    styles = _styles()
    generated = datetime.now(ZoneInfo("America/Detroit"))
    doc = SimpleDocTemplate(
        output,
        pagesize=page_size,
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
        title=title,
        author="Maintenance Performance",
    )
    story = [
        Paragraph(escape(title), styles["ReportTitle"]),
        Paragraph(f"Reporting period: {escape(_safe_text(period))}", styles["ReportSubTitle"]),
        Paragraph(f"Generated: {generated:%Y-%m-%d %H:%M %Z} (America/Detroit)", styles["Small"]),
        Spacer(1, 8),
        Paragraph("Active filters", styles["SectionHeading"]),
        _filter_table(filters, doc.width, styles),
        Spacer(1, 10),
    ]

    if metrics:
        story.extend([Paragraph("Key performance indicators", styles["SectionHeading"]),
                      _metric_table(metrics, doc.width, styles), Spacer(1, 8)])
    for notice in notices or []:
        story.append(Paragraph(escape(_safe_text(notice)), styles["Notice"]))

    nonempty_sections = [section for section in sections if section]
    if not metrics and not nonempty_sections:
        story.append(Paragraph("No data is available for this report selection.", styles["Normal"]))

    for index, section in enumerate(nonempty_sections):
        story.append(PageBreak())
        story.append(Paragraph(escape(_safe_text(section.title)), styles["SectionHeading"]))
        if section.note:
            story.append(Paragraph(escape(_safe_text(section.note)), styles["Small"]))
            story.append(Spacer(1, 5))
        for figure in section.charts:
            if figure is None:
                continue
            chart = Image(BytesIO(_plotly_figure_png(figure)), width=doc.width, height=doc.width * 0.38)
            chart.hAlign = "LEFT"
            story.append(chart)
            story.append(Spacer(1, 8))
        if section.table is not None:
            displayed = _display_table(section.table, section.table_columns)
            if displayed.empty:
                story.append(Paragraph("No records for the current filters.", styles["Small"]))
            else:
                story.extend(_table_flowables(displayed, doc.width, styles))

    def draw_page(canvas_obj, document):
        canvas_obj.saveState()
        canvas_obj.setStrokeColor(colors.HexColor("#c8cdd2"))
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(left_margin, page_height - 0.45 * inch, page_width - right_margin,
                        page_height - 0.45 * inch)
        canvas_obj.setFont("Helvetica-Bold", 8)
        canvas_obj.setFillColor(colors.HexColor("#444b52"))
        canvas_obj.drawString(left_margin, page_height - 0.34 * inch, "MAINTENANCE PERFORMANCE REPORT")
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page, canvasmaker=_NumberedCanvas)
    return output.getvalue()


def _styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle(name="ReportTitle", parent=base["Title"], fontName="Helvetica-Bold",
                            fontSize=21, leading=25, alignment=TA_LEFT, textColor=colors.HexColor("#222b33"),
                            spaceAfter=5))
    base.add(ParagraphStyle(name="ReportSubTitle", parent=base["Normal"], fontName="Helvetica-Bold",
                            fontSize=10, leading=13, textColor=colors.HexColor("#3b444c"), spaceAfter=2))
    base.add(ParagraphStyle(name="SectionHeading", parent=base["Heading2"], fontName="Helvetica-Bold",
                            fontSize=13, leading=16, textColor=colors.HexColor("#263847"), spaceBefore=4,
                            spaceAfter=7, keepWithNext=True))
    base.add(ParagraphStyle(name="Small", parent=base["Normal"], fontSize=8, leading=10,
                            textColor=colors.HexColor("#4b545c"), spaceAfter=2))
    base.add(ParagraphStyle(name="Notice", parent=base["Normal"], fontSize=8.5, leading=11,
                            textColor=colors.HexColor("#8a4b00"), spaceAfter=4))
    base.add(ParagraphStyle(name="TableHeaderPdf", parent=base["Normal"], fontName="Helvetica-Bold",
                            fontSize=7, leading=8.5, textColor=colors.white, alignment=TA_LEFT))
    base.add(ParagraphStyle(name="TableCellPdf", parent=base["Normal"], fontSize=6.7, leading=8,
                            textColor=colors.HexColor("#20272d"), wordWrap="CJK"))
    base.add(ParagraphStyle(name="KpiHeaderPdf", parent=base["Normal"], fontName="Helvetica-Bold",
                            fontSize=7.5, leading=9, textColor=colors.white))
    base.add(ParagraphStyle(name="KpiCellPdf", parent=base["Normal"], fontSize=8, leading=10,
                            textColor=colors.HexColor("#20272d")))
    return base


def _filter_table(filters: dict[str, str], width: float, styles):
    rows = [[Paragraph("Filter", styles["KpiHeaderPdf"]), Paragraph("Selection", styles["KpiHeaderPdf"])]]
    for label, value in filters.items():
        rows.append([Paragraph(escape(_safe_text(label)), styles["TableCellPdf"]),
                     Paragraph(escape(_safe_text(value)), styles["TableCellPdf"])])
    table = Table(rows, colWidths=[width * 0.23, width * 0.77], repeatRows=1, hAlign="LEFT")
    table.setStyle(_table_style())
    return table


def _metric_table(metrics: list[ReportMetric], width: float, styles):
    headers = ["KPI", "Current", "Previous", "Δ vs prev", "4-week avg", "Target", "Comparison / note"]
    rows = [[Paragraph(header, styles["KpiHeaderPdf"]) for header in headers]]
    for metric in metrics:
        rows.append([
            Paragraph(escape(_safe_text(metric.label)), styles["KpiCellPdf"]),
            Paragraph(escape(_format_value(metric.value, metric.unit)), styles["KpiCellPdf"]),
            Paragraph(escape(_format_value(metric.previous, metric.unit)), styles["KpiCellPdf"]),
            Paragraph(escape(_format_value(metric.delta, metric.unit)), styles["KpiCellPdf"]),
            Paragraph(escape(_format_value(metric.rolling, metric.unit)), styles["KpiCellPdf"]),
            Paragraph(escape(_format_value(metric.target, metric.unit)), styles["KpiCellPdf"]),
            Paragraph(escape(_safe_text(metric.note or "")), styles["KpiCellPdf"]),
        ])
    widths = [width * fraction for fraction in (0.20, 0.11, 0.11, 0.11, 0.11, 0.11, 0.25)]
    table = LongTable(rows, colWidths=widths, repeatRows=1, splitByRow=1, hAlign="LEFT")
    table.setStyle(_table_style())
    return table


def _table_flowables(frame: pd.DataFrame, width: float, styles):
    columns = list(frame.columns)
    max_columns = 8
    chunks = []
    if len(columns) <= max_columns:
        chunks.append(columns)
    else:
        chunks.append(columns[:max_columns])
        key_column = columns[0]
        remaining = columns[max_columns:]
        for start in range(0, len(remaining), max_columns - 1):
            chunks.append([key_column, *remaining[start:start + max_columns - 1]])

    output = []
    for chunk_index, selected in enumerate(chunks, start=1):
        if len(chunks) > 1:
            output.append(Paragraph(f"Table columns {chunk_index} of {len(chunks)}; rows align by {_safe_text(columns[0])}.",
                                    styles["Small"]))
        header = [Paragraph(escape(_safe_text(column)), styles["TableHeaderPdf"]) for column in selected]
        rows = [header]
        for values in frame[selected].itertuples(index=False, name=None):
            rows.append([Paragraph(escape(_cell_text(value)), styles["TableCellPdf"]) for value in values])
        weights = [_column_weight(frame[column], column) for column in selected]
        col_widths = [width * value / sum(weights) for value in weights]
        table = LongTable(rows, colWidths=col_widths, repeatRows=1, splitByRow=1, hAlign="LEFT")
        table.setStyle(_table_style())
        output.append(table)
        output.append(Spacer(1, 7))
    return output


def _table_style():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#344b5e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c8cdd2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f4f6")]),
    ])


def _plotly_figure_png(figure: go.Figure) -> bytes:
    spec = figure.to_plotly_json()
    traces = spec.get("data", [])
    layout = spec.get("layout", {})
    fig, axis = plt.subplots(figsize=(12, 4.6), dpi=140)
    axis.set_axisbelow(True)
    axis.grid(axis="y", color="#e5e8eb", linewidth=0.7)
    categories = []
    for trace in traces:
        for item in _list_values(trace.get("x")):
            text = _safe_text(item)
            if text not in categories:
                categories.append(text)
    if not categories:
        categories = [""]
    x_positions = {label: index for index, label in enumerate(categories)}
    bar_traces = [trace for trace in traces if trace.get("type") == "bar"]
    bar_count = max(len(bar_traces), 1)
    bar_index = 0
    secondary = None
    for trace in traces:
        trace_type = trace.get("type")
        x_values = [_safe_text(value) for value in _list_values(trace.get("x"))]
        y_values = pd.to_numeric(pd.Series(_list_values(trace.get("y"))), errors="coerce").to_numpy()
        count = min(len(x_values), len(y_values))
        if not count:
            continue
        x = [x_positions[value] for value in x_values[:count]]
        y = y_values[:count]
        name = _safe_text(trace.get("name") or "Series")
        marker = trace.get("marker") or {}
        line = trace.get("line") or {}
        color = marker.get("color") or line.get("color") or "#c41230"
        if isinstance(color, list):
            color = color[:count]
        if trace_type == "bar":
            width = 0.8 / bar_count
            offset = (bar_index - (bar_count - 1) / 2) * width
            axis.bar([position + offset for position in x], y, width=width, label=name, color=color)
            bar_index += 1
        elif trace_type == "scatter":
            target_axis = axis
            if trace.get("yaxis") == "y2":
                if secondary is None:
                    secondary = axis.twinx()
                target_axis = secondary
            mode = trace.get("mode") or "lines"
            target_axis.plot(x, y, label=name, color=color,
                             marker="o" if "markers" in mode else None,
                             linestyle="-" if "lines" in mode else "None")
    axis.set_xticks(range(len(categories)))
    axis.set_xticklabels(categories, rotation=30 if max(map(len, categories), default=0) > 10 else 0,
                         ha="right" if max(map(len, categories), default=0) > 10 else "center")
    xaxis = layout.get("xaxis") or {}
    yaxis = layout.get("yaxis") or {}
    axis.set_xlabel(_layout_title(xaxis.get("title")))
    axis.set_ylabel(_layout_title(yaxis.get("title")))
    if yaxis.get("range"):
        axis.set_ylim(yaxis["range"])
    if yaxis.get("ticksuffix"):
        axis.yaxis.set_major_formatter(plt.FuncFormatter(lambda value, _position: f"{value:g}{yaxis['ticksuffix']}"))
    if secondary is not None:
        yaxis2 = layout.get("yaxis2") or {}
        secondary.set_ylabel(_layout_title(yaxis2.get("title")))
        if yaxis2.get("range"):
            secondary.set_ylim(yaxis2["range"])
        if yaxis2.get("ticksuffix"):
            secondary.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda value, _position: f"{value:g}{yaxis2['ticksuffix']}"))
    for shape in layout.get("shapes") or []:
        if shape.get("type") == "line" and shape.get("y0") == shape.get("y1"):
            axis.axhline(shape["y0"], color="#c0392b", linestyle=":", linewidth=1.2)
    title_text = _layout_title(layout.get("title"))
    if title_text:
        axis.set_title(title_text, loc="left", fontsize=12, fontweight="bold")
    handles, labels = axis.get_legend_handles_labels()
    if secondary is not None:
        more_handles, more_labels = secondary.get_legend_handles_labels()
        handles.extend(more_handles)
        labels.extend(more_labels)
    if handles:
        axis.legend(handles, labels, loc="best", frameon=False, fontsize=8)
    fig.tight_layout()
    image = BytesIO()
    fig.savefig(image, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return image.getvalue()


def _layout_title(value) -> str:
    if isinstance(value, dict):
        value = value.get("text", "")
    return unescape(re.sub(r"<br\s*/?>", " ", str(value or "")))


def _list_values(values) -> list:
    if values is None:
        return []
    if isinstance(values, dict) and "bdata" in values and "dtype" in values:
        values = np.frombuffer(base64.b64decode(values["bdata"]), dtype=np.dtype(values["dtype"]))
    if hasattr(values, "tolist"):
        values = values.tolist()
    return list(values)


def _display_table(frame: pd.DataFrame, columns: dict[str, str] | None) -> pd.DataFrame:
    if columns:
        selected = [column for column in columns if column in frame.columns]
        return frame[selected].rename(columns=columns).copy()
    return frame.copy()


def _column_weight(column: pd.Series, label: str) -> int:
    values = column.dropna().astype(str).head(100)
    longest = max([len(label), *(len(value) for value in values)], default=len(label))
    return max(6, min(longest, 34))


def _cell_text(value) -> str:
    if value is None or value is pd.NaT:
        return "No data"
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else value.strftime("%Y-%m-%d")
    try:
        if pd.isna(value):
            return "No data"
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return _safe_text(value)


def _format_value(value, unit: str = "") -> str:
    if value is None:
        return "No data"
    if isinstance(value, str):
        return _safe_text(value)
    try:
        if pd.isna(value):
            return "No data"
    except (TypeError, ValueError):
        pass
    if unit == "%":
        return f"{float(value):.1f}%"
    if unit == "min":
        return f"{float(value):.2f} min"
    if unit == "count":
        return f"{float(value):,.0f}"
    if unit == "h":
        return f"{float(value):,.1f} h"
    if unit == "pp":
        return f"{float(value):+.1f} pp"
    return f"{float(value):,.2f}" if isinstance(value, (int, float)) else _safe_text(value)


def _joined(values) -> str:
    if values is None:
        return "All"
    if isinstance(values, str):
        return values or "All"
    items = list(values)
    return ", ".join(_safe_text(value) for value in items) if items else "All"


def _week_range(weeks) -> str:
    if not weeks:
        return "Not set"
    labels = [week_label(week) for week in weeks]
    return labels[0] if len(labels) == 1 else f"{labels[0]} to {labels[-1]}"


def _date_range(start, end) -> str:
    if start is None or end is None:
        return "Not set"
    end_date = pd.Timestamp(end).date() - pd.Timedelta(days=1)
    return f"{pd.Timestamp(start).date():%Y-%m-%d} to {end_date:%Y-%m-%d}"


def _safe_text(value) -> str:
    text = str(value)
    for pattern in _PATH_PATTERNS:
        text = pattern.sub("[local path omitted]", text)
    return text


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", _safe_text(value)).strip("_")[:80] or "report"


class _NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("pageCompression", 0)
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.saveState()
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#4b545c"))
            self.drawRightString(self._pagesize[0] - 0.5 * inch, 0.28 * inch,
                                 f"Page {self._pageNumber} of {total_pages}")
            self.restoreState()
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)