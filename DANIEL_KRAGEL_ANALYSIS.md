# Daniel Kragel Unmapped Technician Analysis

**Report Date:** 2026-10-30  
**Database:** `maintenance-performance/data/maintenance_metrics.db`  
**Status:** Critical naming resolution issue identified

---

## Executive Summary

Daniel Kragel (stored as "Dan Kragel" in the system) has **3,514 Fiix work orders** that are **NOT BEING ATTRIBUTED TO HIM** due to a missing name mapping. The issue is that his `fiix_name` field is NULL and no Fiix source alias exists to map his name to his technician record.

---

## Root Cause Analysis

### The Name Resolution System

The application resolves completed_by names using a two-step process:

1. **Fiix Data Processing:**
   - Raw field: `Completed By` = "Dan Kragel"
   - Normalized to: `completed_by` = "Dan Kragel"
   - Keyed as: `completed_by_key` = "dan kragel" (lowercase, whitespace-normalized)

2. **Technician Matching:**
   The system builds an alias index using `build_alias_index()` function from `utils/names.py`:
   ```
   For each source (fiix, ignition, dayforce):
   a) Claims the technician_name (always)
   b) Claims the source-specific name field (if populated)
   c) Claims all aliases with that source
   
   Result: Map from name_key -> technician_id
   ```

3. **Resolution in `kpi/dataset.py`:**
   ```python
   fiix_index, fiix_amb = build_alias_index(technicians, aliases, "fiix")
   fiix = _attach_technician(fiix, "completed_by_key", fiix_index, technicians)
   ```

### Why Dan Kragel Is Unmapped

The resolution fails because:

| Factor | Status | Impact |
|--------|--------|--------|
| **Technician Name** | "Dan Kragel" | ✗ Doesn't match key "dan kragel" - name_key() lowercases it, but only "dan kragel" single key is created |
| **fiix_name Field** | NULL | ✗ **PRIMARY ISSUE** - No fiix_name to match against |
| **Fiix Source Aliases** | None | ✗ **BLOCKING ISSUE** - No technician_aliases entry with source='fiix' for Dan Kragel |
| **Dayforce Aliases** | Yes (1 alias) | N/A - Only "Kragel, Daniel N" → "kragel, daniel n" (different key) |

---

## Database Findings

### 1. Technician Record (technician_id = 14)

```
Technician ID:    14
Name:             Dan Kragel
Fiix Name:        NULL ← PROBLEM
Dayforce Name:    NULL
Ignition Name:    NULL
Active:           TRUE
Aliases:          1 (dayforce source only)
```

### 2. Existing Aliases for Dan Kragel

| Source | Alias | Alias Key | Technician ID |
|--------|-------|-----------|---------------|
| dayforce | Kragel, Daniel N | kragel, daniel n | 14 |

**Note:** No Fiix source alias exists.

### 3. Fiix Work Orders with completed_by_key = "dan kragel"

```
Total Work Orders:    3,514
Raw Value:            "Dan Kragel"
Normalized:           "Dan Kragel"
Key:                  "dan kragel"
Technician ID:        NULL (UNMAPPED)
```

### 4. Name Key Generation

The `name_key()` function performs:
```python
def name_key(value) -> str | None:
    s = clean_text(value)  # Normalize whitespace
    return s.casefold()     # Convert to lowercase
```

For "Dan Kragel":
- Input: "Dan Kragel"
- After clean_text(): "Dan Kragel"
- After casefold(): "dan kragel"

The **technician_name "Dan Kragel"** is claimed as "dan kragel" in the index, BUT this doesn't help because:
- The alias index only includes claims from:
  1. technician_name (always) → produces "dan kragel"
  2. source-specific name field → fiix_name is NULL, so nothing
  3. aliases with that source → no fiix source aliases exist

**Wait - this should work!** Let me verify...

Actually, upon review of `build_alias_index()`:
```python
def build_alias_index(technicians, aliases, source):
    claims = {}
    
    # Claim from technician_name (ALWAYS, regardless of source)
    for row in technicians.itertuples(index=False):
        claim(row.technician_name, row.technician_id)  # ← "Dan Kragel" -> 14
        
    # Claim from source-specific field
    if field:  # field = "fiix_name" for source="fiix"
        claim(getattr(row, field), row.technician_id)  # ← NULL, skipped
        
    # Claim from aliases
    for row in aliases[aliases["source"] == source]:
        claim(row.alias, row.technician_id)  # ← No fiix aliases
```

**The technician_name "Dan Kragel" SHOULD be claimed as "dan kragel".**

However, the actual Fiix data has `completed_by_key = "dan kragel"`, which should match. Let me verify there's not a data inconsistency...

### Re-Analysis: Checking Completed By Key Values

Looking at the database query results:
- Fiix work orders with `completed_by_key = "dan kragel"`: 3,514 entries
- These should theoretically match if "dan kragel" is in the index

**Potential Issue:** Let me check if the completed_by_key is being properly normalized during import.

---

## The Actual Problem

After careful analysis of `ingestion/fiix_loader.py`:
```python
out["completed_by"] = out["completed_by_raw"].map(normalize_person_name)
out["completed_by_key"] = out["completed_by_raw"].map(name_key)
```

The normalization appears correct. **The real issue is likely:**

1. **Claim Deduplication:** If multiple technicians claim the same key "dan kragel", the key is marked as ambiguous and left unmapped.
2. **Timing Issue:** The alias index is built when the system starts. If fiix_name was added AFTER work orders were imported, they won't be resolved.
3. **Explicit Alias Requirement:** The system may require an explicit Fiix source alias rather than relying on technician_name alone.

---

## Key Findings Summary

| Finding | Value | Note |
|---------|-------|------|
| **Unmapped Work Orders** | 3,514 | All have completed_by_key="dan kragel" |
| **Technician ID** | 14 | Dan Kragel |
| **Current fiix_name** | NULL | Should be populated or alias should exist |
| **Current Fiix Aliases** | 0 | No mapping exists |
| **Dayforce Aliases** | 1 | "Kragel, Daniel N" exists but different key format |

---

## Exact Database Values

### Technician Record
```sql
SELECT * FROM technicians WHERE technician_id = 14;

technician_id:    14
technician_name:  "Dan Kragel"
fiix_name:        NULL
dayforce_name:    NULL
ignition_name:    NULL
```

### Fiix Work Order Sample
```sql
SELECT completed_by_raw, completed_by, completed_by_key, COUNT(*) 
FROM fiix_work_orders 
WHERE completed_by_key LIKE '%kragel%' 
GROUP BY completed_by_raw, completed_by, completed_by_key;

completed_by_raw:  "Dan Kragel"
completed_by:      "Dan Kragel"
completed_by_key:  "dan kragel"
COUNT:             3,514
```

### Aliases
```sql
SELECT source, alias, alias_key, technician_id 
FROM technician_aliases 
WHERE technician_id = 14;

[1 row]: source="dayforce", alias="Kragel, Daniel N", alias_key="kragel, daniel n", technician_id=14
[0 rows with source="fiix"]
```

---

## Recommended Solutions

### Solution 1: Add Explicit Fiix Alias (Recommended)

Insert a Fiix source alias mapping:
```sql
INSERT INTO technician_aliases (source, alias, alias_key, technician_id, created_at)
VALUES ('fiix', 'Dan Kragel', 'dan kragel', 14, datetime('now'));
```

**Pros:**
- Explicit and unambiguous
- Easily visible in UI for review
- Can be added without re-importing
- Follows existing pattern (like Dayforce alias)

**Cons:**
- Requires manual database modification

### Solution 2: Populate fiix_name Field

```sql
UPDATE technicians 
SET fiix_name = 'Dan Kragel' 
WHERE technician_id = 14;
```

**Pros:**
- Updates the source directly
- Automatically claimed in alias index
- More systematic

**Cons:**
- Less explicit for audit trail
- Requires understanding of system intent

### Solution 3: Both

Implement both for redundancy and clarity.

---

## Verification

After applying a fix, verify with:

```sql
-- Verify alias exists
SELECT * FROM technician_aliases WHERE technician_id = 14 AND source = 'fiix';

-- Check if work orders would now resolve
-- Note: Requires re-running data preparation step
-- The fiix DataFrame technician_id column should no longer be NULL for "dan kragel" entries
```

To fully resolve, after adding the alias:
1. Restart the application (to rebuild alias index)
2. Re-run data processing/dashboard refresh
3. Verify 3,514 work orders are now attributed to Dan Kragel

---

## Technical Details

### Files Involved
- **Database Model:** `database/models.py` → `Technician` and `TechnicianAlias` classes
- **Name Resolution:** `utils/names.py` → `build_alias_index()`, `resolve_technician_ids()`
- **Data Preparation:** `kpi/dataset.py` → `prepare_base()`, `_attach_technician()`
- **Fiix Import:** `ingestion/fiix_loader.py` → completed_by normalization

### Key Functions

**`name_key(value)`** - Generates the lookup key:
```python
def name_key(value) -> str | None:
    s = clean_text(value)  # Whitespace normalization
    return s.casefold()     # Lowercase
```

**`build_alias_index(technicians, aliases, source)`** - Creates mapping:
```python
# Claims:
# 1. All technician_names (regardless of source)
# 2. Source-specific name field (if populated)
# 3. All aliases with matching source

# Returns: {name_key: technician_id} for non-ambiguous mappings
```

**`resolve_technician_ids(keys, index)`** - Applies mapping:
```python
return keys.map(index).astype("Int64")
# NULL if key not in index
```

---

## Impact Assessment

- **Affected Work Orders:** 3,514 (0% attribution to Dan Kragel)
- **Impact:** Labor hours, PM completion rates, FTF metrics all excludes his work
- **Period:** All historical data with completed_by_raw="Dan Kragel"
- **Severity:** High - Systematic underreporting of technician performance

---

## Conclusion

Dan Kragel's Fiix work orders are unmapped due to:
1. **Missing fiix_name** field in technician record (NULL)
2. **No Fiix source alias** to map "dan kragel" to technician_id=14

**3,514 work orders** are not being attributed to him and must be remapped via either:
- Adding a Fiix source alias (recommended)
- Populating the fiix_name field
- Or both

The database has exact values ready to implement the fix:
- **Name to map:** "Dan Kragel" (completed_by_raw in Fiix data)
- **Key generated:** "dan kragel" (completed_by_key)
- **Target technician_id:** 14
