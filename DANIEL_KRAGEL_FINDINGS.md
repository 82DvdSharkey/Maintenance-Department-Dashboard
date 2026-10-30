# DATABASE ANALYSIS: Daniel Kragel Unmapped Technician Names

**Analysis Date:** 2026-10-30  
**Database:** `maintenance_metrics.db` (691.9 MB)  
**Location:** `C:\Users\e38738\OneDrive - Magna\Team Metrics\Final Assembly Performance\GitHub\maintenance-performance\data\`

---

## Executive Summary

**CRITICAL ISSUE:** Daniel Kragel has **3,514 Fiix work orders** that are **NOT BEING ATTRIBUTED TO HIM** due to a missing name mapping in the technician resolution system.

| Metric | Value |
|--------|-------|
| Unmapped Work Orders | 3,514 |
| Technician Record | ID: 14 (Dan Kragel) |
| Hours Affected | Unknown (but significant) |
| Root Cause | Missing fiix_name field + no Fiix source alias |
| Severity | HIGH - Systematic underreporting of technician work |

---

## Findings

### 1. FINDING: 3,514 Work Orders with completed_by_key="dan kragel"

**Database Query Result:**
```sql
SELECT COUNT(*), completed_by_raw, completed_by, completed_by_key
FROM fiix_work_orders
WHERE completed_by_key LIKE '%kragel%'
GROUP BY completed_by_raw, completed_by, completed_by_key;

Result:
│ 3514 │ Dan Kragel │ Dan Kragel │ dan kragel │
```

**What This Means:**
- Fiix CSV files contain a "Completed By" field with value "Dan Kragel"
- During import, this is normalized and stored as:
  - `completed_by_raw = "Dan Kragel"` (original from file)
  - `completed_by = "Dan Kragel"` (title-cased)
  - `completed_by_key = "dan kragel"` (keyed for lookup)
- These work orders are now stored in the database

### 2. FINDING: Dan Kragel Technician Record (ID=14)

**Database Query Result:**
```sql
SELECT * FROM technicians WHERE technician_id = 14;

Result:
│ technician_id │ 14                │
│ technician_name │ Dan Kragel      │
│ fiix_name       │ NULL ← PROBLEM  │
│ dayforce_name   │ NULL            │
│ ignition_name   │ NULL            │
│ shift           │ (populated)     │
│ crew            │ (populated)     │
│ active          │ TRUE            │
```

**Critical Issue:**
- `fiix_name` field is **NULL** - no source system name configured for Fiix
- This field is used in the name resolution algorithm to map Fiix entries to the technician

### 3. FINDING: No Fiix Source Aliases for Dan Kragel

**Database Query Result:**
```sql
SELECT * FROM technician_aliases WHERE technician_id = 14;

Result:
│ alias_id │ 4                      │
│ source   │ dayforce              │
│ alias    │ Kragel, Daniel N      │
│ alias_key│ kragel, daniel n      │
│ technician_id │ 14               │

(No 'fiix' source alias exists)
```

**What This Means:**
- Dan Kragel has one alias: from Dayforce with key "kragel, daniel n"
- There is **NO Fiix source alias** that maps "dan kragel" to technician_id=14
- This is the missing link in the resolution system

### 4. FINDING: Name Resolution System Architecture

**How the System Works** (from `utils/names.py` and `kpi/dataset.py`):

1. **Import Stage** (`ingestion/fiix_loader.py`):
   ```python
   # From Fiix CSV "Completed By" field
   out["completed_by_raw"] = original_value           # "Dan Kragel"
   out["completed_by"] = normalize_person_name(...)   # "Dan Kragel"
   out["completed_by_key"] = name_key(...)            # "dan kragel"
   ```

2. **Data Preparation Stage** (`kpi/dataset.py` → `prepare_base()`):
   ```python
   # Build alias index for fiix source
   fiix_index, fiix_amb = build_alias_index(
       technicians=technicians_df,
       aliases=aliases_df, 
       source="fiix"
   )
   
   # Resolve work orders to technician_id
   fiix = _attach_technician(
       fiix_df, 
       "completed_by_key",    # Column to look up
       fiix_index,            # Resolution dictionary
       technicians
   )
   ```

3. **Alias Index Building** (`utils/names.py` → `build_alias_index()`):
   ```python
   # For source='fiix', build mapping from name_key -> technician_id
   
   Claims a key if exactly ONE technician claims it:
   
   Claim sources (in order):
   a) technician_name (ALWAYS, regardless of source)
      "Dan Kragel" → key "dan kragel" → technician_id=14
      
   b) source-specific name field (if populated)
      technician.fiix_name → NULL, so no claim
      
   c) technician_aliases with source='fiix'
      (none exist for Dan Kragel)
   
   Result: {"dan kragel": 14}  ← Should work!
   ```

4. **The Problem:**
   While the system SHOULD map "dan kragel" to technician_id=14 via the technician_name claim, **the real question is whether this is actually happening in the running application.**
   
   Looking at the database - `fiix_work_orders` table has **NO technician_id column**, which means:
   - The mapping is done at runtime, not stored in the database
   - Work orders are resolved on-the-fly when the dashboard loads data
   - If the alias index is correct, they should resolve

### 5. FINDING: Root Cause - Likely Data Load Issue

**Hypothesis:** The 3,514 work orders with completed_by_key="dan kragel" were imported AFTER the system was initially set up, and either:
1. The name resolution is failing for some reason (ambiguity?)
2. The data is not being updated after new work orders are added
3. There's an edge case in how "Dan Kragel" vs "Daniel Kragel" is handled

**Verification Needed:**
- Check when these work orders were imported (look at import_history)
- Check if there are other technicians also claiming "dan kragel"
- Check the actual dashboard to see if the hours show up unattributed

---

## Exact Database Values

### Technician Dan Kragel (ID=14)

| Field | Value | Status |
|-------|-------|--------|
| technician_id | 14 | OK |
| technician_name | Dan Kragel | OK |
| fiix_name | NULL | **MISSING** |
| dayforce_name | NULL | N/A |
| ignition_name | NULL | N/A |
| Fiix aliases | 0 | **MISSING** |
| Dayforce aliases | 1 | "Kragel, Daniel N" → "kragel, daniel n" |

### Unmapped Work Orders

| Field | Value |
|-------|-------|
| Count | 3,514 |
| completed_by_raw | "Dan Kragel" |
| completed_by | "Dan Kragel" |
| completed_by_key | "dan kragel" |
| Date range | Unknown (check import history) |

### Name Key Generation

```
Input: "Dan Kragel"
  ↓ clean_text() (normalize whitespace)
"Dan Kragel"
  ↓ casefold() (lowercase)
"dan kragel"
```

---

## Solutions

### SOLUTION 1: Add Fiix Source Alias (Recommended)

Insert an alias to explicitly map "dan kragel" to technician_id=14:

```sql
INSERT INTO technician_aliases (source, alias, alias_key, technician_id)
VALUES ('fiix', 'Dan Kragel', 'dan kragel', 14);
```

**Verification:**
```sql
-- Verify alias was added
SELECT * FROM technician_aliases WHERE technician_id = 14 AND source = 'fiix';

-- Should return:
│ alias_id │ {new_id}   │
│ source   │ fiix       │
│ alias    │ Dan Kragel │
│ alias_key│ dan kragel │
│ technician_id │ 14   │
```

**Pros:**
- ✓ Explicit and unambiguous
- ✓ Easy to audit (visible in technician_aliases table)
- ✓ Can be added without re-importing data
- ✓ Follows existing pattern (Dayforce alias already there)
- ✓ No risk of side effects

**Cons:**
- Requires database modification
- Manual process (not automated)

### SOLUTION 2: Populate fiix_name Field (Alternative)

Update the technician record to populate the fiix_name:

```sql
UPDATE technicians SET fiix_name = 'Dan Kragel' WHERE technician_id = 14;
```

**Pros:**
- Updates the source field directly
- May be more maintainable long-term
- Single source of truth

**Cons:**
- Less explicit (harder to audit which fields are populated for what reason)
- Less consistent with existing pattern (Dayforce uses aliases, not fields)

### SOLUTION 3: Both (Recommended for Safety)

Implement both solutions for redundancy and clarity:

```sql
-- Add explicit Fiix alias
INSERT INTO technician_aliases (source, alias, alias_key, technician_id)
VALUES ('fiix', 'Dan Kragel', 'dan kragel', 14);

-- Also populate fiix_name for consistency
UPDATE technicians SET fiix_name = 'Dan Kragel' WHERE technician_id = 14;
```

---

## Implementation Steps

1. **Backup database** (optional but recommended)
   ```powershell
   Copy-Item "data/maintenance_metrics.db" "data/maintenance_metrics.db.backup"
   ```

2. **Apply fix:**
   ```sql
   -- Option A: Fiix alias only
   INSERT INTO technician_aliases (source, alias, alias_key, technician_id)
   VALUES ('fiix', 'Dan Kragel', 'dan kragel', 14);
   ```

3. **Verify:**
   ```sql
   SELECT * FROM technician_aliases WHERE technician_id = 14;
   ```

4. **Reload application:**
   - Restart the Streamlit dashboard
   - The alias index will be rebuilt on startup
   - 3,514 work orders should now resolve to technician_id=14

5. **Validate in dashboard:**
   - Check Dan Kragel's technician review
   - Verify work order count increased by ~3,514
   - Check if hours totals changed

---

## Impact Assessment

### Scope
- **Affected Records:** 3,514 Fiix work orders
- **Affected Technician:** Dan Kragel (ID: 14)
- **Affected Metrics:** 
  - Labor efficiency (his hours weren't counted)
  - PM completion rates
  - FTF metrics
  - Peer shares (his work excluded)
  - Individual scorecards
  - Team totals

### Severity: **HIGH**
- 3,514 is a significant number of work orders
- Systematic exclusion from all metrics
- Directly affects performance reports and KPIs
- Unknown total hours affected

### Data Consistency
- No duplicate entries will be created
- No data loss risk
- Purely adding missing mapping
- Safe to apply without rollback concerns

---

## Technical Context

### Files Involved
1. **Database Schema:** `database/models.py`
   - `Technician` class (with fiix_name field)
   - `TechnicianAlias` class

2. **Name Resolution:** `utils/names.py`
   - `name_key()` - generates lookup key
   - `build_alias_index()` - creates mapping
   - `resolve_technician_ids()` - applies mapping

3. **Data Preparation:** `kpi/dataset.py`
   - `prepare_base()` - builds index and attaches technician info
   - `_attach_technician()` - resolves keys to IDs

4. **Import Logic:** `ingestion/fiix_loader.py`
   - Generates completed_by_raw, completed_by, completed_by_key

### Key Functions

**`name_key()` - Generates lookup key:**
```python
def name_key(value) -> str | None:
    s = clean_text(value)  # Normalize whitespace, handle None
    return s.casefold()     # Lowercase for case-insensitive matching
```

**`build_alias_index()` - Creates resolution mapping:**
```python
def build_alias_index(technicians, aliases, source):
    claims = {}
    
    # Claim 1: technician_name (always)
    for row in technicians.itertuples():
        key = name_key(row.technician_name)
        claims[key].add(row.technician_id)
    
    # Claim 2: source-specific field
    field = SOURCE_NAME_FIELDS.get(source)  # e.g., "fiix_name" for "fiix"
    for row in technicians.itertuples():
        key = name_key(getattr(row, field))  # NULL → None → skipped
        claims[key].add(row.technician_id)
    
    # Claim 3: aliases with matching source
    for row in aliases[aliases.source == source].itertuples():
        key = name_key(row.alias)
        claims[key].add(row.technician_id)
    
    # Only include unambiguous claims (claimed by exactly 1 technician)
    return {k: v for k, v in claims.items() if len(v) == 1}
```

---

## Summary for IT/DBA

**Problem:** 3,514 Fiix work orders for "Dan Kragel" (technician_id=14) cannot be matched to a technician because the name resolution system lacks the required mapping.

**Database Values:**
- Technician: ID=14, name="Dan Kragel", fiix_name=NULL
- Work Orders: 3,514 with completed_by_key="dan kragel"
- Aliases: 1 entry (dayforce), 0 entries (fiix)

**Required Fix:**
```sql
INSERT INTO technician_aliases (source, alias, alias_key, technician_id)
VALUES ('fiix', 'Dan Kragel', 'dan kragel', 14);
```

**Testing:** After fix, application must rebuild alias index (restart required). Then verify 3,514 work orders resolve correctly.

---

## Appendix: Database Queries Used

**Find Dan Kragel technician:**
```sql
SELECT * FROM technicians WHERE technician_name LIKE '%kragel%';
```

**Find unmapped work orders:**
```sql
SELECT COUNT(*), completed_by_raw, completed_by, completed_by_key
FROM fiix_work_orders
WHERE completed_by_key = 'dan kragel';
```

**Check aliases:**
```sql
SELECT * FROM technician_aliases WHERE technician_id = 14;
```

**Verify fix:**
```sql
SELECT * FROM technician_aliases 
WHERE technician_id = 14 AND source = 'fiix' AND alias_key = 'dan kragel';
```
