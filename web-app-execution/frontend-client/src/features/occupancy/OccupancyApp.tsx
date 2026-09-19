/**
 * FILE: features/occupancy/OccupancyApp.tsx
 * DESCRIPTION: App "Suivi des usages" de BOS Exécution -- cockpit
 *              "Compréhension du taux d'usage des espaces", accessible
 *              depuis l'écran d'accueil (features/home/AppLauncher.tsx).
 *              Scopé par ZONE comme les autres cockpits (ZonePicker
 *              partagé). Contrairement à Maintenance, les données sources
 *              vivent dans ArcadeDB (capteurs de présence simulés,
 *              EquipmentReading) -- voir api-gateway/bim_proxy.py
 *              /graph/occupancy-dashboard.
 *
 *              Zone sans Room (ex: un bâtiment technique) : état vide
 *              honnête, pas de repli fabriqué -- un tel bâtiment n'a
 *              réellement aucun espace occupé par des humains.
 */
import { useEffect, useState } from 'react'
import ZonePicker from '../shared/ZonePicker'
import { getFloors, type Floor } from '../bim/services/arcadeApi'
import FilterBar, { type FilterValues } from './components/FilterBar'
import OccupancyBarChart from './components/OccupancyBarChart'
import OccupancyDonutChart from './components/OccupancyDonutChart'
import ReservationRateCard from './components/ReservationRateCard'
import MonthlyAverageCard from './components/MonthlyAverageCard'
import ThresholdCountsCard from './components/ThresholdCountsCard'
import HeatmapLinkButton from './components/HeatmapLinkButton'
import SpaceListLinkButton from './components/SpaceListLinkButton'
import { periodRange } from './period'
import {
  getOccupancyDashboard, getOccupancyThresholds, updateOccupancyThresholds,
  type OccupancyDashboard,
} from './services/occupancyApi'

const DEFAULT_VALUES: FilterValues = {
  plage: '7-19', typeEspace: '', etage: '', periode: 'mois', offset: 0,
  seuilBas: 20, seuilHaut: 80, compareEnabled: false, compareOffset: -12,
}

export default function OccupancyApp() {
  const [zone, setZone] = useState('')
  const [loadedZone, setLoadedZone] = useState<string | null>(null)
  const [zoneLoading, setZoneLoading] = useState(false)

  const [floors, setFloors] = useState<Floor[]>([])
  const [values, setValues] = useState<FilterValues>(DEFAULT_VALUES)
  const [dashboard, setDashboard] = useState<OccupancyDashboard | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const openZone = async () => {
    const trimmed = zone.trim()
    if (!trimmed) return
    setZoneLoading(true)
    setError('')
    try {
      const [zoneFloors, thresholds] = await Promise.all([
        getFloors(trimmed).catch(() => []),
        getOccupancyThresholds().catch(() => ({ seuil_bas: DEFAULT_VALUES.seuilBas, seuil_haut: DEFAULT_VALUES.seuilHaut })),
      ])
      setFloors(zoneFloors)
      setValues({ ...DEFAULT_VALUES, seuilBas: thresholds.seuil_bas, seuilHaut: thresholds.seuil_haut })
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

    const { startMs, endMs } = periodRange(values.periode, values.offset)
    const compare = values.compareEnabled
      ? periodRange(values.periode, values.offset + values.compareOffset)
      : null

    getOccupancyDashboard({
      zone: loadedZone,
      start: startMs,
      end: endMs,
      etage: values.etage || undefined,
      type_espace: values.typeEspace || undefined,
      plage: values.plage,
      seuil_bas: values.seuilBas,
      seuil_haut: values.seuilHaut,
      compare_start: compare?.startMs,
      compare_end: compare?.endMs,
    })
      .then((data) => { if (!cancelled) setDashboard(data) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Erreur') })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [loadedZone, values])

  const handleFilterChange = (patch: Partial<FilterValues>) => setValues((prev) => ({ ...prev, ...patch }))

  const handleSaveSeuils = (seuilBas: number, seuilHaut: number) => {
    updateOccupancyThresholds({ seuil_bas: seuilBas, seuil_haut: seuilHaut }).catch(() => {
      // Best-effort : la valeur locale reste affichée même si la
      // persistance échoue (pas de blocage de l'UI pour un réglage annexe).
    })
  }

  return (
    <div className="panel" style={{ marginTop: 24 }}>
      <h3>👥 Suivi des usages</h3>
      <p className="panel-hint">Compréhension du taux d'usage des espaces par zone.</p>

      <ZonePicker zone={zone} onZoneChange={setZone} onOpen={openZone} loading={zoneLoading} label="Ouvrir" />

      {error && <p className="panel-hint" style={{ color: 'var(--danger)' }}>{error}</p>}

      {loadedZone && (
        <>
          <FilterBar floors={floors} values={values} onChange={handleFilterChange} onSaveSeuils={handleSaveSeuils} />

          {loading && <p className="panel-hint">Chargement…</p>}

          {dashboard && !loading && !error && dashboard.has_rooms === false && (
            <p className="panel-hint">
              Aucun espace pour cette zone — {loadedZone} ne contient pas de pièce (bâtiment technique).
            </p>
          )}

          {dashboard && !loading && !error && dashboard.has_rooms && dashboard.bars && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16, marginTop: 8 }}>
              <div style={{ gridColumn: '1 / -1' }}>
                <OccupancyBarChart buckets={dashboard.bars.buckets} seriesA={dashboard.bars.series_a} seriesB={dashboard.bars.series_b} />
              </div>
              <OccupancyDonutChart
                occupiedPct={dashboard.donut!.occupied_pct}
                occupiedCount={dashboard.donut!.occupied_count}
                freeCount={dashboard.donut!.free_count}
              />
              <ReservationRateCard pct={dashboard.reservation_rate!.pct} />
              <MonthlyAverageCard pct={dashboard.monthly_avg_since_year_start!} />
              <ThresholdCountsCard
                seuilBas={dashboard.threshold_counts!.seuil_bas}
                seuilHaut={dashboard.threshold_counts!.seuil_haut}
                occupancyBelow={dashboard.threshold_counts!.occupancy_below}
                occupancyAbove={dashboard.threshold_counts!.occupancy_above}
                reservationBelow={dashboard.threshold_counts!.reservation_below}
                reservationAbove={dashboard.threshold_counts!.reservation_above}
              />
              <HeatmapLinkButton />
              <SpaceListLinkButton />
            </div>
          )}
        </>
      )}
    </div>
  )
}
