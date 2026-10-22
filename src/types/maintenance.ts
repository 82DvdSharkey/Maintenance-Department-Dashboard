export type ShiftType = '1' | '2' | '3' | 'Other'

export type MaintenanceWorkOrderType =
  | 'Changeover'
  | 'Consumables'
  | 'Reactive'
  | 'Reactive-Breakdown'
  | 'Reactive-Controls'
  | 'Reactive-ENG'
  | 'Reactive-O.E.'
  | 'Reactive-O.E. Start-Up/Shutdown'
  | 'Reactive-O.E. Training'
  | 'Reactive-Process Checks'
  | 'Tool Room'
  | 'Preventative (PM)'
  | 'Administration'
  | 'Corrective'
  | 'Proactive'
  | 'Rebuild'
  | 'Start-Up/Shutdown'
  | 'Engineering'
  | 'Training'
  | 'Other'

export type KpiStatus = 'red' | 'yellow' | 'green'

export type ProductionRecord = {
  id: string
  date: string
  week: string
  lineAsset: string
  shift: ShiftType
  plannedRuntime: number
  unplannedDowntime: number
  availability: number
  performance: number
  oee: number
}

export type MaintenanceRecord = {
  id: string
  completionTimestamp: string
  date: string
  week: string
  parentAsset: string
  subAsset: string
  lineAsset: string
  workOrderNumber: string
  woType: MaintenanceWorkOrderType
  status: 'Open' | 'Closed' | 'Open-Ongoing'
  description: string
  category: string
  timeSpentHours: number
  technician: string
  shift: ShiftType
}

export type FilterState = {
  week: string
  startDate: string
  endDate: string
  lineAsset: string
  shift: string
  technician: string
  woType: string
}

export type DashboardTargetCard = {
  key: string
  label: string
  actual: number
  target: number
  variance: number
  status: KpiStatus
}

export type ParetoPoint = {
  category: string
  value: number
  cumulativePercent: number
}
