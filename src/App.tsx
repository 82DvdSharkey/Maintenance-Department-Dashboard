import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import './App.css'
import type { MaintenanceRecord } from './types/maintenance'
import { sampleMaintenanceData } from './data/sampleMaintenanceData'
import {
  appendMaintenanceDeduped,
  getMaintenanceColumnWarnings,
  normalizeMaintenanceRows,
  parseDataText,
} from './utils/dataProcessing'

type PageView = 'page1' | 'page2'
type TrendView = 'daily' | 'weekly'
type ShiftKey = '1' | '2' | '3'
type ActionStatus = 'Open' | 'Closed' | 'Open-Ongoing'
type MeetingCadence = 'Daily Leadership' | 'Weekly Trend Review'

type ActionItem = {
  id: string
  deviation: string
  action: string
  owner: string
  date: string
  status: ActionStatus
}

type DashboardFilters = {
  date: string
  week: string
  shift: string
  woGroup: string
  status: string
  parentAsset: string
}

type DashboardSettings = {
  warningThreshold: number
  alertThreshold: number
  parentTopN: number
  subAssetTopN: number
  presetCadence: MeetingCadence
  presetShift: string
}

type DashboardStatePackage = {
  version: string
  exportedAt: string
  filters: DashboardFilters
  settings: DashboardSettings
  actions: ActionItem[]
}

const DASHBOARD_STATE_PACKAGE_VERSION = '1.1.0'

const ACTIONS_STORAGE_KEY = 'fa-maintenance-actions-v1'
const SETTINGS_STORAGE_KEY = 'fa-maintenance-settings-v1'
const FILTERS_STORAGE_KEY = 'fa-maintenance-filters-v1'
const MAINTENANCE_STORAGE_KEY = 'fa-maintenance-data-v1'

const WO_GROUPS = [
  'Reactive',
  'Preventive Maintenance',
  'Corrective',
  'Rebuild',
  'Changeover',
  'Consumables',
  'Start-Up/Shutdown',
  'Training',
  'Other',
] as const

type WorkOrderGroup = (typeof WO_GROUPS)[number]

const WO_GROUP_COLORS: Record<WorkOrderGroup, string> = {
  Reactive: '#dc2626',
  'Preventive Maintenance': '#22c55e',
  Corrective: '#f59e0b',
  Rebuild: '#3b82f6',
  Changeover: '#14b8a6',
  Consumables: '#eab308',
  'Start-Up/Shutdown': '#f97316',
  Training: '#a855f7',
  Other: '#94a3b8',
}

const defaultFilters: DashboardFilters = {
  date: 'All',
  week: 'All',
  shift: 'All',
  woGroup: 'All',
  status: 'All',
  parentAsset: 'All',
}

const defaultSettings: DashboardSettings = {
  warningThreshold: 3,
  alertThreshold: 5,
  parentTopN: 8,
  subAssetTopN: 8,
  presetCadence: 'Daily Leadership',
  presetShift: 'All',
}

const initialActions: ActionItem[] = [
  {
    id: 'ACT-1',
    deviation: 'Conveyor Zone 1 repeat trips',
    action: 'Validate overload settings and add weekly PM inspection point',
    owner: 'Maint',
    date: '2026-08-13',
    status: 'Open',
  },
  {
    id: 'ACT-2',
    deviation: 'Hydraulic leak recurrence on Line 2',
    action: 'Replace manifold seals and complete root-cause review with Engineering',
    owner: 'Eng',
    date: '2026-08-14',
    status: 'Open-Ongoing',
  },
]

function toGroup(woType: MaintenanceRecord['woType']): WorkOrderGroup {
  if (woType.startsWith('Reactive')) {
    return 'Reactive'
  }
  if (woType === 'Preventative (PM)') {
    return 'Preventive Maintenance'
  }
  if (woType === 'Corrective') {
    return 'Corrective'
  }
  if (woType === 'Rebuild') {
    return 'Rebuild'
  }
  if (woType === 'Changeover') {
    return 'Changeover'
  }
  if (woType === 'Consumables') {
    return 'Consumables'
  }
  if (woType === 'Start-Up/Shutdown') {
    return 'Start-Up/Shutdown'
  }
  if (woType === 'Training') {
    return 'Training'
  }
  return 'Other'
}

function baseCountObject(): Record<WorkOrderGroup, number> {
  return {
    Reactive: 0,
    'Preventive Maintenance': 0,
    Corrective: 0,
    Rebuild: 0,
    Changeover: 0,
    Consumables: 0,
    'Start-Up/Shutdown': 0,
    Training: 0,
    Other: 0,
  }
}

function statusFromCount(count: number, warning: number, alert: number): 'green' | 'orange' | 'red' {
  if (count >= alert) {
    return 'red'
  }
  if (count >= warning) {
    return 'orange'
  }
  return 'green'
}

function toOrdinalShiftLabel(shift: ShiftKey): string {
  if (shift === '1') {
    return '1st'
  }
  if (shift === '2') {
    return '2nd'
  }
  return '3rd'
}

function clampMinOne(value: number): number {
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 1
}

function isValidActionStatus(value: string): value is ActionStatus {
  return value === 'Open' || value === 'Closed' || value === 'Open-Ongoing'
}

function sanitizeActions(data: unknown): ActionItem[] {
  if (!Array.isArray(data)) {
    return []
  }

  return data
    .map((item, index) => {
      if (!item || typeof item !== 'object') {
        return null
      }

      const candidate = item as Partial<ActionItem>
      const status = typeof candidate.status === 'string' && isValidActionStatus(candidate.status)
        ? candidate.status
        : 'Open'

      return {
        id: typeof candidate.id === 'string' && candidate.id.trim() ? candidate.id : `ACT-${index + 1}`,
        deviation: typeof candidate.deviation === 'string' ? candidate.deviation : '',
        action: typeof candidate.action === 'string' ? candidate.action : '',
        owner: typeof candidate.owner === 'string' ? candidate.owner : '',
        date: typeof candidate.date === 'string' ? candidate.date : '',
        status,
      }
    })
    .filter((item): item is ActionItem => item !== null)
}

function sanitizeMaintenanceRows(data: unknown): MaintenanceRecord[] {
  if (!Array.isArray(data)) {
    return []
  }

  return data.filter((item): item is MaintenanceRecord => {
    if (!item || typeof item !== 'object') {
      return false
    }

    const row = item as Partial<MaintenanceRecord>
    return Boolean(
      typeof row.id === 'string'
      && typeof row.date === 'string'
      && typeof row.week === 'string'
      && typeof row.parentAsset === 'string'
      && typeof row.subAsset === 'string'
      && typeof row.woType === 'string'
      && typeof row.status === 'string',
    )
  })
}

function sanitizeFilters(data: unknown): DashboardFilters {
  if (!data || typeof data !== 'object') {
    return defaultFilters
  }

  const candidate = data as Partial<DashboardFilters>
  return {
    date: typeof candidate.date === 'string' ? candidate.date : defaultFilters.date,
    week: typeof candidate.week === 'string' ? candidate.week : defaultFilters.week,
    shift: typeof candidate.shift === 'string' ? candidate.shift : defaultFilters.shift,
    woGroup: typeof candidate.woGroup === 'string' ? candidate.woGroup : defaultFilters.woGroup,
    status: typeof candidate.status === 'string' ? candidate.status : defaultFilters.status,
    parentAsset: typeof candidate.parentAsset === 'string' ? candidate.parentAsset : defaultFilters.parentAsset,
  }
}

function sanitizeSettings(data: unknown): DashboardSettings {
  if (!data || typeof data !== 'object') {
    return defaultSettings
  }

  const candidate = data as Partial<DashboardSettings>
  const presetCadence = candidate.presetCadence === 'Weekly Trend Review'
    ? 'Weekly Trend Review'
    : 'Daily Leadership'

  return {
    warningThreshold: clampMinOne(candidate.warningThreshold ?? defaultSettings.warningThreshold),
    alertThreshold: clampMinOne(candidate.alertThreshold ?? defaultSettings.alertThreshold),
    parentTopN: clampMinOne(candidate.parentTopN ?? defaultSettings.parentTopN),
    subAssetTopN: clampMinOne(candidate.subAssetTopN ?? defaultSettings.subAssetTopN),
    presetCadence,
    presetShift: typeof candidate.presetShift === 'string' ? candidate.presetShift : defaultSettings.presetShift,
  }
}

function App() {
  const [activePage, setActivePage] = useState<PageView>('page1')
  const [trendView, setTrendView] = useState<TrendView>('daily')
  const [maintenanceRows, setMaintenanceRows] = useState<MaintenanceRecord[]>(() => {
    try {
      const raw = localStorage.getItem(MAINTENANCE_STORAGE_KEY)
      if (!raw) {
        return sampleMaintenanceData
      }

      const parsed = sanitizeMaintenanceRows(JSON.parse(raw))
      return parsed.length ? parsed : sampleMaintenanceData
    } catch {
      return sampleMaintenanceData
    }
  })
  const [filters, setFilters] = useState<DashboardFilters>(() => {
    try {
      const raw = localStorage.getItem(FILTERS_STORAGE_KEY)
      if (!raw) {
        return defaultFilters
      }
      return { ...defaultFilters, ...(JSON.parse(raw) as Partial<DashboardFilters>) }
    } catch {
      return defaultFilters
    }
  })
  const [settings, setSettings] = useState<DashboardSettings>(() => {
    try {
      const raw = localStorage.getItem(SETTINGS_STORAGE_KEY)
      if (!raw) {
        return defaultSettings
      }
      const parsed = JSON.parse(raw) as Partial<DashboardSettings>
      return {
        ...defaultSettings,
        ...parsed,
        warningThreshold: clampMinOne(parsed.warningThreshold ?? defaultSettings.warningThreshold),
        alertThreshold: clampMinOne(parsed.alertThreshold ?? defaultSettings.alertThreshold),
        parentTopN: clampMinOne(parsed.parentTopN ?? defaultSettings.parentTopN),
        subAssetTopN: clampMinOne(parsed.subAssetTopN ?? defaultSettings.subAssetTopN),
      }
    } catch {
      return defaultSettings
    }
  })
  const [maintenancePaste, setMaintenancePaste] = useState('')
  const [statusMessage, setStatusMessage] = useState('Sample Fiix data loaded. Upload CSV to append or replace.')
  const [qualityWarnings, setQualityWarnings] = useState<string[]>([])
  const [actions, setActions] = useState<ActionItem[]>(() => {
    try {
      const raw = localStorage.getItem(ACTIONS_STORAGE_KEY)
      if (!raw) {
        return initialActions
      }
      const parsed = sanitizeActions(JSON.parse(raw))
      return parsed.length ? parsed : initialActions
    } catch {
      return initialActions
    }
  })

  const importActionInputRef = useRef<HTMLInputElement | null>(null)
  const importStateInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    localStorage.setItem(ACTIONS_STORAGE_KEY, JSON.stringify(actions))
  }, [actions])

  useEffect(() => {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings))
  }, [settings])

  useEffect(() => {
    localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(filters))
  }, [filters])

  useEffect(() => {
    localStorage.setItem(MAINTENANCE_STORAGE_KEY, JSON.stringify(maintenanceRows))
  }, [maintenanceRows])

  const weekOptions = useMemo(() => {
    return [...new Set(maintenanceRows.map((row) => row.week))].sort((a, b) => a.localeCompare(b))
  }, [maintenanceRows])

  const dateOptions = useMemo(() => {
    return [...new Set(maintenanceRows.map((row) => row.date))].sort((a, b) => a.localeCompare(b))
  }, [maintenanceRows])

  const parentAssetOptions = useMemo(() => {
    return [...new Set(maintenanceRows.map((row) => row.parentAsset))].sort((a, b) => a.localeCompare(b))
  }, [maintenanceRows])

  const latestWeek = weekOptions[weekOptions.length - 1] ?? 'N/A'
  const latestDate = dateOptions[dateOptions.length - 1] ?? 'N/A'

  const scopedRows = useMemo(() => {
    return maintenanceRows.filter((row) => {
      if (filters.date !== 'All' && row.date !== filters.date) {
        return false
      }
      if (filters.week !== 'All' && row.week !== filters.week) {
        return false
      }
      if (filters.shift !== 'All' && row.shift !== filters.shift) {
        return false
      }
      if (filters.woGroup !== 'All' && toGroup(row.woType) !== filters.woGroup) {
        return false
      }
      if (filters.status !== 'All' && row.status !== filters.status) {
        return false
      }
      if (filters.parentAsset !== 'All' && row.parentAsset !== filters.parentAsset) {
        return false
      }
      return true
    })
  }, [maintenanceRows, filters])

  const rowsForShiftIndicator = useMemo(() => {
    return maintenanceRows.filter((row) => {
      if (filters.date !== 'All' && row.date !== filters.date) {
        return false
      }
      if (filters.week !== 'All' && row.week !== filters.week) {
        return false
      }
      if (filters.parentAsset !== 'All' && row.parentAsset !== filters.parentAsset) {
        return false
      }
      if (filters.woGroup !== 'All' && toGroup(row.woType) !== filters.woGroup) {
        return false
      }
      if (filters.status !== 'All' && row.status !== filters.status) {
        return false
      }
      return true
    })
  }, [maintenanceRows, filters])

  const shiftStats = useMemo(() => {
    const counts: Record<ShiftKey, number> = { '1': 0, '2': 0, '3': 0 }
    for (const row of rowsForShiftIndicator) {
      if (row.shift === '1' || row.shift === '2' || row.shift === '3') {
        counts[row.shift] += 1
      }
    }
    return counts
  }, [rowsForShiftIndicator])

  const shiftStates = useMemo(() => {
    return {
      '1': statusFromCount(shiftStats['1'], settings.warningThreshold, settings.alertThreshold),
      '2': statusFromCount(shiftStats['2'], settings.warningThreshold, settings.alertThreshold),
      '3': statusFromCount(shiftStats['3'], settings.warningThreshold, settings.alertThreshold),
    }
  }, [shiftStats, settings.warningThreshold, settings.alertThreshold])

  const kpis = useMemo(() => {
    const total = scopedRows.length
    const reactive = scopedRows.filter((row) => toGroup(row.woType) === 'Reactive').length
    const preventive = scopedRows.filter((row) => toGroup(row.woType) === 'Preventive Maintenance').length
    const corrective = scopedRows.filter((row) => toGroup(row.woType) === 'Corrective').length
    const rebuild = scopedRows.filter((row) => toGroup(row.woType) === 'Rebuild').length
    const open = scopedRows.filter((row) => row.status === 'Open' || row.status === 'Open-Ongoing').length
    const closed = scopedRows.filter((row) => row.status === 'Closed').length
    const uniqueDays = new Set(scopedRows.map((row) => row.date)).size
    const avgDaily = uniqueDays > 0 ? total / uniqueDays : 0
    const weeklyTotal = scopedRows.filter((row) => row.week === (filters.week === 'All' ? latestWeek : filters.week)).length
    const rankedShift = (['1', '2', '3'] as ShiftKey[])
      .map((shift) => ({ shift, count: shiftStats[shift] }))
      .sort((a, b) => b.count - a.count)[0]

    return {
      total,
      reactive,
      preventive,
      corrective,
      rebuild,
      open,
      closed,
      avgDaily,
      weeklyTotal,
      highestShift: rankedShift?.count ? toOrdinalShiftLabel(rankedShift.shift) : 'N/A',
    }
  }, [scopedRows, filters.week, latestWeek, shiftStats])

  const totalCallsTrend = useMemo(() => {
    const grouped = new Map<string, Record<WorkOrderGroup, number> & { period: string }>()

    for (const row of scopedRows) {
      const period = trendView === 'daily' ? row.date : row.week
      const existing = grouped.get(period)
      const group = toGroup(row.woType)
      if (existing) {
        existing[group] += 1
      } else {
        const entry = { period, ...baseCountObject() }
        entry[group] = 1
        grouped.set(period, entry)
      }
    }

    return [...grouped.values()].sort((a, b) => a.period.localeCompare(b.period))
  }, [scopedRows, trendView])

  const parentAssetVolume = useMemo(() => {
    const grouped = new Map<string, Record<WorkOrderGroup, number> & { parentAsset: string; total: number }>()

    for (const row of scopedRows) {
      const group = toGroup(row.woType)
      const existing = grouped.get(row.parentAsset)
      if (existing) {
        existing[group] += 1
        existing.total += 1
      } else {
        const entry = { parentAsset: row.parentAsset, total: 1, ...baseCountObject() }
        entry[group] = 1
        grouped.set(row.parentAsset, entry)
      }
    }

    return [...grouped.values()]
      .sort((a, b) => b.total - a.total)
      .slice(0, settings.parentTopN)
  }, [scopedRows, settings.parentTopN])

  const rankedParents = useMemo(() => {
    const totals = new Map<string, number>()
    for (const row of scopedRows) {
      totals.set(row.parentAsset, (totals.get(row.parentAsset) ?? 0) + 1)
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([parent]) => parent)
  }, [scopedRows])

  function subAssetDataForParentRank(rank: number) {
    const parent = rankedParents[rank - 1]
    if (!parent) {
      return {
        parent: `Rank #${rank} unavailable`,
        rows: [] as Array<Record<WorkOrderGroup, number> & { subAsset: string; total: number }>,
      }
    }

    const grouped = new Map<string, Record<WorkOrderGroup, number> & { subAsset: string; total: number }>()
    for (const row of scopedRows) {
      if (row.parentAsset !== parent) {
        continue
      }
      const group = toGroup(row.woType)
      const existing = grouped.get(row.subAsset)
      if (existing) {
        existing[group] += 1
        existing.total += 1
      } else {
        const entry = { subAsset: row.subAsset, total: 1, ...baseCountObject() }
        entry[group] = 1
        grouped.set(row.subAsset, entry)
      }
    }

    return {
      parent,
      rows: [...grouped.values()].sort((a, b) => b.total - a.total).slice(0, settings.subAssetTopN),
    }
  }

  const subAssetRank1 = useMemo(() => subAssetDataForParentRank(1), [scopedRows, rankedParents, settings.subAssetTopN])
  const subAssetRank2 = useMemo(() => subAssetDataForParentRank(2), [scopedRows, rankedParents, settings.subAssetTopN])
  const subAssetRank3 = useMemo(() => subAssetDataForParentRank(3), [scopedRows, rankedParents, settings.subAssetTopN])
  const subAssetRank4 = useMemo(() => subAssetDataForParentRank(4), [scopedRows, rankedParents, settings.subAssetTopN])

  function ingestFiixText(text: string, replaceData: boolean) {
    const raw = parseDataText(text)
    const warnings = getMaintenanceColumnWarnings(raw)
    const normalized = normalizeMaintenanceRows(raw)

    setQualityWarnings(warnings)

    if (!normalized.length) {
      setStatusMessage('No valid Fiix rows found. Confirm Date Completed and Parent Asset mappings.')
      return
    }

    setMaintenanceRows((previous) => (replaceData ? normalized : appendMaintenanceDeduped(previous, normalized)))
    setStatusMessage(`Fiix data ${replaceData ? 'replaced' : 'appended'}: ${normalized.length} rows.`)
  }

  function handleFiixUpload(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0]
    if (!selectedFile) {
      return
    }

    const reader = new FileReader()
    reader.onload = () => ingestFiixText(`${reader.result ?? ''}`, false)
    reader.readAsText(selectedFile)
    event.target.value = ''
  }

  function updateFilter<K extends keyof DashboardFilters>(key: K, value: DashboardFilters[K]) {
    setFilters((previous) => ({ ...previous, [key]: value }))
  }

  function updateAction(id: string, field: keyof Omit<ActionItem, 'id'>, value: string) {
    setActions((previous) => previous.map((row) => (row.id === id ? { ...row, [field]: value } : row)))
  }

  function addActionRow() {
    setActions((previous) => [
      ...previous,
      {
        id: `ACT-${previous.length + 1}`,
        deviation: '',
        action: '',
        owner: '',
        date: '',
        status: 'Open',
      },
    ])
  }

  function resetActionTable() {
    setActions(initialActions)
    setStatusMessage('Actions table reset to default placeholder rows.')
  }

  function exportActionsToJson() {
    const payload = JSON.stringify(actions, null, 2)
    const blob = new Blob([payload], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `fa-maintenance-actions-${new Date().toISOString().slice(0, 10)}.json`
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    URL.revokeObjectURL(url)
    setStatusMessage('Actions exported to JSON.')
  }

  function importActionsFromFile(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0]
    if (!selectedFile) {
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      try {
        const parsed = sanitizeActions(JSON.parse(`${reader.result ?? '[]'}`))
        if (!parsed.length) {
          setStatusMessage('Actions import failed: no valid action rows found in JSON.')
          return
        }
        setActions(parsed)
        setStatusMessage(`Actions imported: ${parsed.length} rows.`)
      } catch {
        setStatusMessage('Actions import failed: invalid JSON format.')
      }
    }

    reader.readAsText(selectedFile)
    event.target.value = ''
  }

  function applyPreset() {
    const cadence = settings.presetCadence
    const shift = settings.presetShift

    if (cadence === 'Daily Leadership') {
      setTrendView('daily')
      setFilters((previous) => ({
        ...previous,
        date: latestDate !== 'N/A' ? latestDate : 'All',
        week: 'All',
        shift,
      }))
      setStatusMessage('Applied Daily Leadership preset.')
      return
    }

    setTrendView('weekly')
    setFilters((previous) => ({
      ...previous,
      date: 'All',
      week: latestWeek !== 'N/A' ? latestWeek : 'All',
      shift,
    }))
    setStatusMessage('Applied Weekly Trend Review preset.')
  }

  function exportDashboardStatePackage() {
    const packageData: DashboardStatePackage = {
      version: DASHBOARD_STATE_PACKAGE_VERSION,
      exportedAt: new Date().toISOString(),
      filters,
      settings,
      actions,
    }

    const payload = JSON.stringify(packageData, null, 2)
    const blob = new Blob([payload], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `fa-dashboard-state-${new Date().toISOString().slice(0, 10)}.json`
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    URL.revokeObjectURL(url)
    setStatusMessage('Dashboard state package exported.')
  }

  function importDashboardStatePackage(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0]
    if (!selectedFile) {
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      try {
        const parsed = JSON.parse(`${reader.result ?? '{}'}`) as Partial<DashboardStatePackage>
        setFilters(sanitizeFilters(parsed.filters))
        setSettings(sanitizeSettings(parsed.settings))

        const importedActions = sanitizeActions(parsed.actions)
        setActions(importedActions.length ? importedActions : initialActions)

        const importedVersion = typeof parsed.version === 'string' ? parsed.version : 'legacy'
        const importedTimestamp = typeof parsed.exportedAt === 'string' ? parsed.exportedAt : 'unknown timestamp'
        setStatusMessage(
          `Dashboard state imported (v${importedVersion}, ${importedTimestamp})${importedActions.length ? ` with ${importedActions.length} actions.` : ' with default actions.'}`,
        )
      } catch {
        setStatusMessage('Dashboard state import failed: invalid JSON format.')
      }
    }

    reader.readAsText(selectedFile)
    event.target.value = ''
  }

  function resetFilters() {
    setFilters(defaultFilters)
    setTrendView('daily')
  }

  return (
    <main className="fa-shell">
      <header className="fa-header">
        <div>
          <p className="fa-eyebrow">Final Assembly Maintenance Leadership</p>
          <h1>FA Maintenance Call Dashboard</h1>
          <p className="fa-period">Reporting Period: {filters.date !== 'All' ? filters.date : filters.week !== 'All' ? filters.week : 'All Available Data'}</p>
        </div>

        <div className="fa-header-right">
          <div className="shift-indicators" role="group" aria-label="Shift condition indicators">
            <button type="button" className={`shift-btn ${shiftStates['1']}`} onClick={() => updateFilter('shift', filters.shift === '1' ? 'All' : '1')}>
              1st Shift ({shiftStats['1']})
            </button>
            <button type="button" className={`shift-btn ${shiftStates['2']}`} onClick={() => updateFilter('shift', filters.shift === '2' ? 'All' : '2')}>
              2nd Shift ({shiftStats['2']})
            </button>
            <button type="button" className={`shift-btn ${shiftStates['3']}`} onClick={() => updateFilter('shift', filters.shift === '3' ? 'All' : '3')}>
              3rd Shift ({shiftStats['3']})
            </button>
          </div>
          <p className="fa-period">Thresholds: Warning {settings.warningThreshold} | Alert {settings.alertThreshold}</p>
        </div>
      </header>

      <section className="panel page-toggle-panel" aria-label="Page selector">
        <div className="page-toggle">
          <button type="button" className={activePage === 'page1' ? 'active' : ''} onClick={() => setActivePage('page1')}>Page 1: Calls and Drivers</button>
          <button type="button" className={activePage === 'page2' ? 'active' : ''} onClick={() => setActivePage('page2')}>Page 2: Additional Drivers and Actions</button>
        </div>
      </section>

      <section className="panel" aria-label="Dashboard settings">
        <h2>Dashboard Settings</h2>
        <div className="settings-grid">
          <label>
            Warning Threshold
            <input
              type="number"
              min={1}
              value={settings.warningThreshold}
              onChange={(event) => setSettings((previous) => ({
                ...previous,
                warningThreshold: clampMinOne(Number(event.target.value)),
              }))}
            />
          </label>
          <label>
            Alert Threshold
            <input
              type="number"
              min={1}
              value={settings.alertThreshold}
              onChange={(event) => setSettings((previous) => ({
                ...previous,
                alertThreshold: clampMinOne(Number(event.target.value)),
              }))}
            />
          </label>
          <label>
            Parent Asset Chart Top N
            <input
              type="number"
              min={1}
              value={settings.parentTopN}
              onChange={(event) => setSettings((previous) => ({
                ...previous,
                parentTopN: clampMinOne(Number(event.target.value)),
              }))}
            />
          </label>
          <label>
            Sub-Asset Chart Top N
            <input
              type="number"
              min={1}
              value={settings.subAssetTopN}
              onChange={(event) => setSettings((previous) => ({
                ...previous,
                subAssetTopN: clampMinOne(Number(event.target.value)),
              }))}
            />
          </label>
          <label>
            Cadence Preset
            <select
              value={settings.presetCadence}
              onChange={(event) => setSettings((previous) => ({
                ...previous,
                presetCadence: event.target.value as MeetingCadence,
              }))}
            >
              <option value="Daily Leadership">Daily Leadership</option>
              <option value="Weekly Trend Review">Weekly Trend Review</option>
            </select>
          </label>
          <label>
            Preset Shift
            <select
              value={settings.presetShift}
              onChange={(event) => setSettings((previous) => ({
                ...previous,
                presetShift: event.target.value,
              }))}
            >
              <option value="All">All</option>
              <option value="1">1st Shift</option>
              <option value="2">2nd Shift</option>
              <option value="3">3rd Shift</option>
            </select>
          </label>
        </div>
        <div className="settings-actions">
          <button type="button" className="small-btn" onClick={applyPreset}>Apply Preset</button>
          <button type="button" className="small-btn" onClick={resetFilters}>Reset Filters</button>
          <button type="button" className="small-btn" onClick={exportDashboardStatePackage}>Export Dashboard State</button>
          <button type="button" className="small-btn" onClick={() => importStateInputRef.current?.click()}>Import Dashboard State</button>
          <input
            ref={importStateInputRef}
            type="file"
            accept=".json"
            onChange={importDashboardStatePackage}
            style={{ display: 'none' }}
          />
        </div>
      </section>

      <section className="panel" aria-label="Dashboard filters">
        <div className="filters-grid">
          <label>
            Date
            <select value={filters.date} onChange={(event) => updateFilter('date', event.target.value)}>
              <option value="All">All</option>
              {dateOptions.map((date) => <option key={date} value={date}>{date}</option>)}
            </select>
          </label>
          <label>
            Week
            <select value={filters.week} onChange={(event) => updateFilter('week', event.target.value)}>
              <option value="All">All</option>
              {weekOptions.map((week) => <option key={week} value={week}>{week}</option>)}
            </select>
          </label>
          <label>
            Shift
            <select value={filters.shift} onChange={(event) => updateFilter('shift', event.target.value)}>
              <option value="All">All</option>
              <option value="1">1st Shift</option>
              <option value="2">2nd Shift</option>
              <option value="3">3rd Shift</option>
            </select>
          </label>
          <label>
            Parent Asset
            <select value={filters.parentAsset} onChange={(event) => updateFilter('parentAsset', event.target.value)}>
              <option value="All">All</option>
              {parentAssetOptions.map((asset) => <option key={asset} value={asset}>{asset}</option>)}
            </select>
          </label>
          <label>
            Work Order Type
            <select value={filters.woGroup} onChange={(event) => updateFilter('woGroup', event.target.value)}>
              <option value="All">All</option>
              {WO_GROUPS.map((group) => <option key={group} value={group}>{group}</option>)}
            </select>
          </label>
          <label>
            Status
            <select value={filters.status} onChange={(event) => updateFilter('status', event.target.value)}>
              <option value="All">All</option>
              <option value="Open">Open</option>
              <option value="Open-Ongoing">Open-Ongoing</option>
              <option value="Closed">Closed</option>
            </select>
          </label>
          <label>
            Total Calls View
            <select value={trendView} onChange={(event) => setTrendView(event.target.value as TrendView)}>
              <option value="daily">Daily trend</option>
              <option value="weekly">Weekly trend</option>
            </select>
          </label>
        </div>
      </section>

      <section className="kpi-grid" aria-label="KPI cards">
        <article className="kpi-card"><p>Total FA Work Orders</p><strong>{kpis.total}</strong></article>
        <article className="kpi-card"><p>Total Reactive Work Orders</p><strong>{kpis.reactive}</strong></article>
        <article className="kpi-card"><p>Total Preventive Maintenance</p><strong>{kpis.preventive}</strong></article>
        <article className="kpi-card"><p>Total Corrective Work Orders</p><strong>{kpis.corrective}</strong></article>
        <article className="kpi-card"><p>Total Rebuild Work Orders</p><strong>{kpis.rebuild}</strong></article>
        <article className="kpi-card"><p>Open Work Orders</p><strong>{kpis.open}</strong></article>
        <article className="kpi-card"><p>Closed Work Orders</p><strong>{kpis.closed}</strong></article>
        <article className="kpi-card"><p>Average Daily Calls</p><strong>{kpis.avgDaily.toFixed(1)}</strong></article>
        <article className="kpi-card"><p>Weekly Total Calls</p><strong>{kpis.weeklyTotal}</strong></article>
        <article className="kpi-card"><p>Highest Call Volume Shift</p><strong>{kpis.highestShift}</strong></article>
      </section>

      {activePage === 'page1' && (
        <>
          <section className="panel" aria-label="Total calls chart">
            <h2>Total Calls</h2>
            <div className="chart-wrap">
              <ResponsiveContainer>
                <BarChart data={totalCallsTrend} margin={{ top: 12, right: 18, left: 0, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis dataKey="period" stroke="#e5e7eb" />
                  <YAxis allowDecimals={false} stroke="#e5e7eb" />
                  <Tooltip />
                  <Legend />
                  {WO_GROUPS.map((group) => (
                    <Bar key={group} dataKey={group} stackId="calls" fill={WO_GROUP_COLORS[group]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section className="panel" aria-label="Volume by parent asset">
            <h2>Volume of Calls by Parent Asset</h2>
            <p className="panel-subtitle">Top {settings.parentTopN} parent assets by total calls</p>
            <div className="chart-wrap">
              <ResponsiveContainer>
                <BarChart data={parentAssetVolume} margin={{ top: 12, right: 18, left: 0, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis dataKey="parentAsset" stroke="#e5e7eb" />
                  <YAxis allowDecimals={false} stroke="#e5e7eb" />
                  <Tooltip />
                  <Legend />
                  {WO_GROUPS.map((group) => (
                    <Bar key={group} dataKey={group} stackId="parent" fill={WO_GROUP_COLORS[group]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          </section>

          <div className="split-grid">
            <section className="panel" aria-label="Top sub-asset driver 1">
              <h2>#1 Highest Volume of Calls by Sub-Asset</h2>
              <p className="panel-subtitle">Parent Asset: {subAssetRank1.parent}</p>
              <div className="chart-wrap">
                <ResponsiveContainer>
                  <BarChart data={subAssetRank1.rows} margin={{ top: 12, right: 18, left: 0, bottom: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis dataKey="subAsset" stroke="#e5e7eb" />
                    <YAxis allowDecimals={false} stroke="#e5e7eb" />
                    <Tooltip />
                    <Legend />
                    {WO_GROUPS.map((group) => (
                      <Bar key={group} dataKey={group} stackId="sub1" fill={WO_GROUP_COLORS[group]} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="panel" aria-label="Top sub-asset driver 2">
              <h2>#2 Highest Volume of Calls by Sub-Asset</h2>
              <p className="panel-subtitle">Parent Asset: {subAssetRank2.parent}</p>
              <div className="chart-wrap">
                <ResponsiveContainer>
                  <BarChart data={subAssetRank2.rows} margin={{ top: 12, right: 18, left: 0, bottom: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis dataKey="subAsset" stroke="#e5e7eb" />
                    <YAxis allowDecimals={false} stroke="#e5e7eb" />
                    <Tooltip />
                    <Legend />
                    {WO_GROUPS.map((group) => (
                      <Bar key={group} dataKey={group} stackId="sub2" fill={WO_GROUP_COLORS[group]} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          </div>
        </>
      )}

      {activePage === 'page2' && (
        <>
          <div className="split-grid">
            <section className="panel" aria-label="Top sub-asset driver 3">
              <h2>#3 Highest Volume of Calls by Sub-Asset</h2>
              <p className="panel-subtitle">Parent Asset: {subAssetRank3.parent}</p>
              <div className="chart-wrap">
                <ResponsiveContainer>
                  <BarChart data={subAssetRank3.rows} margin={{ top: 12, right: 18, left: 0, bottom: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis dataKey="subAsset" stroke="#e5e7eb" />
                    <YAxis allowDecimals={false} stroke="#e5e7eb" />
                    <Tooltip />
                    <Legend />
                    {WO_GROUPS.map((group) => (
                      <Bar key={group} dataKey={group} stackId="sub3" fill={WO_GROUP_COLORS[group]} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="panel" aria-label="Top sub-asset driver 4">
              <h2>#4 Highest Volume of Calls by Sub-Asset</h2>
              <p className="panel-subtitle">Parent Asset: {subAssetRank4.parent}</p>
              <div className="chart-wrap">
                <ResponsiveContainer>
                  <BarChart data={subAssetRank4.rows} margin={{ top: 12, right: 18, left: 0, bottom: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis dataKey="subAsset" stroke="#e5e7eb" />
                    <YAxis allowDecimals={false} stroke="#e5e7eb" />
                    <Tooltip />
                    <Legend />
                    {WO_GROUPS.map((group) => (
                      <Bar key={group} dataKey={group} stackId="sub4" fill={WO_GROUP_COLORS[group]} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          </div>

          <section className="panel" aria-label="Actions table">
            <div className="actions-title-row">
              <h2>Actions</h2>
              <div className="settings-actions">
                <button type="button" className="small-btn" onClick={addActionRow}>Add Action Row</button>
                <button type="button" className="small-btn" onClick={exportActionsToJson}>Export Actions JSON</button>
                <button type="button" className="small-btn" onClick={() => importActionInputRef.current?.click()}>Import Actions JSON</button>
                <button type="button" className="small-btn" onClick={resetActionTable}>Reset Actions</button>
                <input
                  ref={importActionInputRef}
                  type="file"
                  accept=".json"
                  onChange={importActionsFromFile}
                  style={{ display: 'none' }}
                />
              </div>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Deviation</th>
                    <th>Action</th>
                    <th>Owner</th>
                    <th>Date</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {actions.map((row) => (
                    <tr key={row.id}>
                      <td><input value={row.deviation} onChange={(event) => updateAction(row.id, 'deviation', event.target.value)} /></td>
                      <td><input value={row.action} onChange={(event) => updateAction(row.id, 'action', event.target.value)} /></td>
                      <td><input value={row.owner} onChange={(event) => updateAction(row.id, 'owner', event.target.value)} /></td>
                      <td><input type="date" value={row.date} onChange={(event) => updateAction(row.id, 'date', event.target.value)} /></td>
                      <td>
                        <select value={row.status} onChange={(event) => updateAction(row.id, 'status', event.target.value)}>
                          <option value="Open">Open</option>
                          <option value="Open-Ongoing">Open-Ongoing</option>
                          <option value="Closed">Closed</option>
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      <section className="panel" aria-label="Fiix data upload and quality">
        <h2>Fiix Data Upload</h2>
        <a className="template-link" href="/templates/fiix_maintenance_template.csv" download>Download Fiix template</a>
        <div className="ingest-row">
          <label className="upload-pill">
            <span>Upload Fiix CSV</span>
            <input type="file" accept=".csv,.txt" onChange={handleFiixUpload} />
          </label>
          <button type="button" className="small-btn" onClick={() => ingestFiixText(maintenancePaste, false)}>Append Fiix</button>
          <button type="button" className="small-btn" onClick={() => ingestFiixText(maintenancePaste, true)}>Replace Fiix</button>
        </div>
        <textarea className="paste-box" value={maintenancePaste} onChange={(event) => setMaintenancePaste(event.target.value)} placeholder="Paste Fiix rows" />
        <p className="status-text">{statusMessage}</p>
        {!!qualityWarnings.length && (
          <div className="warning-box">
            {qualityWarnings.map((warning) => <p key={warning}>{warning}</p>)}
          </div>
        )}
      </section>
    </main>
  )
}

export default App
