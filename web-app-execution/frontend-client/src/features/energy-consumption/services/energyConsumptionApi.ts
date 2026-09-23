/**
 * FILE: features/energy-consumption/services/energyConsumptionApi.ts
 * DESCRIPTION: Client REST du cockpit fixe "Consommation énergétique".
 *              `getEnergyConsumptionDashboard` appelle bim_api (via le proxy
 *              api-gateway/bim_proxy.py, /graph/energy-consumption-dashboard)
 *              -- les données sources (power_instant_kw) vivent dans
 *              ArcadeDB, pas Postgres. Aucun réglage persisté pour ce
 *              cockpit (pas de seuil comme Maintenance/Occupancy).
 */
import apiClient from '../../../services/apiClient'

export interface EnergyConsumptionDashboardParams {
  zone: string
  start: number
  end: number
  compare_start?: number
  compare_end?: number
  plage?: string
  type_consommation?: string
  sous_categorie?: string
}

export interface EnergyConsumptionDashboard {
  ok: true
  zone: string
  has_equipment: boolean
  bars?: {
    days: string[]
    subcategories: string[]
    series: Record<string, number[]>
  }
  kpi_realtime_kw?: number
  kpi_current_month_kwh?: number
  kpi_previous_month_kwh?: number | null
  kpi_variation_pct?: number | null
}

function extractErrorMessage(error: unknown, fallback: string): string {
  const anyError = error as { response?: { data?: { error?: string; detail?: string } }; message?: string }
  return anyError.response?.data?.error || anyError.response?.data?.detail || anyError.message || fallback
}

export async function getEnergyConsumptionDashboard(
  params: EnergyConsumptionDashboardParams,
): Promise<EnergyConsumptionDashboard> {
  try {
    const resp = await apiClient.get('/api/bim/graph/energy-consumption-dashboard', { params })
    return resp.data as EnergyConsumptionDashboard
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Échec du chargement du cockpit de consommation énergétique'))
  }
}
