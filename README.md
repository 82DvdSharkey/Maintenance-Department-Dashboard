# Maintenance Activity Dashboard (IG + Fiix)

Integrated manufacturing dashboard that combines production performance (IG) and maintenance execution (Fiix) to track KPI compliance and line-level risk.

## Primary KPI Targets

- OEE target: 81.5%
- Availability target: 90.0%

Status logic used in target cards:

- Red: below target
- Yellow: within 2.0% below target
- Green: meets or exceeds target

## Data Inputs

Two independent upload/paste pipelines are supported:

1. IG Production Data
	 - Date
	 - Week
	 - Line / Asset
	 - Shift
	 - Planned Runtime
	 - Unplanned Downtime
	 - Availability
	 - Performance
	 - OEE
2. Fiix Maintenance Data
	 - Completion Timestamp
	 - Week
	 - Line / Asset
	 - Shift
	 - Work Order Number
	 - WO Type
	 - Description / Notes
	 - Time Spent (hours)
	 - Technician

Templates:

- public/templates/ig_production_template.csv
- public/templates/fiix_maintenance_template.csv

## Processing Rules Implemented

- Availability, Performance, and OEE are weighted by Planned Runtime.
- Production rows with zero planned runtime are excluded.
- Production rows with invalid KPI values are excluded:
	- OEE > 150%
	- Performance > 150%
- Fiix work order descriptions are grouped into maintenance categories via keyword rules.
- Week values are auto-generated from date when missing.
- Deduplication keys:
	- IG: Date + Line/Asset + Shift
	- Fiix: Date + Work Order Number

## Dashboard Sections

- Primary target KPI cards (actual vs target with red/yellow/green status)
- Integrated summary cards (runtime, downtime, maintenance hours, reactive WO)
- Weighted production KPI trends by week
- Maintenance workload trend by WO type
- Line KPI risk ranking table
- Maintenance category analysis (table + Pareto)
- Filtered Fiix work order detail table
- Global filters: week, date range, line/asset, shift, technician, WO type

## Weekly Update Workflow

1. Export IG and Fiix weekly datasets as CSV (or copy from Excel).
2. In Data Input, upload or paste IG rows (append or replace).
3. Upload or paste Fiix rows (append or replace).
4. Check target cards and line risk ranking for impact.
5. Use filters to analyze line, shift, and technician contributors.

## Assumptions

- Native .xlsx import is not enabled; use CSV export or paste tab-delimited rows.
- Shift values normalize to 1, 2, 3, or Other.
- Percent fields may be provided as 0-1 or 0-100 and are normalized automatically.
- Description category mapping uses keyword heuristics and can be extended in code.

## Run and Build

```bash
npm install
npm run dev
```

Production build:

```bash
npm run build
```

Latest validation: production build completed successfully.
