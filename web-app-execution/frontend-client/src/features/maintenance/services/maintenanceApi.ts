/**
 * FILE: features/maintenance/services/maintenanceApi.ts
 * DESCRIPTION: Client REST du cockpit maintenance sur /api/v1/maintenance
 *              (api-gateway, tables Postgres `maintenance_*`). Même forme
 *              try/catch + extractErrorMessage que cockpitApi.ts.
 */
import apiClient from '../../../services/apiClient'

export interface MetierFilterOption {
  sous_categorie: string
  prestataire: string
}

export interface MetierFilters {
  metier: string
  sous_categories: MetierFilterOption[]
}

export interface MaintenanceFilters {
  metiers: MetierFilters[]
  etages: string[]
}

export interface DashboardParams {
  zone: string
  metier?: string
  sous_categorie?: string
  statut_workflow?: string
  priorite?: string
  etage?: string
  periode: string
}

export interface TimeSeriesPoint {
  label: string
  count: number
}

export interface CountEntry {
  metier?: string
  bucket?: string
  count: number
}

export interface ZoneEntry {
  zone: string
  count: number
  over_threshold: boolean
}

export interface AlertEntry {
  type: 'zone' | 'metier'
  label: string
  count: number
  threshold: number
  severity: 'warning' | 'danger'
}

export interface MaintenanceDashboard {
  kpi_total_tickets: number
  kpi_sla_ratio: number | null
  time_series: { bucket: string; points: TimeSeriesPoint[] }
  by_metier: CountEntry[]
  by_duration_bucket: CountEntry[]
  by_zone: ZoneEntry[]
  alerts: AlertEntry[]
  seuil_alerte: number
}

function extractErrorMessage(error: unknown, fallback: string): string {
  const anyError = error as { response?: { data?: { detail?: string } }; message?: string }
  return anyError.response?.data?.detail || anyError.message || fallback
}

export async function getMaintenanceFilters(zone: string): Promise<MaintenanceFilters> {
  try {
    const resp = await apiClient.get('/api/v1/maintenance/filters', { params: { zone } })
    return resp.data as MaintenanceFilters
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to load maintenance filters'))
  }
}

export async function getMaintenanceDashboard(params: DashboardParams): Promise<MaintenanceDashboard> {
  try {
    const resp = await apiClient.get('/api/v1/maintenance/dashboard', { params })
    return resp.data as MaintenanceDashboard
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to load maintenance dashboard'))
  }
}

export async function getMaintenanceThreshold(): Promise<number> {
  try {
    const resp = await apiClient.get('/api/v1/maintenance/settings')
    return resp.data.seuil_alerte as number
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to load maintenance threshold'))
  }
}

export async function updateMaintenanceThreshold(seuil_alerte: number): Promise<number> {
  try {
    const resp = await apiClient.put('/api/v1/maintenance/settings', { seuil_alerte })
    return resp.data.seuil_alerte as number
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to update maintenance threshold'))
  }
}
