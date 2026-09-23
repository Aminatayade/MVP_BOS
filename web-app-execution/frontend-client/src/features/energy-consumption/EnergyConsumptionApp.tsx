/**
 * FILE: features/energy-consumption/EnergyConsumptionApp.tsx
 * DESCRIPTION: App "Consommation énergétique" de BOS Exécution -- cockpit
 *              fixe (client: "Cockpit 1 : cockpit de consommation
 *              énergétique"), distinct du constructeur générique "Cockpit
 *              Énergie" (features/cockpits/) qui reste inchangé à côté.
 *              Scopé par ZONE comme les autres cockpits (ZonePicker
 *              partagé). Données sources dans ArcadeDB (power_instant_kw
 *              des équipements taggés energy_subcategory, voir
 *              equipment_simulation_catalog.json) -- voir
 *              api-gateway/bim_proxy.py /graph/energy-consumption-dashboard.
 *
 *              Zone sans équipement taggé pour la combinaison de filtres
 *              choisie (ex: sous_categorie=ascenseurs sur un bâtiment sans
 *              ascenseur) : état vide honnête, pas de repli fabriqué.
 */
import { useEffect, useState } from 'react'
import ZonePicker from '../shared/ZonePicker'
import { periodRange, formatPeriodLabel } from '../cockpits/utils/period'
import FilterBar, { type FilterValues } from './components/FilterBar'
import EnergyStackedBarChart from './components/EnergyStackedBarChart'
import RealtimeKpiCard from './components/RealtimeKpiCard'
import CurrentMonthKpiCard from './components/CurrentMonthKpiCard'
import PreviousMonthKpiCard from './components/PreviousMonthKpiCard'
import { getEnergyConsumptionDashboard, type EnergyConsumptionDashboard } from './services/energyConsumptionApi'

const DEFAULT_VALUES: FilterValues = {
  offset: 0, plage: 'toute_journee', typeConsommation: 'total', sousCategorie: '',
}

export default function EnergyConsumptionApp() {
  const [zone, setZone] = useState('')
  const [loadedZone, setLoadedZone] = useState<string | null>(null)
  const [zoneLoading, setZoneLoading] = useState(false)

  const [values, setValues] = useState<FilterValues>(DEFAULT_VALUES)
  const [dashboard, setDashboard] = useState<EnergyConsumptionDashboard | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const openZone = () => {
    const trimmed = zone.trim()
    if (!trimmed) return
    setValues(DEFAULT_VALUES)
    setLoadedZone(trimmed)
  }

  useEffect(() => {
    if (!loadedZone) return
    let cancelled = false
    setLoading(true)
    setError('')

    const current = periodRange('mois', values.offset)
    // "Mois n-1" est TOUJOURS le mois calendaire précédent, décalage fixe
    // -- pas une case à cocher "comparer" comme le cockpit Occupancy : le
    // client demande une comparaison systématique, pas optionnelle.
    const previous = periodRange('mois', values.offset - 1)

    getEnergyConsumptionDashboard({
      zone: loadedZone,
      start: current.startMs,
      end: current.endMs,
      compare_start: previous.startMs,
      compare_end: previous.endMs,
      plage: values.plage,
      type_consommation: values.typeConsommation,
      sous_categorie: values.sousCategorie || undefined,
    })
      .then((data) => { if (!cancelled) setDashboard(data) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Erreur') })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [loadedZone, values])

  const handleFilterChange = (patch: Partial<FilterValues>) => setValues((prev) => ({ ...prev, ...patch }))

  return (
    <div className="panel" style={{ marginTop: 24 }}>
      <h3>⚡ Consommation énergétique</h3>
      <p className="panel-hint">Suivi mensuel de la consommation par sous-catégorie, avec comparaison au mois précédent.</p>

      <ZonePicker zone={zone} onZoneChange={setZone} onOpen={openZone} loading={zoneLoading} label="Ouvrir" />

      {error && <p className="panel-hint" style={{ color: 'var(--danger)' }}>{error}</p>}

      {loadedZone && (
        <>
          <FilterBar values={values} onChange={handleFilterChange} />

          {loading && <p className="panel-hint">Chargement…</p>}

          {dashboard && !loading && !error && dashboard.has_equipment === false && (
            <p className="panel-hint">
              Aucune donnée pour cette combinaison de filtres — {loadedZone} n'a pas d'équipement de ce type dans ce
              périmètre.
            </p>
          )}

          {dashboard && !loading && !error && dashboard.has_equipment && dashboard.bars && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16, marginTop: 8 }}>
              <div style={{ gridColumn: '1 / -1' }}>
                <EnergyStackedBarChart
                  days={dashboard.bars.days}
                  subcategories={dashboard.bars.subcategories}
                  series={dashboard.bars.series}
                />
              </div>
              <RealtimeKpiCard kw={dashboard.kpi_realtime_kw ?? 0} />
              <CurrentMonthKpiCard
                kwh={dashboard.kpi_current_month_kwh ?? 0}
                monthLabel={formatPeriodLabel('mois', values.offset)}
              />
              <PreviousMonthKpiCard
                kwh={dashboard.kpi_previous_month_kwh ?? null}
                pct={dashboard.kpi_variation_pct ?? null}
                monthLabel={formatPeriodLabel('mois', values.offset - 1)}
              />
            </div>
          )}
        </>
      )}
    </div>
  )
}
