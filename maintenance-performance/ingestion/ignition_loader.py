"""Ignition maintenance-call workbook ingestion (xlsx, SpreadsheetML 2003 .xls, and BIFF .xls)."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

from kpi.ignition_metrics import DURATION_COLUMNS, seconds_to_minutes
from utils.dates import add_week_columns
from utils.names import clean_text, name_key, normalize_person_name
from utils.validation import Issue, LoadResult, ValidationError, missing_columns, standardize_columns, to_float

IGNITION_COLUMN_MAP = {
    "ID": "id",
    "FocusFactory": "focus_factory",
    "IgnAreaName": "ign_area_name",
    "IgnMachineName": "ign_machine_name",
    "AreaName": "area_name",
    "MachineName": "machine_name",
    "RunStart": "run_start",
    "RunEnd": "run_end",
    "DowntimeID": "downtime_id",
    "DowntimeStart": "downtime_start",
    "DowntimeEnd": "downtime_end",
    "EventDescription": "event_description",
    "Active": "active",
    "OperatorName": "operator_name",
    "TechName": "tech_name",
    "CallType": "call_type",
    "CallTime": "call_time",
    "ResponseTime": "response_time",
    "ArrivalTime": "arrival_time",
    "CompleteTime": "complete_time",
    "RawCallDuration": "raw_call_duration",
    "RawAckDuration": "raw_ack_duration",
    "RawResponseDuration": "raw_response_duration",
    "RawRepairDuration": "raw_repair_duration",
    "RawDelayUntilCall": "raw_delay_until_call",
    "DelayUntilCall": "delay_until_call",
    "CallDuration": "call_duration",
    "AcknowledgeDuration": "acknowledge_duration",
    "ResponseDuration": "response_duration",
    "RepairDuration": "repair_duration",
    "CodeSent": "code_sent",
    "Cancelled": "cancelled",
    "CancelledUser": "cancelled_user",
    "LoggedOutUser": "logged_out_user",
    "ConveyancePartNumbers": "conveyance_part_numbers",
}
IGNITION_REQUIRED = [
    "ID", "CallType", "TechName", "CallTime", "IgnAreaName", "IgnMachineName",
    "RawCallDuration", "RawAckDuration", "RawResponseDuration", "RawRepairDuration",
]
DATETIME_COLUMNS = ["run_start", "run_end", "downtime_start", "downtime_end",
                    "call_time", "response_time", "arrival_time", "complete_time"]
NUMERIC_COLUMNS = ["downtime_id", "raw_call_duration", "raw_ack_duration", "raw_response_duration",
                   "raw_repair_duration", "raw_delay_until_call"]

_SS = "{urn:schemas-microsoft-com:office:spreadsheet}"


def _ss_value(data):
    if data is None:
        return None
    raw = "".join(data.itertext())
    kind = data.get(_SS + "Type")
    if raw == "":
        return None
    if kind == "Number":
        try:
            return float(raw)
        except ValueError:
            return raw
    if kind == "DateTime":
        return pd.to_datetime(raw, errors="coerce")
    if kind == "Boolean":
        return raw.strip() in ("1", "true", "True")
    return raw


def read_spreadsheetml(path: Path, sheet_name: str) -> pd.DataFrame:
    rows: list[list] = []
    in_target = found = False
    for event, elem in ET.iterparse(path, events=("start", "end")):
        if event == "start" and elem.tag == _SS + "Worksheet":
            in_target = elem.get(_SS + "Name") == sheet_name
            found = found or in_target
        elif event == "end" and elem.tag == _SS + "Row":
            if in_target:
                values: list = []
                for cell in elem.findall(_SS + "Cell"):
                    idx = cell.get(_SS + "Index")
                    if idx:
                        values.extend([None] * (int(idx) - 1 - len(values)))
                    values.append(_ss_value(cell.find(_SS + "Data")))
                rows.append(values)
            elem.clear()
        elif event == "end" and elem.tag == _SS + "Worksheet" and in_target:
            break
    if not found:
        raise ValidationError(f"Worksheet '{sheet_name}' not found.")
    if not rows:
        return pd.DataFrame()
    header = [str(h).strip() if h is not None else f"Column{i + 1}" for i, h in enumerate(rows[0])]
    width = len(header)
    data = [(r + [None] * width)[:width] for r in rows[1:]]
    return pd.DataFrame(data, columns=header)


def read_ignition_workbook(path: Path, sheet_name: str) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".xls":
        with open(path, "rb") as fh:
            head = fh.read(512)
        if head.lstrip().startswith(b"<?xml") or b"urn:schemas-microsoft-com:office:spreadsheet" in head:
            return read_spreadsheetml(path, sheet_name)
        engine = "xlrd"
    else:
        engine = "openpyxl"
    try:
        return pd.read_excel(path, sheet_name=sheet_name, engine=engine)
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise ValidationError(f"Worksheet '{sheet_name}' not found.") from exc
        raise


def _code_text(value) -> str | None:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean_text(value)


def _bool_text(value) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    s = clean_text(value)
    return s.lower() if s else None


def clean_ignition(raw: pd.DataFrame, offset_hours: float = 1, source: str = "ignition") -> LoadResult:
    df = standardize_columns(raw, list(IGNITION_COLUMN_MAP))
    missing = missing_columns(df, IGNITION_REQUIRED)
    if missing:
        raise ValidationError(f"Missing required Ignition columns: {', '.join(missing)}")

    warnings: list[str] = []
    optional_missing = missing_columns(df, list(IGNITION_COLUMN_MAP))
    if optional_missing:
        warnings.append(f"Optional columns missing: {', '.join(optional_missing)}")
        for col in optional_missing:
            df[col] = None
    extra_cols = [c for c in df.columns if c not in IGNITION_COLUMN_MAP]

    out = df[list(IGNITION_COLUMN_MAP)].rename(columns=IGNITION_COLUMN_MAP).reset_index(drop=True)
    if extra_cols:
        extras = df[extra_cols].reset_index(drop=True)
        records = [{k: v for k, v in rec.items() if pd.notna(v)} for rec in extras.to_dict("records")]
        out["extra_json"] = [json.dumps(r, default=str) if r else None for r in records]
    else:
        out["extra_json"] = None

    issues: list[Issue] = []
    out["id"] = pd.to_numeric(out["id"], errors="coerce").astype("Int64")
    no_id = out["id"].isna()
    for idx in out.index[no_id]:
        issues.append(Issue(source, "missing_id", f"row {idx + 2}", "Row has no numeric ID and cannot be stored."))
    out = out[~no_id]
    dup = out["id"].duplicated(keep="first")
    for i in out.loc[dup, "id"]:
        issues.append(Issue(source, "duplicate_id_in_file", str(i), "ID appears more than once in the file; first row kept."))
    out = out[~dup].copy()

    for col in DATETIME_COLUMNS:
        out[col] = pd.to_datetime(out[col], errors="coerce", format="mixed")
    for col in NUMERIC_COLUMNS:
        numeric = to_float(out[col])
        bad = numeric.isna() & out[col].notna()
        for i in out.loc[bad, "id"]:
            issues.append(Issue(source, "invalid_duration", str(i), f"{col} is not numeric."))
        out[col] = numeric
    out["active"] = pd.to_numeric(out["active"], errors="coerce").astype("Int64")
    out["code_sent"] = out["code_sent"].map(_code_text).astype("object")
    out["cancelled"] = out["cancelled"].map(_bool_text).astype("object")
    text_cols = [c for c in out.columns if c not in DATETIME_COLUMNS + NUMERIC_COLUMNS
                 + ["id", "active", "code_sent", "cancelled", "extra_json"]]
    for col in text_cols:
        out[col] = out[col].map(clean_text).astype("object")

    out["tech_name_norm"] = out["tech_name"].map(normalize_person_name)
    out["tech_key"] = out["tech_name"].map(name_key)
    out["call_type_norm"] = out["call_type"].map(lambda v: v.strip().lower() if isinstance(v, str) else None)
    for raw_col, minute_col in DURATION_COLUMNS.items():
        out[minute_col] = seconds_to_minutes(out[raw_col])
    out = add_week_columns(out, "call_time", offset_hours)
    return LoadResult(frame=out, source_rows=len(raw), issues=issues, warnings=warnings)


def load_ignition_file(path: Path, settings: dict) -> LoadResult:
    raw = read_ignition_workbook(path, settings.get("ignition_sheet_name", "Dataset 1"))
    return clean_ignition(raw, float(settings.get("week_start_offset_hours", 1)))
