/**
 * FILE: features/cockpits/components/WidgetBlock.tsx
 * DESCRIPTION: Rendu générique d'UN widget de cockpit (barres/courbe/
 *              camembert/semaine type/indicateur). Résout la stratégie de
 *              récupération de données depuis `widget.type` + `space`, en
 *              réutilisant TEL QUEL les fonctions déjà utilisées par
 *              l'ancien panneau "Consommation électrique"
 *              (getBuildingConsumption/getLocationReadings/getLocationSummary,
 *              voir features/bim/services/arcadeApi.ts) -- aucune requête
 *              n'est dupliquée ici, ce composant les orchestre seulement.
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import {
  getBuildingConsumption,
  getEquipmentSummary,
  getLocationReadings,
  getLocationSummary,
  type ReadingPoint,
} from '../../bim/services/arcadeApi'
import { categoryLabel, type CockpitPeriod, type CockpitSpace, type CockpitWidget } from '../types'
import { useChartInstance } from '../utils/useChartInstance'
import { intervalToBucketSeconds, periodRange } from '../utils/period'
import { computeTypicalWeek } from '../utils/typicalWeek'
import { useWidgetAggregateSocket, type WidgetAggregatePayload } from '../hooks/useWidgetAggregateSocket'

const FLOOR_COLORS = ['#38bdf8', '#a3e635', '#fbbf24', '#f472b6', '#c084fc', '#fb923c']
// Mode "équipement ciblé" du widget Indicateur uniquement (effet dédié plus
// bas) -- hors périmètre du flux temps réel par websocket, reste en polling.
const REFRESH_MS = 30_000

type Props = {
  widget: CockpitWidget
  space: CockpitSpace
  period: CockpitPeriod
  zone: string
  onEdit: () => void
  onRemove: () => void
}

type BarDatum = { label: string; value: number }

export default function WidgetBlock({ widget, space, period, zone, onEdit, onRemove }: Props) {
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [unit, setUnit] = useState('')
  const [indicatorValue, setIndicatorValue] = useState<number | null>(null)
  const [indicatorLabel, setIndicatorLabel] = useState<string | null>(null)
  const [barData, setBarData] = useState<BarDatum[] | null>(null)
  const [lineDatasets, setLineDatasets] = useState<{ label: string; points: ReadingPoint[] }[] | null>(null)

  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  // La catégorie ne filtre QUE quand un seul type de donnée est sélectionné
  // -- avec plusieurs (ou aucun = "tous"), pas de filtre unique possible sur
  // ces endpoints, donc on ne filtre pas du tout (comportement de l'ancien
  // panneau, qui n'avait pas cette notion).
  const singleCategory = widget.dataTypes.length === 1 ? widget.dataTypes[0] : undefined
  const dataTypesKey = widget.dataTypes.join(',')

  // Calculés une seule fois par changement d'intervalle/période (pas à
  // chaque rendu -- periodRange(Date.now()) changerait sinon à chaque rendu
  // du composant, ce qui ferait reconnecter la websocket d'agrégation
  // ci-dessous en boucle, voir sa dépendance `key`). Bornage CALENDAIRE
  // (jour/semaine/mois/année civils, voir period.ts) : `offset` permet la
  // pagination "Mois précédent/suivant" du haut de page (CockpitBuilder.tsx).
  const bucketSeconds = useMemo(() => intervalToBucketSeconds(widget.interval), [widget.interval])
  const { startMs, endMs } = useMemo(
    () => periodRange(period.preset, period.offset ?? 0),
    [period.preset, period.offset],
  )

  // Applique un résultat déjà résolu à l'état affiché -- factorisé pour être
  // appelé À LA FOIS par le fetch REST initial (refresh(), ci-dessous) ET par
  // chaque message poussé par la websocket d'agrégation (onAggregatePayload,
  // plus bas) : les deux chemins doivent produire exactement le même état,
  // jamais deux logiques de mise en forme qui pourraient diverger.
  const applyIndicator = (value: number, unitValue: string) => {
    setIndicatorValue(value)
    setUnit(unitValue)
  }
  const applyBars = (bars: BarDatum[], unitValue: string) => {
    setBarData(bars)
    setUnit(unitValue)
  }
  const applyCourbe = (datasets: { label: string; points: ReadingPoint[] }[]) => {
    setLineDatasets(datasets)
  }

  useEffect(() => {
    let cancelled = false

    if (widget.type === 'indicateur' && widget.targetEquipmentId) {
      // Mode "équipement ciblé" -- pris en charge par l'effet dédié
      // ci-dessous, indépendant de l'espace sélectionné.
      return
    }

    if (space.ids.length === 0) {
      // Niveau Étage/Type de pièce/Pièce sélectionné mais pas encore de
      // valeur choisie -- pas d'appel avec un global_id vide (400 côté
      // backend), juste un état d'attente.
      setLoading(false)
      setError(null)
      setIndicatorValue(null)
      setBarData(null)
      setLineDatasets(null)
      return
    }

    const refresh = async () => {
      try {
        setError(null)

        if (widget.type === 'indicateur') {
          const dataTypes = widget.dataTypes.length ? widget.dataTypes : [undefined]
          const results = await Promise.all(
            dataTypes.map((dt) => getLocationSummary(zone, space.ids, widget.metric, widget.calcul, dt, startMs, endMs)),
          )
          if (cancelled) return
          const total = results.reduce((sum, r) => sum + (r.value ?? 0), 0)
          applyIndicator(Math.round(total * 100) / 100, results.find((r) => r.unit)?.unit ?? '')
        } else if (widget.type === 'barres' || widget.type === 'camembert') {
          if (space.level === 'batiment') {
            const consumption = await getBuildingConsumption(zone, space.ids[0], widget.metric, singleCategory)
            if (cancelled) return
            applyBars(consumption.by_floor.map((f) => ({ label: f.floor_name, value: f.value })), consumption.unit)
          } else {
            const dataTypes = widget.dataTypes.length ? widget.dataTypes : ['_all']
            const results = await Promise.all(
              dataTypes.map(async (dt) => ({
                label: dt === '_all' ? 'Tous types' : categoryLabel(dt),
                summary: await getLocationSummary(zone, space.ids, widget.metric, widget.calcul, dt === '_all' ? undefined : dt, startMs, endMs),
              })),
            )
            if (cancelled) return
            applyBars(
              results.map((r) => ({ label: r.label, value: r.summary.value ?? 0 })),
              results.find((r) => r.summary.unit)?.summary.unit ?? '',
            )
          }
        } else if (widget.type === 'courbe') {
          const dataTypes = widget.dataTypes.length ? widget.dataTypes : ['_all']
          const results = await Promise.all(
            dataTypes.map(async (dt) => ({
              label: dt === '_all' ? widget.title : categoryLabel(dt),
              points: await getLocationReadings(
                zone, space.ids, widget.metric, bucketSeconds, dt === '_all' ? undefined : dt, widget.calcul,
                undefined, startMs, endMs,
              ),
            })),
          )
          if (cancelled) return
          applyCourbe(results)
        } else if (widget.type === 'semaine_type') {
          // Indépendant du sélecteur Période global : une "semaine type" a
          // besoin de plusieurs semaines d'historique quelle que soit la
          // période affichée ailleurs dans le cockpit -- fenêtre fixe de 8
          // semaines, bucket 1h, raw_limit relevé pour couvrir ce volume.
          const points = await getLocationReadings(
            zone, space.ids, widget.metric, 3600, singleCategory, widget.calcul, 20000,
          )
          if (cancelled) return
          const typical = computeTypicalWeek(points)
          setBarData(typical.map((p) => ({ label: p.label, value: p.value })))
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Erreur inattendue')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    // Un seul fetch REST pour le premier affichage -- les mises à jour
    // suivantes arrivent via la websocket d'agrégation (voir
    // useWidgetAggregateSocket ci-dessous), plus de polling périodique.
    setLoading(true)
    refresh()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widget.type, widget.metric, widget.calcul, widget.interval, widget.targetEquipmentId, dataTypesKey, space.level, space.ids.join(','), period.preset, period.offset, bucketSeconds, startMs, endMs, zone])

  // Mises à jour poussées : dès qu'un équipement du périmètre du widget
  // publie une nouvelle mesure sur NATS, la gateway rappelle en interne le
  // MÊME endpoint REST que refresh() ci-dessus et pousse le résultat ici --
  // voir api-gateway/widget_aggregate.py pour la garantie de non-divergence.
  // Hors périmètre : mode "équipement ciblé" (pris en charge par l'effet
  // dédié plus bas, déjà événementiel via /ws/equipment/:id).
  const isTargetedIndicator = widget.type === 'indicateur' && !!widget.targetEquipmentId
  const aggregateParams = !isTargetedIndicator && space.ids.length > 0
    ? {
        widgetType: widget.type,
        spaceIds: space.ids,
        spaceLevel: space.level,
        metric: widget.metric,
        calcul: widget.calcul,
        dataTypes: widget.dataTypes,
        bucketSeconds,
        startMs,
        endMs,
        zone,
      }
    : null

  useWidgetAggregateSocket(aggregateParams, (payload: WidgetAggregatePayload) => {
    if (payload.type === 'indicateur') {
      applyIndicator(payload.value, payload.unit)
    } else if (payload.type === 'barres' || payload.type === 'camembert') {
      if (payload.building) {
        applyBars(payload.bars, payload.unit)
      } else {
        applyBars(
          payload.entries.map((e) => ({ label: e.code === '_all' ? 'Tous types' : categoryLabel(e.code), value: e.value })),
          payload.entries.find((e) => e.unit)?.unit ?? '',
        )
      }
    } else if (payload.type === 'courbe') {
      applyCourbe(payload.datasets.map((d) => ({ label: d.code === '_all' ? widget.title : categoryLabel(d.code), points: d.points })))
    } else if (payload.type === 'semaine_type') {
      const typical = computeTypicalWeek(payload.points)
      setBarData(typical.map((p) => ({ label: p.label, value: p.value })))
    }
  })

  // Mode "équipement ciblé" (widget Indicateur uniquement) : lit la dernière
  // valeur d'UN équipement précis via /timeseries/equipment-summary,
  // indépendamment de l'espace sélectionné en haut du cockpit. `value_labels`
  // du catalogue de simulation (ex: {"0":"OK","1":"Défaut"}) traduit la
  // valeur brute en libellé humain quand le type d'équipement en définit un.
  useEffect(() => {
    if (widget.type !== 'indicateur' || !widget.targetEquipmentId) {
      setIndicatorLabel(null)
      return
    }
    let cancelled = false
    const targetId = widget.targetEquipmentId

    const refresh = async () => {
      try {
        setError(null)
        const summary = await getEquipmentSummary(targetId)
        if (cancelled) return
        const metricEntry = summary.metrics.find((m) => m.metric === widget.metric) ?? summary.metrics[0] ?? null
        const rawValue = metricEntry?.current?.value ?? null
        setIndicatorValue(rawValue)
        setIndicatorLabel(rawValue != null ? metricEntry?.value_labels?.[String(rawValue)] ?? null : null)
        setUnit(metricEntry?.unit ?? '')
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Erreur inattendue')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    setLoading(true)
    refresh()
    const interval = window.setInterval(refresh, REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [widget.type, widget.targetEquipmentId, widget.metric])

  const hasData = space.ids.length > 0 || !!widget.targetEquipmentId
  const isBarLike = widget.type === 'barres' || widget.type === 'semaine_type'
  const isDonut = widget.type === 'camembert'
  // Widget 'courbe' : Ligne (historique classique) ou Barres groupées par
  // bucket -- même données (lineDatasets), juste un autre style de tracé
  // Chart.js (façon "Répartition Énergies" : une barre par catégorie et par
  // jour, pas une valeur agrégée unique comme le widget 'barres' classique).
  const isCourbeBars = widget.type === 'courbe' && widget.chartStyle === 'barres'
  const isLine = widget.type === 'courbe' && !isCourbeBars

  // Un bucket d'un jour ou plus (pagination "Mois précédent/suivant" avec
  // intervalle "Jour") : la portion HEURE de `ts` est toujours "00:00", donc
  // on affiche la date à la place -- sinon l'axe des abscisses serait
  // illisible (une série de "00:00" identiques).
  const formatTick = (ts: string): string => {
    const [datePart, timePart] = ts.split(' ')
    if (bucketSeconds >= 86400) {
      const [, month, day] = datePart.split('-')
      return `${day}/${month}`
    }
    return timePart?.slice(0, 5) ?? ts
  }

  useChartInstance(
    canvasRef,
    () => {
      if (isBarLike && barData) {
        return {
          type: 'bar',
          data: {
            labels: barData.map((d) => d.label),
            datasets: [{ data: barData.map((d) => d.value), backgroundColor: barData.map((_, i) => FLOOR_COLORS[i % FLOOR_COLORS.length]), borderRadius: 4 }],
          },
          options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => `${ctx.formattedValue} ${unit}` } } },
            scales: {
              x: { ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { display: false } },
              y: { ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { color: 'rgba(156,163,175,0.15)' } },
            },
          },
        }
      }
      if (isDonut && barData) {
        return {
          type: 'doughnut',
          data: {
            labels: barData.map((d) => d.label),
            datasets: [{ data: barData.map((d) => d.value), backgroundColor: barData.map((_, i) => FLOOR_COLORS[i % FLOOR_COLORS.length]), borderWidth: 2, borderColor: '#111827' }],
          },
          options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            plugins: {
              legend: { position: 'right', labels: { color: '#9ca3af', font: { size: 9 }, boxWidth: 8 } },
              tooltip: { callbacks: { label: (ctx) => `${ctx.label}: ${ctx.formattedValue} ${unit}` } },
            },
          },
        }
      }
      if (isCourbeBars && lineDatasets) {
        const palette = FLOOR_COLORS
        const labels = lineDatasets[0]?.points.map((p) => formatTick(p.ts)) ?? []
        return {
          type: 'bar',
          data: {
            labels,
            datasets: lineDatasets.map((ds, i) => ({
              label: ds.label, data: ds.points.map((p) => p.value),
              backgroundColor: palette[i % palette.length],
              borderRadius: 3,
            })),
          },
          options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            plugins: {
              legend: { display: lineDatasets.length > 1, labels: { color: '#9ca3af', font: { size: 9 } } },
              tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.formattedValue} ${unit}` } },
            },
            scales: {
              // stacked: true sur les deux axes -- une seule barre empilée
              // par jour/bucket (une portion par catégorie), pas des barres
              // groupées côte à côte, pour matcher le rendu visé (façon
              // "Répartition Énergies").
              x: { stacked: true, ticks: { color: '#9ca3af', font: { size: 9 }, maxRotation: 45, minRotation: 0 }, grid: { display: false } },
              y: { stacked: true, ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { color: 'rgba(156,163,175,0.15)' } },
            },
          },
        }
      }
      if (isLine && lineDatasets) {
        const palette = FLOOR_COLORS
        const labels = lineDatasets[0]?.points.map((p) => formatTick(p.ts)) ?? []
        return {
          type: 'line',
          data: {
            labels,
            datasets: lineDatasets.map((ds, i) => ({
              label: ds.label, data: ds.points.map((p) => p.value),
              borderColor: palette[i % palette.length],
              backgroundColor: `${palette[i % palette.length]}26`,
              borderWidth: 1.5, pointRadius: 0, fill: lineDatasets.length === 1, tension: 0.25,
            })),
          },
          options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            plugins: {
              legend: { display: lineDatasets.length > 1, labels: { color: '#9ca3af', font: { size: 9 } } },
              tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.formattedValue} ${unit}` } },
            },
            scales: {
              x: { ticks: { color: '#9ca3af', font: { size: 9 }, maxTicksLimit: 6 }, grid: { display: false } },
              y: { ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { color: 'rgba(156,163,175,0.15)' } },
            },
          },
        }
      }
      return null
    },
    [barData, lineDatasets, unit, isBarLike, isDonut, isLine, isCourbeBars, bucketSeconds],
  )

  return (
    <div className="panel" style={{ position: 'relative', minHeight: 200 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <strong style={{ fontSize: 13, color: 'var(--text-h)', flex: 1 }}>{widget.title}</strong>
        <button type="button" onClick={onEdit} title="Réglages" style={iconBtnStyle}>⚙️</button>
        <button type="button" onClick={onRemove} title="Retirer" style={iconBtnStyle}>×</button>
      </div>

      {/* Un widget ciblant un équipement précis n'a pas besoin de l'espace
          sélectionné en haut du cockpit -- indépendant de space.ids. */}
      {!hasData && <p className="panel-hint">Choisissez un espace en haut du cockpit.</p>}
      {hasData && error && <p className="panel-hint" style={{ color: '#e53e3e' }}>{error}</p>}
      {hasData && !error && loading && <p className="panel-hint">Chargement...</p>}

      {hasData && !error && !loading && widget.type === 'indicateur' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontSize: 32, fontWeight: 700, color: 'var(--text-h)' }}>
              {indicatorLabel ?? indicatorValue ?? '—'}
            </span>
            {!indicatorLabel && <span style={{ fontSize: 14, color: 'var(--text)' }}>{unit}</span>}
          </div>
          {widget.target != null && !widget.targetEquipmentId && indicatorValue != null && (
            <div
              style={{
                fontSize: 12, marginTop: 4,
                color: indicatorValue > widget.target ? '#e53e3e' : '#38a169',
              }}
            >
              Objectif : {widget.target}{unit} (
              {indicatorValue > widget.target ? '+' : ''}
              {Math.round(((indicatorValue - widget.target) / widget.target) * 1000) / 10}%)
            </div>
          )}
        </div>
      )}

      {hasData && !error && !loading && widget.type !== 'indicateur' && (
        <div style={{ position: 'relative', width: '100%', height: 180 }}>
          <canvas ref={canvasRef} />
        </div>
      )}
    </div>
  )
}

const iconBtnStyle: CSSProperties = {
  background: 'none', border: 'none', cursor: 'pointer', fontSize: 14, color: 'var(--text)', padding: 2, lineHeight: 1,
}
