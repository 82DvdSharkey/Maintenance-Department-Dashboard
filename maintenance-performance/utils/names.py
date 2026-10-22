"""Technician name normalisation and alias resolution (exact, case/whitespace-insensitive only)."""
from __future__ import annotations

import pandas as pd

SOURCE_NAME_FIELDS = {"fiix": "fiix_name", "ignition": "ignition_name", "dayforce": "dayforce_name"}


def clean_text(value) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = " ".join(str(value).split())
    return s or None


def normalize_person_name(value) -> str | None:
    s = clean_text(value)
    if s is None:
        return None
    if s.isupper() or s.islower():
        s = s.title()
    return s


def name_key(value) -> str | None:
    s = clean_text(value)
    return s.casefold() if s else None


def flip_name(name) -> str | None:
    """'Joshua, Michael T Jr' -> 'Michael Joshua' (first given name + last name)."""
    s = clean_text(name)
    if not s or "," not in s:
        return s
    last, _, rest = s.partition(",")
    given = rest.split()
    return f"{given[0]} {last.strip()}" if given else last.strip()


def build_alias_index(technicians: pd.DataFrame, aliases: pd.DataFrame, source: str) -> tuple[dict[str, int], set[str]]:
    """Map name keys to technician_id for a source. Keys claimed by >1 technician are ambiguous and left unmapped."""
    claims: dict[str, set[int]] = {}

    def claim(name, tech_id):
        key = name_key(name)
        if key is not None and pd.notna(tech_id):
            claims.setdefault(key, set()).add(int(tech_id))

    field = SOURCE_NAME_FIELDS.get(source)
    for row in technicians.itertuples(index=False):
        claim(row.technician_name, row.technician_id)
        if field:
            claim(getattr(row, field), row.technician_id)
    if not aliases.empty:
        for row in aliases[aliases["source"] == source].itertuples(index=False):
            claim(row.alias, row.technician_id)

    index = {k: next(iter(v)) for k, v in claims.items() if len(v) == 1}
    ambiguous = {k for k, v in claims.items() if len(v) > 1}
    return index, ambiguous


def resolve_technician_ids(keys: pd.Series, index: dict[str, int]) -> pd.Series:
    return keys.map(index).astype("Int64")
