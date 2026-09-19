/**
 * FILE: features/occupancy/services/occupancyApi.ts
 * DESCRIPTION: Client REST du cockpit "Compréhension du taux d'usage des
 *              espaces". `getOccupancyDashboard` appelle bim_api (via le
 *              proxy api-gateway/bim_proxy.py) -- les données sources
 *              (capteurs simulés, EquipmentReading) vivent dans ArcadeDB, pas
 *              Postgres, contrairement au cockpit Maintenance. Seul le seuil
 *              (`getOccupancyThreshold`/`updateOccupancyThreshold`) est une
 *              route api-gateway classique (Postgres, voir occupancy.py).
 */
import apiClient from '../../../services/apiClient'

export interface OccupancyDashboardParams {
  zone: string
  start: number
  end: number
  etage?: string
  type_espace?: string
  plage?: string
  seuil_bas?: number
  seuil_haut?: number
  compare_start?: number
  compare_end?: number
}

export interface SpaceEntry {
  room_id: string
  room_name: string | null
  space_type: string | null
  occupancy_pct: number | null
  reservation_pct: number | null
}

export interface OccupancyDashboard {
  ok: true
  zone: string
  has_rooms: boolean
  room_count?: number
  bars?: { buckets: string[]; series_a: number[]; series_b: number[] | null }
  donut?: { occupied_pct: number; occupied_count: number; free_count: number }
  reservation_rate?: { pct: number | null }
  monthly_avg_since_year_start?: number
  threshold_counts?: {
    seuil_bas: number
    seuil_haut: number
    occupancy_below: number
    occupancy_above: number
    reservation_below: number
    reservation_above: number
  }
  spaces?: SpaceEntry[]
}

function extractErrorMessage(error: unknown, fallback: string): string {
  const anyError = error as { response?: { data?: { error?: string; detail?: string } }; message?: string }
  return anyError.response?.data?.error || anyError.response?.data?.detail || anyError.message || fallback
}

export async function getOccupancyDashboard(params: OccupancyDashboardParams): Promise<OccupancyDashboard> {
  try {
    const resp = await apiClient.get('/api/bim/graph/occupancy-dashboard', { params })
    return resp.data as OccupancyDashboard
  } catch (error) {
    throw new Error(extractErrorMessage(error, "Échec du chargement du cockpit d'occupation"))
  }
}

export interface OccupancyThresholds {
  seuil_bas: number
  seuil_haut: number
}

export async function getOccupancyThresholds(): Promise<OccupancyThresholds> {
  try {
    const resp = await apiClient.get('/api/v1/occupancy/settings')
    return resp.data as OccupancyThresholds
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Échec du chargement des seuils'))
  }
}

export async function updateOccupancyThresholds(thresholds: OccupancyThresholds): Promise<OccupancyThresholds> {
  try {
    const resp = await apiClient.put('/api/v1/occupancy/settings', thresholds)
    return resp.data as OccupancyThresholds
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Échec de la mise à jour des seuils'))
  }
}
