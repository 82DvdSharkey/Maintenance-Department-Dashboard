"""Excel export of the currently filtered KPI tables."""
from __future__ import annotations

from io import BytesIO

import pandas as pd


def build_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, df in sheets.items():
            out = df.copy()
            for col in out.columns:
                if isinstance(out[col].dtype, pd.DatetimeTZDtype):
                    out[col] = out[col].dt.tz_localize(None)
            out.to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.sheets[name[:31]]
            for i, col in enumerate(out.columns, start=1):
                width = min(60, max(10, len(str(col)) + 2))
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
            ws.freeze_panes = "A2"
    return buffer.getvalue()
