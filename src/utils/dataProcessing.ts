import Papa from 'papaparse'
import type {
  DashboardTargetCard,
  FilterState,
  KpiStatus,
  MaintenanceRecord,
  MaintenanceWorkOrderType,
  ParetoPoint,
  ProductionRecord,
  ShiftType,
} from '../types/maintenance'

type RawRow = Record<string, unknown>

type FilterOptions = {
  weeks: string[]
  lines: string[]
  shifts: string[]
  technicians: string[]
  woTypes: string[]
}

const productionAliases: Record<string, string[]> = {
  // Real IG export columns mapped first, generic fallbacks follow.
  date: ['day', 'date', 'clientdatecode'],
  week: ['week', 'iso week'],
  lineAsset: ['line name', 'line / asset', 'line', 'asset', 'line asset', 'mes name', 'line name short'],
  shift: ['shift', 'shift name'],
  plannedRuntime: [
    'planned run time',
    'planned runtime',
    'planned run time (min)',
    'planned run time (mins)',
    'planned run time min',
    'planned run time mins',
    'planned run',
    'runtime planned',
    'planned production time',
    'planned time',
    'planned minutes',
  ],
  unplannedDowntime: [
    'unplanned downtime',
    'unplanned down time',
    'unplanned downtime (min)',
    'unplanned downtime (mins)',
    'unplanne downtime',
    'downtime',
    'down time',
  ],
  availability: ['availability', 'availability %', 'avail', 'line availability'],
  performance: ['performance', 'performance %', 'perf'],
  oee: ['oee', 'oee %', 'actual oee'],
}

const maintenanceAliases: Record<string, string[]> = {
  // Real Fiix export columns mapped first, generic fallbacks follow.
  completionTimestamp: ['date completed', 'completion timestamp', 'completed at', 'completion time'],
  week: ['week', 'iso week'],
  // Parent Asset is the production line; Asset is the specific equipment.
  parentAsset: ['parent asset', 'line / asset', 'line', 'line asset'],
  subAsset: ['asset', 'sub asset', 'asset name', 'equipment', 'child asset'],
  lineAsset: ['parent asset', 'line / asset', 'line', 'asset', 'line asset'],
  workOrderNumber: ['wo', 'work order number', 'work order', 'wo number'],
  woType: ['wo type', 'work order type', 'type'],
  status: ['status', 'wo status', 'work order status', 'state'],
  // Task Description is the primary description; Completion Notes is supplementary.
  description: ['task description', 'description / notes', 'description', 'notes', 'task notes completion'],
  timeSpentHours: ['time spent', 'time spent (hours)', 'time spent hours', 'hours'],
  technician: ['completed by', 'technician', 'tech'],
  shift: ['shift'],
}

function cleanColumnName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[_-]/g, ' ')
    .replace(/\s+/g, ' ')
}

function getTextValue(row: RawRow, aliases: string[]): string {
  const normalizedAliases = aliases.map((alias) => cleanColumnName(alias))

  // 1) Exact normalized alias match.
  for (const [key, value] of Object.entries(row)) {
    if (normalizedAliases.includes(cleanColumnName(key))) {
      return `${value ?? ''}`.trim()
    }
  }

  // 2) Fallback for truncated headers with strict similarity checks.
  for (const [key, value] of Object.entries(row)) {
    const normalizedKey = cleanColumnName(key)
    const compactKey = normalizedKey.replace(/\s+/g, '')
    const fuzzyMatch = normalizedAliases.some((alias) => {
      const compactAlias = alias.replace(/\s+/g, '')

      // Avoid broad matches such as short tokens like "line" or "id".
      if (compactKey.length < 6 || compactAlias.length < 6) {
        return false
      }

      // Accept only prefix-like truncation matches (e.g. "availabili" vs "availability").
      return compactKey.startsWith(compactAlias) || compactAlias.startsWith(compactKey)
    })

    if (fuzzyMatch) {
      return `${value ?? ''}`.trim()
    }
  }

  return ''
}

function toNumber(value: string): number {
  const cleaned = value.replace(/[^0-9.-]/g, '')
  const parsed = Number(cleaned)
  return Number.isFinite(parsed) ? parsed : 0
}

function toIsoDate(value: string): string {
  if (!value) {
    return ''
  }

  // Handle M/D/YYYY or M/D/YYYY H:MM formats (IG and Fiix exports).
  // Parsed directly to avoid UTC-to-local timezone shift from new Date().
  const mdyMatch = /^(\d{1,2})\/(\d{1,2})\/(\d{4})/.exec(value.trim())
  if (mdyMatch) {
    const year = mdyMatch[3]
    const month = mdyMatch[1].padStart(2, '0')
    const day = mdyMatch[2].padStart(2, '0')
    return `${year}-${month}-${day}`
  }

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return ''
  }

  return parsed.toISOString().slice(0, 10)
}

function getIsoWeek(date: Date): string {
  // Use UTC methods so calculation is correct when date was constructed as UTC midnight
  // (e.g. new Date('2026-06-16') creates UTC midnight, not local midnight).
  const copy = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
  const day = copy.getUTCDay() || 7
  copy.setUTCDate(copy.getUTCDate() + 4 - day)
  const yearStart = new Date(Date.UTC(copy.getUTCFullYear(), 0, 1))
  const weekNo = Math.ceil((((copy.getTime() - yearStart.getTime()) / 86400000) + 1) / 7)
  return `${copy.getUTCFullYear()}-W${String(weekNo).padStart(2, '0')}`
}

function normalizeShift(value: string): ShiftType {
  const normalized = value.toLowerCase()
  if (normalized.includes('1')) {
    return '1'
  }
  if (normalized.includes('2')) {
    return '2'
  }
  if (normalized.includes('3')) {
    return '3'
  }
  return 'Other'
}

function toPercent(value: string): number {
  const parsed = toNumber(value)
  if (parsed <= 1) {
    return parsed * 100
  }
  return parsed
}

function normalizeWoType(value: string): MaintenanceWorkOrderType {
  // Match longest / most specific patterns first to avoid false positives.
  const trim = value.trim()

  const mappings: Array<{ test: RegExp; value: MaintenanceWorkOrderType }> = [
    { test: /reactive.*(o\.?e\.?|oe).*train/i,                 value: 'Reactive-O.E. Training' },
    { test: /reactive.*(o\.?e\.?|oe).*(start|shut)/i,          value: 'Reactive-O.E. Start-Up/Shutdown' },
    { test: /reactive.*breakdown|reactive.*break/i,             value: 'Reactive-Breakdown' },
    { test: /reactive.*control/i,                               value: 'Reactive-Controls' },
    { test: /reactive.*eng(ineering)?/i,                        value: 'Reactive-ENG' },
    { test: /reactive.*process|reactive.*check/i,               value: 'Reactive-Process Checks' },
    // Reactive-O.E. standalone: matches "Reactive-O.E." with no further qualifier
    { test: /^reactive-o\.?e\.?$/i,                            value: 'Reactive-O.E.' },
    { test: /reactive.*o\.?e\.?/i,                             value: 'Reactive-O.E.' },
    { test: /^reactive$/i,                                      value: 'Reactive' },
    { test: /reactive/i,                                        value: 'Reactive' },
    { test: /preventat|preventiv|\bpm\b/i,                     value: 'Preventative (PM)' },
    { test: /changeover/i,                                      value: 'Changeover' },
    { test: /consumable/i,                                      value: 'Consumables' },
    { test: /tool\s*room/i,                                     value: 'Tool Room' },
    { test: /admin/i,                                           value: 'Administration' },
    { test: /corrective/i,                                      value: 'Corrective' },
    { test: /proactive/i,                                       value: 'Proactive' },
    { test: /rebuild|overhaul/i,                                value: 'Rebuild' },
    // Standalone types (not prefixed with Reactive)
    { test: /start.?up|shut.?down/i,                            value: 'Start-Up/Shutdown' },
    { test: /\bengineering\b/i,                                 value: 'Engineering' },
    { test: /\btraining\b/i,                                    value: 'Training' },
  ]

  for (const mapping of mappings) {
    if (mapping.test.test(trim)) {
      return mapping.value
    }
  }

  return 'Other'
}

function normalizeWorkOrderStatus(value: string): 'Open' | 'Closed' | 'Open-Ongoing' {
  const normalized = value.trim().toLowerCase()
  if (!normalized) {
    return 'Closed'
  }
  if (normalized.includes('ongoing') || normalized.includes('in progress')) {
    return 'Open-Ongoing'
  }
  if (normalized.includes('open')) {
    return 'Open'
  }
  if (normalized.includes('close') || normalized.includes('complete') || normalized.includes('done')) {
    return 'Closed'
  }
  return 'Closed'
}

function categorizeDescription(value: string): string {
  const normalized = value.toLowerCase()

  const categoryRules: Array<{ test: RegExp; category: string }> = [
    { test: /motor|sensor|photo|control|electrical|wiring|power/, category: 'Electrical / Controls' },
    { test: /gear|bearing|belt|chain|conveyor|mechanical|vibration/, category: 'Mechanical Drive' },
    { test: /hydraulic|leak|pump/, category: 'Hydraulic' },
    { test: /pneumatic|air|valve/, category: 'Pneumatic' },
    { test: /lube|lubrication|grease|oil/, category: 'Lubrication' },
    { test: /calibration|quality|process|support/, category: 'Process / Quality' },
  ]

  for (const rule of categoryRules) {
    if (rule.test.test(normalized)) {
      return rule.category
    }
  }

  return 'General Maintenance'
}

function normalizeProductionRow(row: RawRow, index: number): ProductionRecord | null {
  const date = toIsoDate(getTextValue(row, productionAliases.date))
  if (!date) {
    return null
  }

  // Exclude ASSEMBLY and PACKOUT aggregate/rollup rows — they sum all lines and
  // would double-count runtime and downtime in weighted KPI calculations.
  const lineAsset = getTextValue(row, productionAliases.lineAsset) || 'Unknown Line'
  if (lineAsset === 'ASSEMBLY' || lineAsset === 'PACKOUT') {
    return null
  }

  const plannedRuntime = toNumber(getTextValue(row, productionAliases.plannedRuntime))
  if (plannedRuntime <= 0) {
    return null
  }

  const availability = toPercent(getTextValue(row, productionAliases.availability))
  const performance = toPercent(getTextValue(row, productionAliases.performance))
  const oee = toPercent(getTextValue(row, productionAliases.oee))

  if (performance > 150 || oee > 150) {
    return null
  }

  const week = getTextValue(row, productionAliases.week) || getIsoWeek(new Date(date))

  return {
    id: `IG-${date}-${index}`,
    date,
    week,
    lineAsset,
    shift: normalizeShift(getTextValue(row, productionAliases.shift)),
    plannedRuntime,
    unplannedDowntime: toNumber(getTextValue(row, productionAliases.unplannedDowntime)),
    availability,
    performance,
    oee,
  }
}

function deriveShiftFromTimestamp(timestamp: string): ShiftType {
  // Shift 1: 06:00-13:59  Shift 2: 14:00-21:59  Shift 3: 22:00-05:59
  const match = /[T\s](\d{1,2}):(\d{2})/.exec(timestamp)
  if (!match) {
    return 'Other'
  }

  const hour = parseInt(match[1], 10)
  if (hour >= 6 && hour < 14) {
    return '1'
  }
  if (hour >= 14 && hour < 22) {
    return '2'
  }
  return '3'
}

function normalizeMaintenanceRow(row: RawRow, index: number): MaintenanceRecord | null {
  const completionTimestamp = getTextValue(row, maintenanceAliases.completionTimestamp)
  const date = toIsoDate(completionTimestamp)
  if (!date) {
    return null
  }

  const description = getTextValue(row, maintenanceAliases.description) || 'No description'
  const week = getTextValue(row, maintenanceAliases.week) || getIsoWeek(new Date(date))

  const explicitShift = getTextValue(row, maintenanceAliases.shift)
  const shift = explicitShift
    ? normalizeShift(explicitShift)
    : deriveShiftFromTimestamp(completionTimestamp)

  // Fallback logic required by dashboard:
  // - Parent Asset prefers "Parent Asset" and falls back to line-like columns.
  // - Sub-Asset prefers "Asset" and then falls back to parent when missing.
  const parentAsset = getTextValue(row, maintenanceAliases.parentAsset)
    || getTextValue(row, maintenanceAliases.lineAsset)
    || 'Unknown Parent Asset'
  const subAsset = getTextValue(row, maintenanceAliases.subAsset)
    || parentAsset

  return {
    id: `FIIX-${date}-${index}`,
    completionTimestamp,
    date,
    week,
    parentAsset,
    subAsset,
    lineAsset: parentAsset,
    workOrderNumber: getTextValue(row, maintenanceAliases.workOrderNumber) || `WO-${index + 1}`,
    woType: normalizeWoType(getTextValue(row, maintenanceAliases.woType)),
    status: normalizeWorkOrderStatus(getTextValue(row, maintenanceAliases.status)),
    description,
    category: categorizeDescription(description),
    timeSpentHours: toNumber(getTextValue(row, maintenanceAliases.timeSpentHours)),
    technician: getTextValue(row, maintenanceAliases.technician) || 'Unassigned',
    shift,
  }
}

export function parseDataText(text: string): RawRow[] {
  const firstLine = text.split(/\r?\n/, 1)[0] ?? ''
  const tabCount = (firstLine.match(/\t/g) ?? []).length
  const commaCount = (firstLine.match(/,/g) ?? []).length
  const usesTabs = tabCount > commaCount

  const result = Papa.parse<RawRow>(text, {
    header: true,
    skipEmptyLines: true,
    delimiter: usesTabs ? '\t' : '',
    transformHeader: (header: string) => header.trim(),
  })

  return result.data
}

function hasColumnMatch(columns: string[], aliases: string[]): boolean {
  const normalizedColumns = columns.map((column) => cleanColumnName(column))
  const normalizedAliases = aliases.map((alias) => cleanColumnName(alias))

  return normalizedColumns.some((column) => {
    const compactColumn = column.replace(/\s+/g, '')
    return normalizedAliases.some((alias) => {
      const compactAlias = alias.replace(/\s+/g, '')
      if (column === alias) {
        return true
      }
      if (compactColumn.length < 6 || compactAlias.length < 6) {
        return false
      }
      return compactColumn.startsWith(compactAlias) || compactAlias.startsWith(compactColumn)
    })
  })
}

export function getProductionColumnWarnings(rawRows: RawRow[]): string[] {
  const firstRow = rawRows[0]
  if (!firstRow) {
    return ['IG upload is empty or unreadable.']
  }

  const columns = Object.keys(firstRow)
  const requiredFields: Array<{ key: keyof typeof productionAliases; label: string }> = [
    { key: 'date', label: 'Date (Day)' },
    { key: 'lineAsset', label: 'Line Name' },
    { key: 'shift', label: 'Shift' },
    { key: 'plannedRuntime', label: 'Planned Runtime' },
    { key: 'unplannedDowntime', label: 'Unplanned Downtime' },
    { key: 'availability', label: 'Availability' },
    { key: 'performance', label: 'Performance' },
    { key: 'oee', label: 'OEE' },
  ]

  const missing = requiredFields
    .filter((field) => !hasColumnMatch(columns, productionAliases[field.key]))
    .map((field) => `IG missing required column mapping: ${field.label}`)

  return missing
}

export function getMaintenanceColumnWarnings(rawRows: RawRow[]): string[] {
  const firstRow = rawRows[0]
  if (!firstRow) {
    return ['Fiix upload is empty or unreadable.']
  }

  const columns = Object.keys(firstRow)
  const requiredFields: Array<{ key: keyof typeof maintenanceAliases; label: string }> = [
    { key: 'completionTimestamp', label: 'Date Completed' },
    { key: 'parentAsset', label: 'Parent Asset' },
    { key: 'workOrderNumber', label: 'WO Number' },
    { key: 'woType', label: 'WO Type' },
    { key: 'description', label: 'Task Description' },
    { key: 'timeSpentHours', label: 'Time Spent' },
    { key: 'technician', label: 'Technician (Completed By)' },
  ]

  const missing = requiredFields
    .filter((field) => !hasColumnMatch(columns, maintenanceAliases[field.key]))
    .map((field) => `Fiix missing required column mapping: ${field.label}`)

  return missing
}

export function normalizeProductionRows(rawRows: RawRow[]): ProductionRecord[] {
  return rawRows
    .map((row, index) => normalizeProductionRow(row, index))
    .filter((row): row is ProductionRecord => row !== null)
    .sort((a, b) => a.date.localeCompare(b.date))
}

export function normalizeMaintenanceRows(rawRows: RawRow[]): MaintenanceRecord[] {
  return rawRows
    .map((row, index) => normalizeMaintenanceRow(row, index))
    .filter((row): row is MaintenanceRecord => row !== null)
    .sort((a, b) => a.date.localeCompare(b.date))
}

export function appendProductionDeduped(
  current: ProductionRecord[],
  incoming: ProductionRecord[],
): ProductionRecord[] {
  const map = new Map<string, ProductionRecord>()

  for (const row of current) {
    map.set(`${row.date}|${row.lineAsset}|${row.shift}`, row)
  }

  for (const row of incoming) {
    map.set(`${row.date}|${row.lineAsset}|${row.shift}`, row)
  }

  return [...map.values()].sort((a, b) => a.date.localeCompare(b.date))
}

export function appendMaintenanceDeduped(
  current: MaintenanceRecord[],
  incoming: MaintenanceRecord[],
): MaintenanceRecord[] {
  const map = new Map<string, MaintenanceRecord>()

  for (const row of current) {
    map.set(`${row.date}|${row.workOrderNumber}`, row)
  }

  for (const row of incoming) {
    map.set(`${row.date}|${row.workOrderNumber}`, row)
  }

  return [...map.values()].sort((a, b) => a.date.localeCompare(b.date))
}

export function getFilterOptions(
  productionRows: ProductionRecord[],
  maintenanceRows: MaintenanceRecord[],
): FilterOptions {
  const setFrom = (values: string[]) => [...new Set(values)].sort((a, b) => a.localeCompare(b))

  return {
    weeks: setFrom([...productionRows.map((row) => row.week), ...maintenanceRows.map((row) => row.week)]),
    lines: setFrom([
      ...productionRows.map((row) => row.lineAsset),
      ...maintenanceRows.map((row) => row.lineAsset),
    ]),
    shifts: setFrom([...productionRows.map((row) => row.shift), ...maintenanceRows.map((row) => row.shift)]),
    technicians: setFrom(maintenanceRows.map((row) => row.technician)),
    woTypes: setFrom(maintenanceRows.map((row) => row.woType)),
  }
}

export function applyProductionFilters(
  rows: ProductionRecord[],
  filters: FilterState,
): ProductionRecord[] {
  return rows.filter((row) => {
    if (filters.week !== 'All' && row.week !== filters.week) {
      return false
    }
    if (filters.lineAsset !== 'All' && row.lineAsset !== filters.lineAsset) {
      return false
    }
    if (filters.shift !== 'All' && row.shift !== filters.shift) {
      return false
    }
    if (filters.startDate && row.date < filters.startDate) {
      return false
    }
    if (filters.endDate && row.date > filters.endDate) {
      return false
    }
    return true
  })
}

export function applyMaintenanceFilters(
  rows: MaintenanceRecord[],
  filters: FilterState,
): MaintenanceRecord[] {
  return rows.filter((row) => {
    if (filters.week !== 'All' && row.week !== filters.week) {
      return false
    }
    if (filters.lineAsset !== 'All' && row.lineAsset !== filters.lineAsset) {
      return false
    }
    if (filters.shift !== 'All' && row.shift !== filters.shift) {
      return false
    }
    if (filters.technician !== 'All' && row.technician !== filters.technician) {
      return false
    }
    if (filters.woType !== 'All' && row.woType !== filters.woType) {
      return false
    }
    if (filters.startDate && row.date < filters.startDate) {
      return false
    }
    if (filters.endDate && row.date > filters.endDate) {
      return false
    }
    return true
  })
}

function weightedAverage(rows: ProductionRecord[], key: 'availability' | 'performance' | 'oee'): number {
  const totalRuntime = rows.reduce((acc, row) => acc + row.plannedRuntime, 0)
  if (totalRuntime === 0) {
    return 0
  }

  const weighted = rows.reduce((acc, row) => acc + row[key] * row.plannedRuntime, 0)
  return weighted / totalRuntime
}

export function getKpiStatus(actual: number, target: number): KpiStatus {
  if (actual >= target) {
    return 'green'
  }
  if (actual >= target - 2) {
    return 'yellow'
  }
  return 'red'
}

export function getTargetCards(
  productionRows: ProductionRecord[],
  oeeTarget: number,
  availabilityTarget: number,
): DashboardTargetCard[] {
  const oeeActual = weightedAverage(productionRows, 'oee')
  const availabilityActual = weightedAverage(productionRows, 'availability')

  return [
    {
      key: 'oee',
      label: 'OEE',
      actual: oeeActual,
      target: oeeTarget,
      variance: oeeActual - oeeTarget,
      status: getKpiStatus(oeeActual, oeeTarget),
    },
    {
      key: 'availability',
      label: 'Availability',
      actual: availabilityActual,
      target: availabilityTarget,
      variance: availabilityActual - availabilityTarget,
      status: getKpiStatus(availabilityActual, availabilityTarget),
    },
  ]
}

function groupProductionByWeek(rows: ProductionRecord[]): Map<string, ProductionRecord[]> {
  return rows.reduce((acc, row) => {
    const bucket = acc.get(row.week)
    if (bucket) {
      bucket.push(row)
    } else {
      acc.set(row.week, [row])
    }
    return acc
  }, new Map<string, ProductionRecord[]>())
}

function groupMaintenanceByWeek(rows: MaintenanceRecord[]): Map<string, MaintenanceRecord[]> {
  return rows.reduce((acc, row) => {
    const bucket = acc.get(row.week)
    if (bucket) {
      bucket.push(row)
    } else {
      acc.set(row.week, [row])
    }
    return acc
  }, new Map<string, MaintenanceRecord[]>())
}

export function getWeightedWeeklyTrend(rows: ProductionRecord[]) {
  const byWeek = groupProductionByWeek(rows)
  const weeks = [...byWeek.keys()].sort((a, b) => a.localeCompare(b))

  return weeks.map((week) => {
    const weekRows = byWeek.get(week) ?? []
    return {
      week,
      availability: weightedAverage(weekRows, 'availability'),
      performance: weightedAverage(weekRows, 'performance'),
      oee: weightedAverage(weekRows, 'oee'),
      unplannedDowntime: weekRows.reduce((acc, row) => acc + row.unplannedDowntime, 0),
    }
  })
}

export type LineShiftTrendRow = {
  week: string
  lineAsset: string
  shift: ShiftType
  availability: number
  oee: number
}

export function getLineShiftWeeklyTrend(rows: ProductionRecord[]): LineShiftTrendRow[] {
  const grouped = rows.reduce((acc, row) => {
    const key = `${row.lineAsset}|${row.shift}|${row.week}`
    const bucket = acc.get(key)
    if (bucket) {
      bucket.push(row)
    } else {
      acc.set(key, [row])
    }
    return acc
  }, new Map<string, ProductionRecord[]>())

  const result: LineShiftTrendRow[] = []

  for (const [key, bucket] of grouped.entries()) {
    const [lineAsset, shift, week] = key.split('|')
    result.push({
      week,
      lineAsset,
      shift: shift as ShiftType,
      availability: weightedAverage(bucket, 'availability'),
      oee: weightedAverage(bucket, 'oee'),
    })
  }

  return result.sort((a, b) => {
    const lineCompare = a.lineAsset.localeCompare(b.lineAsset)
    if (lineCompare !== 0) {
      return lineCompare
    }
    const shiftCompare = a.shift.localeCompare(b.shift)
    if (shiftCompare !== 0) {
      return shiftCompare
    }
    return a.week.localeCompare(b.week)
  })
}

// All WO type keys used in charts and aggregations.
const WO_TYPE_KEYS = [
  'Reactive-Breakdown',
  'Reactive-Controls',
  'Reactive-Process Checks',
  'Reactive-ENG',
  'Reactive-O.E.',
  'Reactive-O.E. Start-Up/Shutdown',
  'Reactive-O.E. Training',
  'Reactive',
  'Preventative (PM)',
  'Corrective',
  'Proactive',
  'Rebuild',
  'Changeover',
  'Consumables',
  'Tool Room',
  'Administration',
  'Start-Up/Shutdown',
  'Engineering',
  'Training',
  'Other',
] as const

export { WO_TYPE_KEYS }

export function getMaintenanceTypeByWeek(rows: MaintenanceRecord[]) {
  const byWeek = groupMaintenanceByWeek(rows)
  const weeks = [...byWeek.keys()].sort((a, b) => a.localeCompare(b))

  return weeks.map((week) => {
    const weekRows = byWeek.get(week) ?? []
    const entry: Record<string, number> = { week: 0 }

    for (const key of WO_TYPE_KEYS) {
      entry[key] = weekRows.filter((row) => row.woType === key).length
    }

    return { ...entry, week } as Record<string, string | number> & { week: string }
  })
}

export type WorkOrderTypesByParentAssetRow = {
  parentAsset: string
  totalWorkOrders: number
  totalHours: number
} & Record<(typeof WO_TYPE_KEYS)[number], number>

export function getWorkOrderTypesByParentAsset(
  rows: MaintenanceRecord[],
): WorkOrderTypesByParentAssetRow[] {
  const grouped = rows.reduce((acc, row) => {
    const existing = acc.get(row.lineAsset)
    if (existing) {
      existing.totalWorkOrders += 1
      existing.totalHours += row.timeSpentHours
      existing[row.woType] += 1
    } else {
      const entry: WorkOrderTypesByParentAssetRow = {
        parentAsset: row.lineAsset,
        totalWorkOrders: 1,
        totalHours: row.timeSpentHours,
        'Reactive-Breakdown': 0,
        'Reactive-Controls': 0,
        'Reactive-Process Checks': 0,
        'Reactive-ENG': 0,
        'Reactive-O.E.': 0,
        'Reactive-O.E. Start-Up/Shutdown': 0,
        'Reactive-O.E. Training': 0,
        Reactive: 0,
        'Preventative (PM)': 0,
        Corrective: 0,
        Proactive: 0,
        Rebuild: 0,
        Changeover: 0,
        Consumables: 0,
        'Tool Room': 0,
        Administration: 0,
        'Start-Up/Shutdown': 0,
        Engineering: 0,
        Training: 0,
        Other: 0,
      }

      entry[row.woType] = 1
      acc.set(row.lineAsset, entry)
    }

    return acc
  }, new Map<string, WorkOrderTypesByParentAssetRow>())

  return [...grouped.values()].sort((a, b) => b.totalWorkOrders - a.totalWorkOrders)
}

export function getLineRiskTable(
  productionRows: ProductionRecord[],
  maintenanceRows: MaintenanceRecord[],
  oeeTarget: number,
  availabilityTarget: number,
) {
  const lines = [...new Set([...productionRows.map((row) => row.lineAsset), ...maintenanceRows.map((row) => row.lineAsset)])]

  return lines
    .map((line) => {
      const prod = productionRows.filter((row) => row.lineAsset === line)
      const maint = maintenanceRows.filter((row) => row.lineAsset === line)

      const availability = weightedAverage(prod, 'availability')
      const oee = weightedAverage(prod, 'oee')
      const performance = weightedAverage(prod, 'performance')
      const downtime = prod.reduce((acc, row) => acc + row.unplannedDowntime, 0)
      const woCount = maint.length
      const reactiveCount = maint.filter((row) => row.woType.startsWith('Reactive')).length
      const reactiveHours = maint
        .filter((row) => row.woType.startsWith('Reactive'))
        .reduce((acc, row) => acc + row.timeSpentHours, 0)

      const riskScore =
        Math.max(0, oeeTarget - oee) +
        Math.max(0, availabilityTarget - availability) +
        reactiveCount * 1.5 +
        reactiveHours

      return {
        lineAsset: line,
        availability,
        performance,
        oee,
        unplannedDowntime: downtime,
        workOrders: woCount,
        reactiveWorkOrders: reactiveCount,
        reactiveHours,
        riskScore,
        availabilityStatus: getKpiStatus(availability, availabilityTarget),
        oeeStatus: getKpiStatus(oee, oeeTarget),
      }
    })
    .sort((a, b) => b.riskScore - a.riskScore)
}

export function getMaintenanceCategoryRollup(rows: MaintenanceRecord[]) {
  const grouped = rows.reduce((acc, row) => {
    const current = acc.get(row.category)
    if (current) {
      current.workOrders += 1
      current.hours += row.timeSpentHours
    } else {
      acc.set(row.category, { category: row.category, workOrders: 1, hours: row.timeSpentHours })
    }
    return acc
  }, new Map<string, { category: string; workOrders: number; hours: number }>())

  return [...grouped.values()].sort((a, b) => b.hours - a.hours)
}

export type WeeklyCalendarRow = {
  week: string
  oee: number
  availability: number
  performance: number
  unplannedDowntime: number
  plannedRuntime: number
  totalWorkOrders: number
  reactiveWorkOrders: number
  preventativeWorkOrders: number
  reactivePercent: number
  maintenanceHours: number
  oeeDelta: number
  availabilityDelta: number
  downtimeDelta: number
}

export function getWeeklyCalendarSummary(
  productionRows: ProductionRecord[],
  maintenanceRows: MaintenanceRecord[],
): WeeklyCalendarRow[] {
  const allWeeks = [
    ...new Set([
      ...productionRows.map((row) => row.week),
      ...maintenanceRows.map((row) => row.week),
    ]),
  ].sort((a, b) => a.localeCompare(b))

  const prodByWeek = groupProductionByWeek(productionRows)
  const maintByWeek = groupMaintenanceByWeek(maintenanceRows)

  const rows: WeeklyCalendarRow[] = allWeeks.map((week) => {
    const prod = prodByWeek.get(week) ?? []
    const maint = maintByWeek.get(week) ?? []

    const oee = weightedAverage(prod, 'oee')
    const availability = weightedAverage(prod, 'availability')
    const performance = weightedAverage(prod, 'performance')
    const unplannedDowntime = prod.reduce((acc, row) => acc + row.unplannedDowntime, 0)
    const plannedRuntime = prod.reduce((acc, row) => acc + row.plannedRuntime, 0)
    const totalWorkOrders = maint.length
    const reactiveWorkOrders = maint.filter((row) => row.woType.startsWith('Reactive')).length
    const preventativeWorkOrders = maint.filter((row) => row.woType === 'Preventative (PM)').length
    const reactivePercent = totalWorkOrders > 0 ? (reactiveWorkOrders / totalWorkOrders) * 100 : 0
    const maintenanceHours = maint.reduce((acc, row) => acc + row.timeSpentHours, 0)

    return {
      week,
      oee,
      availability,
      performance,
      unplannedDowntime,
      plannedRuntime,
      totalWorkOrders,
      reactiveWorkOrders,
      preventativeWorkOrders,
      reactivePercent,
      maintenanceHours,
      oeeDelta: 0,
      availabilityDelta: 0,
      downtimeDelta: 0,
    }
  })

  // Fill week-over-week deltas.
  for (let i = 1; i < rows.length; i++) {
    rows[i].oeeDelta = rows[i].oee - rows[i - 1].oee
    rows[i].availabilityDelta = rows[i].availability - rows[i - 1].availability
    rows[i].downtimeDelta = rows[i].unplannedDowntime - rows[i - 1].unplannedDowntime
  }

  return rows
}

export function toPareto(data: Array<{ category: string; value: number }>): ParetoPoint[] {
  const sorted = [...data].sort((a, b) => b.value - a.value)
  const total = sorted.reduce((acc, entry) => acc + entry.value, 0)

  let running = 0
  return sorted.map((entry) => {
    running += entry.value
    return {
      category: entry.category,
      value: entry.value,
      cumulativePercent: total > 0 ? (running / total) * 100 : 0,
    }
  })
}
