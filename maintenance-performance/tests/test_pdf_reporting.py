from io import BytesIO

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest
from pypdf import PdfReader

from kpi.definitions import KPI_DEFS
from ui.pdf_reporting import ReportSection, build_dashboard_pdf, dashboard_filter_summary, metrics_from_kpis
from ui.pdf_reporting import _plotly_figure_png


@pytest.mark.parametrize("trace_type", [go.Bar, go.Scatter])
def test_pdf_chart_preserves_numpy_backed_plotly_values(monkeypatch, trace_type):
    figure = go.Figure(trace_type(x=np.array([1, 2, 3]), y=np.array([12.5, 8.0, 16.0])))
    captured = []
    close = plt.close

    def capture_chart(chart):
        if hasattr(chart, "axes"):
            captured.append(chart)
        close(chart)

    monkeypatch.setattr(plt, "close", capture_chart)
    image = _plotly_figure_png(figure)

    assert image.startswith(b"\x89PNG")
    axis = captured[-1].axes[0]
    if trace_type is go.Bar:
        assert [bar.get_height() for bar in axis.patches] == pytest.approx([12.5, 8.0, 16.0])
    else:
        assert axis.lines[0].get_ydata().tolist() == pytest.approx([12.5, 8.0, 16.0])
    assert [label.get_text() for label in axis.get_xticklabels()] == ["1", "2", "3"]


def _pages(pdf_bytes: bytes):
    return PdfReader(BytesIO(pdf_bytes)).pages


def test_empty_report_is_a_valid_single_page_pdf():
    pdf = build_dashboard_pdf(title="Ignition Performance", period="2026-CW41",
                             filters={"Shift": "All", "Reporting period": "2026-CW41"},
                             metrics=[], sections=[])
    pages = _pages(pdf)

    assert pdf.startswith(b"%PDF-")
    assert len(pages) == 1
    assert "No data is available" in pages[0].extract_text()
    assert "America/Detroit" in pages[0].extract_text()


def test_pdf_kpis_match_current_previous_rolling_and_target_values():
    definition = KPI_DEFS["labor_efficiency"]
    current = pd.Series({definition.column: 72.34, f"{definition.column}_r4": 77.51})
    previous = pd.Series({definition.column: 71.66})
    metrics = metrics_from_kpis([definition], current, previous,
                                {definition.key: {"target": 75.0}})
    pdf = build_dashboard_pdf(title="Executive Dashboard", period="2026-CW41",
                              filters={"Department": "Final Assembly", "Shift": "1st Shift"},
                              metrics=metrics, sections=[])
    text = _pages(pdf)[0].extract_text()

    assert "72.3%" in text
    assert "71.7%" in text
    assert "0.7%" in text
    assert "77.5%" in text
    assert "75.0%" in text


def test_filter_summary_keeps_global_and_dashboard_area_filters_distinct():
    class Filters:
        label = "2026-CW41"
        week_keys = [202641]
        date_start = None
        date_end = None
        areas = ["Assembly Cell 1"]
        shifts = ["1st Shift"]
        technician_ids = [7]
        ff_groups = []
        wo_types = []

    class Context:
        settings = {"department_name": "Final Assembly"}
        filters = Filters()

        @staticmethod
        def tech_name(technician_id):
            return {7: "Technician A"}[technician_id]

    summary = dashboard_filter_summary(Context(), {"Crew area": "SMT"})

    assert summary["Area"] == "Assembly Cell 1"
    assert summary["Crew area"] == "SMT"
    assert summary["Shift"] == "1st Shift"
    assert summary["Technician"] == "Technician A"


def test_long_tables_repeat_headers_and_page_numbers_without_local_paths():
    rows = pd.DataFrame({"Work Order": [f"WO-{index:04d}" for index in range(320)],
                         "Logged Hours": [index / 10 for index in range(320)]})
    figure = go.Figure(go.Bar(x=["1st Shift", "2nd Shift"], y=[12, 8], name="Hours"))
    figure.update_layout(title="Logged Hours by Shift")
    pdf = build_dashboard_pdf(title="Fiix Performance", period="2026-CW41",
                              filters={"Source": r"C:\Users\tester\OneDrive - Magna\Weekly Reports\Fiix weekly report.csv"},
                              metrics=[], sections=[ReportSection("Work order detail", table=rows, charts=[figure])])
    pages = _pages(pdf)
    text_by_page = [page.extract_text() for page in pages]

    assert len(pages) > 1
    assert all("Work Order" in page_text for page_text in text_by_page[1:])
    assert all(f"Page {index} of {len(pages)}" in page_text
               for index, page_text in enumerate(text_by_page, start=1))
    assert "C:\\Users\\tester" not in "\n".join(text_by_page)
    assert "OneDrive - Magna" not in "\n".join(text_by_page)
    assert "[local path omitted]" in text_by_page[0]