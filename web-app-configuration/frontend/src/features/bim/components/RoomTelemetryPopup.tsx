/**
 * FILE: features/bim/components/RoomTelemetryPopup.tsx
 * DESCRIPTION: Popup flottante au clic sur une pièce (Room/Space) dans le
 *              graphe -- logique métier demandée : lister les métriques
 *              DISPONIBLES dans la pièce (ex: Occupation, État, Température),
 *              pour chaque métrique lister LE OU LES capteurs qui la
 *              fournissent, et pour chaque capteur sa valeur courante +
 *              historique. Contrairement à EquipmentTelemetryPopup (un seul
 *              équipement), ici on agrège TOUS les équipements de la pièce
 *              puis on regroupe par nom de métrique -- pas de moyenne, le
 *              détail par capteur reste visible.
 *
 * Réutilise 100% de code déjà existant/testé : getRoomEquipment (déjà
 * utilisé pour le dépliage "N équipements" du graphe), parseArcadeNode
 * (extraction id/label), getEquipmentSummary (même endpoint que la popup
 * équipement). Pas de nouvel endpoint backend -- le regroupement par
 * métrique se fait ici, côté client.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  formatMetricValue,
  getEquipmentSummary,
  getRoomEquipment,
  truncateLabel,
  type EquipmentMetricSummary,
} from '../services/arcadeApi'
import { parseArcadeNode } from '../utils/graphUtils'
import { useEquipmentLivePoint, type LivePoint } from '../hooks/useEquipmentTelemetry'
import MetricHistoryPanel from './MetricHistoryPanel'

const MAX_HISTORY_POINTS = 50

type Props = {
  zone: string
  roomId: string
  label: string
  x: number
  y: number
  onClose: () => void
}

type SensorMetric = {
  equipmentId: string
  equipmentLabel: string
  metric: EquipmentMetricSummary
}

// metric name -> capteurs qui la fournissent, dans l'ordre de première
// rencontre (Map préserve l'ordre d'insertion des clés en JS).
type GroupedMetrics = Map<string, SensorMetric[]>

/**
 * Fusion immuable d'un point WS dans l'état groupé -- ne touche QUE
 * l'entrée (metric, equipmentId) concernée, laisse le reste de la Map
 * intact (React ne re-rendrait pas sinon les groupes non affectés, mais
 * surtout ça évite de reconstruire tout l'état pour un seul point).
 */
function applyLivePoint(groups: GroupedMetrics, equipmentId: string, point: LivePoint): GroupedMetrics {
  const entries = groups.get(point.metric)
  if (!entries) return groups
  const index = entries.findIndex((entry) => entry.equipmentId === equipmentId)
  if (index === -1) return groups

  const entry = entries[index]
  const history = [...entry.metric.history, { value: point.value, timestamp: point.timestamp }].slice(
    -MAX_HISTORY_POINTS,
  )
  const updatedEntry: SensorMetric = {
    ...entry,
    metric: {
      ...entry.metric,
      current: { value: point.value, timestamp: point.timestamp, quality: point.quality },
      history,
    },
  }

  const updatedEntries = [...entries]
  updatedEntries[index] = updatedEntry
  const nextGroups = new Map(groups)
  nextGroups.set(point.metric, updatedEntries)
  return nextGroups
}

/**
 * Un seul rôle : ouvrir le WS d'UN équipement (mutualisé même s'il fournit
 * plusieurs métriques affichées dans des groupes différents -- une seule
 * connexion, pas une par métrique) et remonter chaque point au parent. Ne
 * rend rien -- l'affichage vit entièrement dans `groups` côté parent.
 */
function SensorLiveBridge({ equipmentId, onPoint }: { equipmentId: string; onPoint: (point: LivePoint) => void }) {
  useEquipmentLivePoint(equipmentId, onPoint)
  return null
}

export default function RoomTelemetryPopup({ zone, roomId, label, x, y, onClose }: Props) {
  const [groups, setGroups] = useState<GroupedMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setGroups(null)
    setError(null)

    getRoomEquipment(zone, roomId)
      .then(async (rows) => {
        const equipment = rows
          .map((raw) => parseArcadeNode((raw as Record<string, unknown>).node ?? raw))
          .filter((parsed): parsed is NonNullable<typeof parsed> => parsed !== null)

        if (equipment.length === 0) {
          if (!cancelled) setGroups(new Map())
          return
        }

        const results = await Promise.allSettled(equipment.map((e) => getEquipmentSummary(e.id, zone)))
        if (cancelled) return

        const grouped: GroupedMetrics = new Map()
        results.forEach((result, index) => {
          if (result.status !== 'fulfilled') return
          const eq = equipment[index]
          for (const metric of result.value) {
            const entries = grouped.get(metric.metric) ?? []
            entries.push({ equipmentId: eq.id, equipmentLabel: eq.label, metric })
            grouped.set(metric.metric, entries)
          }
        })
        setGroups(grouped)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Erreur inattendue')
      })

    return () => {
      cancelled = true
    }
  }, [roomId, zone])

  const handleLivePoint = useCallback(
    (equipmentId: string) => (point: LivePoint) => {
      setGroups((prev) => (prev ? applyLivePoint(prev, equipmentId, point) : prev))
    },
    [],
  )

  // Une connexion WS par CAPTEUR, pas par (capteur, métrique) -- un même
  // équipement peut apparaître dans plusieurs groupes (ex: une CTA fournit
  // "state" ET "temperature") sans qu'on ouvre deux fois le même flux.
  const uniqueEquipmentIds = groups
    ? Array.from(new Set(Array.from(groups.values()).flatMap((entries) => entries.map((e) => e.equipmentId))))
    : []

  return (
    <div
      className="equipment-popup"
      style={{
        position: 'absolute',
        left: x + 24,
        top: y,
        transform: 'translateY(-50%)',
        width: 260,
        boxSizing: 'border-box',
        maxHeight: 420,
        overflowY: 'auto',
      }}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="equipment-popup__head">
        <span className="equipment-popup__title" title={label}>
          {truncateLabel(label)}
        </span>
        <button type="button" className="equipment-popup__close" onClick={onClose} aria-label="Fermer">
          ✕
        </button>
      </div>

      {error && <p className="equipment-popup__error">{error}</p>}

      {!error && groups === null && <p className="equipment-popup__muted">Chargement…</p>}

      {!error && groups !== null && groups.size === 0 && (
        <p className="equipment-popup__muted">Aucun équipement avec télémétrie dans cette pièce.</p>
      )}

      {!error && groups !== null && groups.size > 0 && (
        <div className="equipment-popup__metrics">
          {Array.from(groups.entries()).map(([metricName, sensors]) => (
            <div key={metricName} className="equipment-popup__metric-block">
              <div className="equipment-popup__metric-name" style={{ fontWeight: 600 }}>
                {metricName}
              </div>
              {sensors.map((sensor) => (
                <div key={sensor.equipmentId} style={{ marginLeft: 8 }}>
                  <div className="equipment-popup__metric">
                    <span className="equipment-popup__metric-name" title={sensor.equipmentLabel}>
                      {truncateLabel(sensor.equipmentLabel, 22)}
                    </span>
                    <span className="equipment-popup__metric-value">{formatMetricValue(sensor.metric)}</span>
                  </div>
                  <MetricHistoryPanel metric={sensor.metric} limit={3} />
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {uniqueEquipmentIds.map((equipmentId) => (
        <SensorLiveBridge key={equipmentId} equipmentId={equipmentId} onPoint={handleLivePoint(equipmentId)} />
      ))}
    </div>
  )
}
