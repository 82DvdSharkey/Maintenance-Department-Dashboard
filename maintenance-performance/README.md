# Maintenance Performance KPI Dashboard

A local Windows application for the maintenance department. It imports the weekly **Fiix CMMS** CSV reports and **Ignition** maintenance-call workbooks, stores normalized history in **SQLite**, takes manual **Dayforce hours** and weekly **PM Open vs Closed** counts, calculates technician, shift and department KPIs, and shows them in an interactive **Streamlit** dashboard.

## Requirements

- Windows 10/11
- Python **3.12+**
- Read access to the Fiix and Ignition source folders (for example the `G:` drive)

## Installation

From the `maintenance-performance` folder:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Running the application

```powershell
.venv\Scripts\activate
streamlit run app.py
```

The dashboard opens at <http://localhost:8501>. The Executive Dashboard opens on the most recently imported calendar week.

### Microsoft sign-in and access

Authentication is temporarily disabled because Entra app-registration access is not yet available. While
`AUTHENTICATION_ENABLED` is `False` in `app.py`, the app runs in local full-access mode and does not enforce
the technician privacy restrictions. Keep the app bound to localhost and do not expose it to other users or
networks in this mode.

When Entra registration is available, enable authentication in `app.py`. The app uses Streamlit's OpenID Connect
support with a Microsoft Entra ID app registration. Copy
`.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, then fill in the Entra tenant's OpenID metadata URL,
client ID and client secret, and a unique cookie secret. Register `http://localhost:8501/oauth2callback` as a Web
redirect URI for local use. Generate a cookie secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keep `secrets.toml` private; it is excluded by `.gitignore`. Once enabled, if auth is missing or incomplete, the app
stops at a setup message and does not grant unauthenticated access. Entra sign-in is tenant-scoped by the configured
metadata URL.

On the first database initialization, the app seeds `David.Sharkey@Magna.Com` as the bootstrap administrator if the
separate administrative-user list is empty. Administrators can maintain that list under **Administration →
Administrative Users**. Admins have full access. Other signed-in users can view only Executive, Shift, Ignition and
Fiix Performance; technician-level comparisons, identifiable work-order/call detail, data entry, configuration,
quality, incident review and broad Excel exports remain admin-only. General users cannot filter the dashboards by
technician, and their dashboard PDFs omit person-level sections.

Microsoft sign-in does not configure scheduled email delivery. Sending reports through Outlook still requires a
separately approved Microsoft Graph mail integration.

### Administration and Data Entry password

Until a password is configured, both menus and their functions remain accessible to administrators, including on
upgrades. To enable protection, open **Administration → Password**, fill in the masked **New Password** and
**Confirm Password** fields, and choose **Save Password**. Both fields are required and must match. Invalid entries
or a failed database save show an error and do not enable protection.

A successful save immediately locks both menus and returns to the Executive Dashboard. The menus remain visible;
select either to enter the shared password. **Unlock** opens both menus for the current app session; an incorrect
password or **Cancel** leaves them locked. A new browser/app session or server restart requires unlocking again.
Existing Microsoft sign-in and administrator permissions still apply. Other menus, source refreshes and exports
keep their existing behavior.

To replace a configured password, unlock Administration and open **Password** again. The **Current Password**
must also be verified. Saving a replacement relocks the current session and invalidates other sessions' previous
unlocks on their next access. Protected screens, their action handlers and manual/configuration write operations
check access on the Streamlit server, including when a protected URL is opened directly. There is no separate API.

Only a random salt and PBKDF2-HMAC-SHA256 hash (600,000 iterations) are saved in SQLite's `app_settings` table, not
the password. Keep the configured database on persistent storage and protect its backups and local filesystem.
Password-setting read failures or malformed settings fail closed with an error; they are not treated as fresh
setup. This menu password is not a replacement for Microsoft sign-in, network restrictions or filesystem security.

### Quarterly and annual performance-review export

On **Technician Performance**, select the review subject under **Technician detail**, then use
**Export to PDF** and **Download Technician Review PDF**. This export contains only that person's
performance KPIs, targets and YTD status, with **Q1, Q2, Q3, Q4 and YTD** columns. It does not include
the department technician scorecard, peer names, rankings, comparison charts or work-order details.
The year is taken from the sidebar's selected end week; the cutoff is **today in America/Detroit**.
Past years cover the full year, current periods are marked partial and future quarters are not started.
Period dates and missing-data notices are included. Calendar completion does not certify complete imports.

The review uses calendar dates (Jan 1-Mar 31, Apr 1-Jun 30, Jul 1-Sep 30, Oct 1-Dec 31), independent
of the dashboard's week-based YTD toggle. Sidebar data filters are ignored so a review always covers the
selected person's full activity. Fiix and Ignition records are filtered by their completion/call timestamps.
As agreed for weekly Dayforce data, **full weekly hours are included for every overlapping production
week**, with an explicit report warning. Boundary-week hours can occur in two quarter denominators;
labor efficiency is not an exact calendar-date ratio. YTD is calculated independently using existing KPI
services, never by summing quarter denominators or averaging quarterly percentages.
PM and call shares keep the existing current shift/crew peer definitions, including self, without exporting
peer details. N/A is preserved for unavailable metrics, missing peers and periods that have not started.
Changing the subject, year, targets, date or data version invalidates the previously prepared download.
The separate Administration weekly report remains a single-week report.

### Shift team-lead quarterly and annual reviews

On **Shift Performance**, choose **Area** and **Shift detail**, then open
**Quarterly and annual team-lead review**. Choose a **Review year** and select
**Generate quarterly / annual review**. Preview the team KPIs, individual technician KPIs, roster and
calculation notes, then use **Download Team Lead Review PDF**. This function is administrator-only
because it includes individual performance data. The existing filtered-dashboard PDF remains available.

The review covers **only the selected shift and crew area**, ignoring other sidebar data filters.
It includes Q1-Q4 and independently calculated YTD values, targets and YTD status. Calendar periods,
today's America/Detroit cutoff, partial/future labels and the full-overlapping-week Dayforce warning
match the technician review described above. Past review years show the full year.
The year defaults to the sidebar's selected end-week year and can be changed within the review section.

Team KPIs preserve the existing shift definitions: qualifying pooled labor efficiency excludes leads
and out-of-scope technicians, FTF uses the configured legacy technician/shift aggregation, PMs Closed
is a count, and Ignition durations are call-weighted. Department PM Completion percentages and
individual 33% peer-share targets are not repurposed as team targets.
The individual breakdown includes the existing technician KPIs and peer shares. Zero-activity active
members remain visible, while inactive members are included when their End Date overlaps the year
or they have recorded activity. Team leads are identified in the roster. Team results are not mislabeled
as personal lead KPIs, and personal KPI values need not sum or average to team KPI values.

Shift/crew/lead assignments use the current technician master, not historical assignments.
The existing SMT chart-only anomaly adjustment is **not** applied to review calculations.
Missing-source notices and calendar-period dates are included; a completed calendar period does not
certify that all imports or Dayforce hours are present. Changing shift, area, year, date, data version,
targets or crew-area configuration invalidates the previously prepared review.

### Individual weekly technician PDF reports (Phase 2)

Open **Administration > Technician Reports**, select a reporting calendar week and technician, then choose
**Preview Report** or **Generate PDF**. Both actions use the same individual PDF generator; **Download
Technician PDF** saves that exact PDF through the browser. No report files are written to the server.
Install the updated requirements to include Streamlit's PDF viewer (`streamlit[pdf]`); preview renders
the generated PDF locally, without an external document-viewing service.
The default is the most recently completed production week (Sunday 23:00 boundary by default).
Unfinished weeks require the explicit **Allow partial-week preview** option and are marked as partial.
The reporting period displays exact production-week boundaries, including the exclusive end time.

Reports use the full technician/week independently of sidebar filters, and include the existing dashboard
KPI values, previous week, pooled four-week averages, targets/status, workload, qualifying Fiix labor by
configured classification, FTF repeat links, Ignition area summaries, and 12-week trends. Detail tables are
capped with explicit notices to keep the scorecard compact. Missing values are marked N/A, not invented.
Existing KPI services remain authoritative; no report-specific KPI formulas are used.

**Technician PM completion share:** the technician dashboard and individual reports show
**PM Completion Share (Shift/Crew)** = the technician's PMs Closed divided by the total PMs Closed
by their shift-and-crew group, including the technician, times 100. Eligible group members are active,
non-lead maintenance technicians; peers with zero completions still count as peers. Both Shift and Crew
must match (ignoring case and surrounding spaces). Missing designations, no other eligible peers, or
zero group completions produce N/A with an explanation. Leads/inactive/non-department technicians
are not eligible for this comparison. Grouping uses the current technician master designations.
Person/shift sidebar filters never shrink the denominator; period and other applicable data filters
remain in effect. The four-week value pools own and group completions across four contiguous weeks,
not an average of weekly percentages. The report reveals only the individual's percentage, not peer details.

This is a **share of completed PMs**, not a percentage of assigned PMs completed. Its default target
is 33%, independently configurable from the department PM target in Administration > KPI targets.
Existing **PMs Closed** counts and department/area **PM Completion %** (Closed / Open) remain unchanged.
PM Assigned and PM Remaining are still N/A because assignments are stored by area, not technician.

**Technician maintenance-call share:** **Maintenance Call Share (Shift/Crew)** uses the same eligible
peer grouping as PM completion share. It equals the technician's Ignition maintenance-call count divided
by their shift-and-crew group's total maintenance calls (including self), times 100. Its default target
is **33%**, higher is better, with a five-percentage-point Watch band, configurable under
Administration > KPI targets. Existing maintenance-call counts and department/shift KPIs are unchanged.
The technician scorecard, detail cards, comparisons, trends and PDF reports include this percentage.
The technician KPI card uses this share and its target instead of a separate untargeted call-count
card. Raw call counts remain in the scorecard, Ignition details and report workload summary.
New target defaults are saved automatically on reload, even when the database engine is cached;
existing configured targets are preserved.
Period (including year to date) and IgnAreaName filters apply to both counts; person/shift selections do
not shrink the peer denominator. Four-week shares pool call counts instead of averaging percentages.
Missing designations, no eligible peers or no group calls show N/A with an explanation; zero own calls
with positive group calls show 0%. Reports include only the selected technician's share, not peer details.

FTF uses the same existing work-order repeat-window calculation as Technician Performance, not the
separate reviewer-classified incident FTF. Recent FTF outcomes can change as new repeats are imported.

These PDFs contain confidential employee information. Only the selected technician's data is included,
with no department ranking or other technician's detailed work records. Technician reports are available only to
administrators; general users cannot access the report page.
**Email delivery, email configuration, approvals, delivery history and automatic scheduling are not
implemented or enabled in Phase 2.** No database schema or ingestion changes are required.

Reporting integration points:

| Module | Role |
|---|---|
| `database/models.py`, `database/repository.py` | Existing technician master and SQLite reads; unchanged |
| `kpi/dataset.py`, `kpi/scorecard.py` | Existing filtering/scorecards, with shared technician peer-share and pooled trend integration in the dataset layer |
| `kpi/pm_completion.py` | Department PM calculations plus the distinct technician shift/crew completion-share calculation |
| `kpi/first_time_fix.py`, `kpi/labor_efficiency.py`, `kpi/ignition_metrics.py`, `kpi/targets.py` | Authoritative existing FTF, labor, call and status services; unchanged |
| `utils/dates.py` | Existing production-week boundaries and contiguous historical weeks; unchanged |
| `reporting/weekly_technician.py` | UI-independent individual report data with stable technician ID |
| `reporting/technician_pdf.py` | In-memory PDF renderer; no KPI formulas or email-provider coupling |
| `ui/technician_reports.py`, `pages/administration.py` | Report selection, PDF preview/download and stale-selection invalidation |
| `ui/pdf_reporting.py`, `ui/export_view.py` | Existing dashboard PDF and Excel exports; unchanged |
| `tests/test_weekly_technician_reports.py` | KPI consistency, PDF contents/page range, isolation, calendar and UI tests |

Run the reporting tests with `.venv\Scripts\python.exe -m pytest tests\test_weekly_technician_reports.py`.

## Project layout

| Folder | Purpose |
|---|---|
| `app.py` | Entry point: sidebar (refresh, global filters, export) and navigation |
| `config/settings.json` | Source folders, file patterns, worksheet name, DB path, week convention |
| `database/` | SQLAlchemy models, engine/settings helpers, query and save functions |
| `ingestion/` | Fiix CSV loader, Ignition workbook loader, folder scanning and import tracking |
| `kpi/` | KPI formulas: labor efficiency, first-time fix, PM completion, Ignition metrics, scorecards, data quality |
| `pages/` | Streamlit pages (UI only; no KPI formulas) |
| `ui/` | Shared UI components, cached data context, Excel export assembly |
| `utils/` | Calendar weeks, name normalization, validation, Excel writer |
| `tests/` | pytest unit tests |
| `data/` | SQLite database (`maintenance_metrics.db`) and backups |

## Source directory configuration

The default folders are set in `config/settings.json`:

```json
"fiix_source_dir": "G:\\Maintenance\\$_Metrics\\Fiix\\Weekly Reports",
"ignition_source_dir": "G:\\Maintenance\\$_Metrics\\IGN Call Data",
"fiix_file_patterns": ["*/*.csv"],
"ignition_file_patterns": ["*/*.xlsx", "*/*.xls"]
```

The patterns read the year subfolders (`2024`, `2025`, `2026`, …). A new year folder is picked up automatically and no code changes are needed. You can change the folders, patterns, worksheet name (`Dataset 1`) and department name under **Administration → Data Sources**.

## How weekly file ingestion works

1. Click **Refresh Weekly Data** in the sidebar (also available under Administration).
2. Both source folders are scanned. A file is skipped if a file with the same name, size and modified time was already imported successfully.
3. Each new file is validated. Missing required columns cause the import to fail, and the failure is recorded.
4. Records are cleaned and new records are inserted:
   - **Fiix:**
     - Column names are trimmed, and `WO#`/`TL#` are accepted as `WO`/`TL`.
     - Technician names are trimmed and normalized.
     - Dates are parsed.
     - `Time Spent` is converted to decimal hours.
     - The original text values are kept.
   - **Ignition:**
     - Only the `Dataset 1` worksheet is read; Dataset 2 is ignored.
     - `.xlsx`, legacy SpreadsheetML `.xls` (XML) and binary `.xls` files are supported.
     - Raw durations are converted from seconds to minutes. Nulls and negative values stay null and are never set to zero.
     - All CallTypes are stored, but KPIs use only `CallType = Maintenance` (trimmed, case-insensitive).
5. Duplicates are prevented. A Fiix `WO` or Ignition `ID` that already exists is skipped and logged as a data-quality issue.
6. New Fiix WO Types are added to the WO Type configuration with classification **Other** and flagged for review.
7. Each file gets an import-history row: file name, path, modified date, size, content hash, import time, row count, inserted and duplicate counts, data year/week and status. Warnings are stored with it; the path and content hash identify an unchanged file.
8. The KPI caches are invalidated, so the dashboard updates straight away.

### Calendar weeks

Fiix and Ignition use Year, CalendarWeek, WeekStart and WeekEnd from `utils/dates.py`:

- The week is the **ISO week of (timestamp + 1 hour)**, so a production week starts **Sunday 23:00**. This matches the Fiix and Ignition report boundaries.
- Fiix rows use `Date Completed`, or `Date Created` when that is blank. Ignition rows use `CallTime`.
- The week number in the filename (`FiixCW40`, `26CW1`, `CW-10-Rev.1`, `30CW`) is compared with the main week in the file's data. Differences appear as import warnings.
- The offset can be changed in Administration. Changing it requires a database rebuild.

Dayforce entries use their clock-in time instead: Sunday clock-ins before 9:30 PM belong to the current calendar week, while clock-ins at or after 9:30 PM belong to the following calendar week. The Fiix/Ignition offset setting does not change this Dayforce rule.

This leaves a source-specific boundary: weekly Dayforce hours can shift into the following week 90 minutes before the Fiix/Ignition boundary. Date-range selections also use mixed data grains: Fiix and Ignition are filtered to the selected timestamps, while weekly Dayforce hours and PM Open/Closed counts include each entire production week touched by the date range.

## Technician master table and name mapping

**Administration → Technicians** holds the master list: name, shift, crew, department, supervisor, active flag, Fiix/Ignition/Dayforce names, and start/end dates.

- **Shift comes only from this table.** It is never inferred from work-order or call times. Technicians without a shift are left out of shift-level metrics.
- Fiix `Completed By` and Ignition `TechName` are matched to a technician by exact name (ignoring case and extra spaces). The match can be on the technician name, the source-specific name, or an alias.
- **Administration → Name Mapping** lists unmatched names. Map them to an existing technician as an alias, or create a new technician.
- If a name matches more than one technician, it is ambiguous. It stays unmapped and is listed in Data Quality.
- Rows with no `Completed By` or `TechName` are never assigned to a technician and are excluded from all KPIs (listed in Data Quality).

## Dayforce manual entry

## Dayforce hours worked (automatic import)

Dayforce time-entry reports (`.xlsx`, e.g. *Unauthorized Records Report* with **Show Time Entries**, **Status: All**) placed in `G:\Maintenance\$_Metrics\Dayforce Data` are imported by **Refresh Weekly Data** and the automatic scan.

- **Daily entries:** every daily time entry is stored with employee ID, date, punch in/out, job, pay code and hours.
- **Re-importing:** a report covering the same dates replaces the earlier entries for those employees and dates.
- **Hours worked:** REG + OT hours on WRK, Very Late Out and Very Early In lines.
- **Not hours worked:** Shift Premium, OT 0.5 Memo and PTO (Sick / Time Off) lines. PTO is shown as a comment.
- **Jobs:** **Lead, Maintenance** hours count. **Tool & Die** hours do not. The excluded job titles can be changed in Administration → Data Sources.
- **Week assignment:** each entry is assigned by its **clock-in time**. Sunday clock-ins before 9:30 PM belong to that Sunday’s current calendar week; clock-ins at or after 9:30 PM belong to the following calendar week. Unpunched entries use noon on the recorded work date.
- **Partial weeks:** a week is used only once its Monday–Saturday are covered by imported reports. Weeks with only a Sunday fragment wait for the neighbouring report, so partial hours never inflate Labor Efficiency.
- **Matching technicians:**
  1. By **Dayforce ID** (technician list).
  2. By exact Dayforce name or alias.
  3. By "Last, First" flipped to "First Last".

  Unmatched employees appear in Data Quality and Name Mapping.
- **New employees:** a Dayforce employee who matches no technician is **added automatically** as an active technician. The new record gets their Dayforce ID and name; Lead is ticked if any of their entries are under a Lead job. It is flagged **Needs review**:
  - A banner in the sidebar lists technicians awaiting review.
  - Administration → Technicians shows a review panel. Fill in shift, crew, supervisor and Fiix/Ignition names, then click **Confirm as new technician(s)**.
  - If the person already exists under another name (e.g. "Anthony Ballou" vs "Tony Ballou"), use **Link and remove duplicate**. This moves the Dayforce ID to the existing technician.

**Data Entry → Dayforce Hours Worked** shows the imported hours (read-only *Dayforce Import* column). A manual row saved for a technician and week **overrides** the import for that week:

- `TotalHoursWorked = RegularHours + OvertimeHours + OtherPaidHours − ExcludedHours`
- Enter a **Total Override** to replace the calculated total.
- **Save**, **Copy prior week** (fills technicians who are blank this week), **Bulk fill**, and **Lock finalized week** / **Unlock week**.
- **Import from CSV**. Columns: `Year, CalendarWeek, Technician, RegularHours, OvertimeHours, OtherPaidHours, ExcludedHours, TotalHoursWorked, Comments`. A supplied `TotalHoursWorked` is stored as an override.
- Nonblank numeric CSV cells must contain valid numbers. Invalid values are rejected for that week and do not clear or overwrite saved entries; blank rows continue to remove existing entries.

## PM Open vs Closed entry

**Data Entry → PM Open vs Closed** has one row per area: **Final Assembly** and **SMT**. Each week, enter:

- **PMs Open:** PMs opened (due) for the week.
- **PMs Closed:** how many of the open PMs were closed.

The grid previews each area's PM Completion %. **Save**, **Copy prior week**, **Lock finalized week** / **Unlock week** and **Import from CSV** are available. CSV columns are `Year, CalendarWeek, Area, Open, Closed, Comments`. The area list is stored in the `pm_areas` app setting.

PM Open/Closed values must be nonnegative whole-number counts; fractional counts are rejected.

PM Completion % is a **department KPI** and is not shown for individual technicians or shifts. Technician (and shift) PM performance is **PMs Closed**: the number of Fiix `Preventative (PM)` work orders with a Closed status completed by the technician. Duplicate/admin-only closures are excluded. `kpi/pm_completion.py` takes any frame with `week_key, area, pm_open, pm_closed`, so a future Fiix scheduled-PM report could replace the manual entry.

## KPI definitions

| KPI | Formula |
|---|---|
| **Labor Efficiency %** | Qualifying Fiix `Time Spent` ÷ Dayforce hours worked × 100. Only WO Types marked *Counts toward labor* and mapped technicians are included. No % is shown when worked hours are 0. Weeks without Dayforce hours are left out of the ratio. |
| **Legacy First-Time Fix %** (Fiix Performance) | Preserved work-order-level repeat estimate. It is not reviewed incident-based FTF and must not be presented as verified incident success. |
| **Incident FTF Rate** (First-Time Fix Incidents page) | Successful first-repair incidents ÷ assessed eligible incidents × 100. Incidents are classified/reviewed before assessment; only the initial repair owner/team and initial shift receive attribution. |
| **PM Completion %** (department, per area) | % of open PMs closed: PMs Closed ÷ PMs Open × 100, from the weekly area entries. Reported **separately** for Final Assembly and SMT (separate KPI cards, trends and export columns); areas are never combined. No % is shown when no open PMs are entered. |
| **PMs Closed** (technician / shift) | Count of Fiix `Preventative (PM)` WOs with a Closed status and the technician in `Completed By`. Shift value = sum of its technicians. |
| **Maintenance Calls** | Count of Ignition calls with CallType = Maintenance |
| **Avg Call / Ack / Response / Repair Duration** | Average of `RawCallDuration`, `RawAckDuration`, `RawResponseDuration`, `RawRepairDuration` ÷ 60, in minutes. Nulls are ignored. |

Fiix `Time Spent` values below zero and malformed `H:MM` values (minutes outside 00–59) are excluded from Labor Efficiency and appear in Data Quality.

How levels are aggregated:

- Shift and department figures are calculated from the underlying hours, work orders and individual calls. They are **never** a plain average of technician averages.
- Rolling 4-week values pool the four weeks ending at the current week.
- Labor Efficiency pairs each technician's Fiix hours with that technician's own Dayforce hours. **Department and shift Labor Efficiency exclude technicians marked Lead or inactive** in Administration → Technicians; those technicians still appear individually.
- **Only Dayforce-mapped data is included.** The maintenance department is the technicians mapped to a Dayforce record (imported or entered manually); a Crew alone does not qualify. Fiix work orders and Ignition calls from anyone else (other departments, unmapped or blank names) are excluded from every KPI and dashboard, but remain visible in Data Quality and in the Ignition page's *Excluded Calls* count. Technicians are included automatically once they appear in Dayforce.
- **Inactive technicians with an End Date** are kept for all weeks up to and including the End Date week and excluded after it; inactive technicians without an End Date are excluded from all weeks.
- PM Completion % (manual area entries) is not affected by Dayforce mapping.

### Incident-based First-Time Fix policy

The **First-Time Fix Incidents** page is the reviewed incident-based measure. The existing Fiix Performance FTF/repeat analysis remains available for continuity but is a work-order-level estimate, not verified incident FTF.

- **Source fields actually available:** Fiix reports provide `WO`, `TL`, `Parent Asset`, `Asset`, `Task Description`, task/completion notes, `Completed By`, `Date Created`, `Date Completed`, `Status`, and `WO Type`. They do not provide structured component, failure mode, cause, repair start/end, return-to-service evidence, or primary owner/team. Ignition Maintenance call reports provide `ID`, `IgnMachineName`/`MachineName`, `EventDescription`, `TechName`, `CallTime`, `ResponseTime`, `ArrivalTime`, `CompleteTime`, and durations, but no Fiix WO ID or asset TL#. These unavailable fields must be classified or linked by a reviewer; source descriptions are only reported symptoms, not assumed failure diagnoses.
- **Equipment matching:** only the individual Fiix `TL`/`TL#` identifies equipment. Normalization trims surrounding whitespace and case-folds while preserving leading zeros and punctuation. Never substitute `Asset`, Parent Asset, production line, FF group, Ignition area, or area for a missing TL#. Missing TL# remains in classification/review. Different TL# values are separate assets even if their parent or descriptions match.
- **Repeat suggestions and relationships:** suggestions require the same normalized TL#, same reviewer-classified failure mode, and the same component/sub-asset when both components exist. Matching does not confirm a repeat. Reviewers can confirm an in-window repeat, record recurrence outside the window, link records describing the same initial intervention, classify an independent failure, or leave a relationship unresolved. Text similarity is not used as automatic evidence. Confirmed repeats and duplicate source links are attached to the original incident, so multiple follow-up WOs do not create extra denominator incidents. A different diagnosis may remain part of the same incident when a reviewer confirms the connection.
- **Incident assessment:** Fiix reactive WOs are idempotently seeded into a classification queue. Required classification includes individual TL#, symptom, failure mode, intervention start, return-to-service timestamp, and confirmation that return-to-service is documented. No incomplete record can silently be a success. Continuous repair work with multiple WOs/technicians/shift handoffs requires review as one initial intervention or follow-up relationship; imports do not infer intervention boundaries.
- **Verification:** default window is seven calendar days after documented return to service; this is an internal policy, not an industry standard. The default and low-volume warning threshold are configurable in the FTF page. Each incident preserves its own verification method/window snapshot. Neither export contains a reliable runtime or cycle counter, so operating-hour/cycle verification is unavailable; elapsed time is not fabricated into operating time.
- **Statuses and maturity:** incidents show Needs classification, Open / in progress, Pending verification, Successful first-time fix, Failed first-time fix, Excluded, or Unresolved review. Cohort is the week of the first intervention end; open/unended work remains visible as unassigned. Pending verification, unresolved review, and known failures awaiting maturity/coverage are shown separately. A finalized success requires elapsed verification time and explicit complete-through attestations for both Fiix and Ignition through the deadline. Import recency is not treated as source completeness.
- **Reporting:** outcomes belong to the primary initial technician or shared repair team and initial shift. Group percentages use pooled successes ÷ assessed incidents, not averages of group percentages. Four-week baseline pools the prior four cohorts. Department, shift, technician/team, TL#, component, failure mode and reporting-week breakdowns are available; parent asset, production group and Ignition area are report-only filters/dimensions and never used for matching. Sample-size warning is configurable; zero denominator displays N/A. Displayed data can be exported to CSV.
- **Review/audit and source linking:** reviewer decisions and corrections are audit-logged; source links show source ID, file, timestamp and description. Ignition calls are filtered to existing Maintenance calls. Cross-source records are never merged based on a matching name alone: reviewers first verify an explicit Ignition machine label → individual TL# mapping, then explicitly link the records as the same initial intervention. Typed reviewer names are not authentication; protect this local app with an authorized access layer before production use.
- **Failure reasons:** Diagnosis, Workmanship, Parts unavailable, Temporary repair, Tools/information unavailable, Other, and Unknown/pending review are recorded separately. Eligible process failures remain in overall incident FTF regardless of reason; outcome is attributed to the initial repair owner/team, not whoever later identifies the cause or repairs the recurrence.

Targets and watch bands are set under **Administration → KPI Targets & Settings**:

- Status shows **On Target**, **Watch**, or **Below/Above Target**, each with text and an icon.
- Durations are better when lower. Percentages are better when higher.
- The same page holds the FTF rules (window 1–30 days, asset rule, exclusions), the labor-efficiency warning threshold and the number of trend weeks. Qualifying FTF WO Types are set on the **WO Types** tab.

## Dashboard pages

- **Executive Dashboard:** KPI cards (current week, previous week, Δ, 4-week rolling average, target, status), weekly trends, shift and technician comparison, area Pareto and repair time by area.
- **Technician Performance:** the combined technician scorecard (all columns sortable) and a technician detail profile with 12-week trends and drill-down tables.
- **Shift Performance:** the shift scorecard and shift detail.
- **Ignition Performance:** technician table, grouping, IgnAreaName → IgnMachineName → individual-call drill-down, Pareto and weekly trends.
- **Fiix Performance:** labor breakdown by WO Type, classification, technician, shift, FF Group, asset, parent asset and week. Also labor flags, First-Time Fix, repeat-failure drill-down, PM completion and work-order list.
- **First-Time Fix Incidents:** incident cohort reporting, source evidence, explicit asset mappings, incident classification and reviewer-confirmed relationship queue.
- **Data Entry:** Dayforce hours and PM Open vs Closed by area.
- **Data Quality:** missing technicians, unmapped and ambiguous names, missing Dayforce or PM entries, duplicates, invalid dates and durations, failed imports.
- **Administration:** sources, technicians, name mapping, shifts, WO Types, targets and settings, import history, backup and rebuild.

The global filters in the sidebar stay the same on every page:

- Year
- Calendar week, week range or date range
- Shift
- Technician
- FF Group: also filters all Ignition call metrics through FocusFactory (FA = DAS1, SMT = SMT, Post SMT = Post SMT or Post-SMT). Multiple groups combine their calls. With no group selected, all factories remain in scope. This applies to call counts, call share, MTTA, response time, MTTR, total call duration, trends, comparisons, drill-downs and exports. The executive FF Group selector overrides the sidebar group selection.
- IgnAreaName
- WO Type

**Export to Excel** in the sidebar creates a workbook for the current filters with these sheets: Department KPIs, Shift KPIs, Technician KPIs, Fiix Detail, Ignition Detail and Data Quality.

Each reporting dashboard also has an **Export to PDF** button. Choose the global period and filters in the sidebar, then apply any dashboard-specific area, shift, technician, cohort, or drill-down selections before exporting. Click **Export to PDF** to generate the current report, then click **Download PDF** to save it. The PDF uses those same filtered calculations and includes its reporting period, active filters, KPI comparisons, charts, and relevant tables. PDFs are generated in memory only when requested; they are not emailed or uploaded, and local filesystem paths are omitted.

PDF timestamps use America/Detroit time. Long tables repeat their header row across pages and include page numbers. An empty selection still produces a one-page report that clearly states there is no data. The legacy Fiix/Technician/Shift First-Time Fix value is labeled as a work-order-level asset repeat estimate; the First-Time Fix Incidents report separately marks incident-based results provisional until verification and source coverage requirements are met.

## Database

- **Location:** `data/maintenance_metrics.db`. You can change it with `database_path` in `config/settings.json`; relative paths are resolved from the project folder.
- **Tables:**
  - `technicians`, `technician_aliases`, `shifts`
  - `fiix_work_orders`, `ignition_calls`
  - `dayforce_hours`, `pm_area_status`, `week_locks` (`pm_targets` is legacy and no longer used)
  - `wo_type_config`, `kpi_targets`, `app_settings`
  - `file_import_history`, `data_quality_issues`

### Backing up

- **Administration → Backup & Rebuild → Create backup copy** writes a consistent snapshot to `data/backups/`.
- You can also stop the app and copy `data/maintenance_metrics.db`.

Manual entries (Dayforce hours, PM Open vs Closed, technicians, aliases) exist **only** in this database, so back it up regularly.

### Rebuilding

**Administration → Backup & Rebuild → Rebuild database** requires a checkbox and typing `REBUILD`.

- It deletes the imported Fiix and Ignition records, the import history and the import issues, then re-imports every file from the source folders.
- Configuration, technicians, aliases, Dayforce hours, PM Open vs Closed entries, targets and settings are kept.

## Running tests

```powershell
.venv\Scripts\activate
python -m pytest
```

The tests cover:

- Labor efficiency (40 ÷ 50 = 80%)
- Seconds-to-minutes conversion (600 s = 10 min, 90 s = 1.5 min)
- Call-weighted shift and department averages
- First-time fix (no repeat, repeat inside the window, repeat outside the window, configurable window, exclusions, parent-asset fallback)
- PM completion (9 ÷ 10 = 90%)
- Calendar weeks and filename parsing
- Name ambiguity
- Target status
- Scorecard aggregation
