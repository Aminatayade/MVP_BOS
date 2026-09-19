/**
 * FILE: features/maintenance/MaintenanceApp.tsx
 * DESCRIPTION: App "Maintenance" de BOS Exécution -- cockpit "Statistiques
 *              d'interventions" (filtres en cascade + 7 widgets), accessible
 *              depuis l'écran d'accueil (features/home/AppLauncher.tsx).
 *              Scopé par ZONE comme le Jumeau numérique et le Cockpit
 *              Énergie (ZonePicker partagé, features/shared/ZonePicker.tsx) --
 *              chaque projet a ses propres tickets, jamais un jeu de données
 *              générique partagé entre bâtiments.
 *
 *              Un seul fetch (/dashboard) nourrit les 7 widgets une fois la
 *              zone choisie : toute l'agrégation (séries temporelles,
 *              tranches de durée, seuil) est déjà calculée côté serveur,
 *              voir api-gateway/maintenance.py.
 */
import { useEffect, useState } from 'react'
import ZonePicker from '../shared/ZonePicker'
import FilterBar, { type FilterValues } from './components/FilterBar'
import KpiTicketsCard from './components/KpiTicketsCard'
import KpiSlaRatioCard from './components/KpiSlaRatioCard'
import TimeSeriesChart from './components/TimeSeriesChart'
import ByMetierPieChart from './components/ByMetierPieChart'
import DurationBucketPieChart from './components/DurationBucketPieChart'
import ByZoneBarChart from './components/ByZoneBarChart'
import ThresholdAlertsList from './components/ThresholdAlertsList'
import { getMaintenanceFilters, getMaintenanceDashboard, type MaintenanceFilters, type MaintenanceDashboard } from './services/maintenanceApi'

const EMPTY_FILTERS: MaintenanceFilters = { metiers: [], etages: [] }

const DEFAULT_VALUES: FilterValues = {
  // "trimestre" par défaut plutôt que "mois" : avec ~142 tickets de démo par
  // zone répartis sur 180 jours, une fenêtre d'un mois ne capture qu'une
  // trentaine de tickets (aucun dépassement de seuil visible) -- le
  // trimestre montre un vrai contraste (certains étages au-dessus du seuil,
  // d'autres en dessous).
  metier: '', sousCategorie: '', statutWorkflow: '', priorite: '', etage: '', periode: 'trimestre',
}

export default function MaintenanceApp() {
  const [zone, setZone] = useState('')
  const [loadedZone, setLoadedZone] = useState<string | null>(null)
  const [zoneLoading, setZoneLoading] = useState(false)

  const [filters, setFilters] = useState<MaintenanceFilters>(EMPTY_FILTERS)
  const [values, setValues] = useState<FilterValues>(DEFAULT_VALUES)
  const [dashboard, setDashboard] = useState<MaintenanceDashboard | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const openZone = async () => {
    const trimmed = zone.trim()
    if (!trimmed) return
    setZoneLoading(true)
    setError('')
    try {
      const found = await getMaintenanceFilters(trimmed)
      setFilters(found)
      setValues(DEFAULT_VALUES)
      setLoadedZone(trimmed)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur')
    } finally {
      setZoneLoading(false)
    }
  }

  useEffect(() => {
    if (!loadedZone) return
    let cancelled = false
    setLoading(true)
    setError('')
    getMaintenanceDashboard({
      zone: loadedZone,
      metier: values.metier || undefined,
      sous_categorie: values.sousCategorie || undefined,
      statut_workflow: values.statutWorkflow || undefined,
      priorite: values.priorite || undefined,
      etage: values.etage || undefined,
      periode: values.periode,
    })
      .then((data) => { if (!cancelled) setDashboard(data) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Erreur') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [loadedZone, values])

  const handleFilterChange = (patch: Partial<FilterValues>) => setValues((prev) => ({ ...prev, ...patch }))

  return (
    <div className="panel" style={{ marginTop: 24 }}>
      <h3>🛠️ Statistiques d'interventions</h3>
      <p className="panel-hint">Suivi des interventions de maintenance par métier, priorité et zone.</p>

      <ZonePicker zone={zone} onZoneChange={setZone} onOpen={openZone} loading={zoneLoading} label="Ouvrir" />

      {error && <p className="panel-hint" style={{ color: 'var(--danger)' }}>{error}</p>}

      {loadedZone && (
        <>
          <FilterBar filters={filters} values={values} onChange={handleFilterChange} />

          {loading && <p className="panel-hint">Chargement…</p>}

          {dashboard && !loading && !error && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16, marginTop: 8 }}>
              <KpiTicketsCard total={dashboard.kpi_total_tickets} />
              <KpiSlaRatioCard ratio={dashboard.kpi_sla_ratio} />
              <div style={{ gridColumn: '1 / -1' }}>
                <TimeSeriesChart bucket={dashboard.time_series.bucket} points={dashboard.time_series.points} />
              </div>
              <ByMetierPieChart data={dashboard.by_metier} />
              <DurationBucketPieChart data={dashboard.by_duration_bucket} />
              <ByZoneBarChart data={dashboard.by_zone} />
              <ThresholdAlertsList alerts={dashboard.alerts} seuilAlerte={dashboard.seuil_alerte} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
