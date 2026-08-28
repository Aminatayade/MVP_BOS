import { useEffect, useState } from 'react'
import { getEquipmentSummary, type EquipmentMetricSummary } from '../services/arcadeApi'

type Props = {
  globalId: string
  label: string
  x: number
  y: number
  onClose: () => void
}

// Popup flottante ancrée sur le nœud Equipment cliqué dans le graphe
// (coordonnées écran fournies par BimGraphViewer via node.renderedPosition()).
// Pas de mini-graphique pour cette itération : valeur courante uniquement --
// la visualisation de l'historique est prévue côté ThatOpen (3D) plus tard.
// `history` est déjà renvoyée par l'API (voir arcadeApi.ts) pour ce futur usage.
export default function EquipmentTelemetryPopup({ globalId, label, x, y, onClose }: Props) {
  const [metrics, setMetrics] = useState<EquipmentMetricSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setMetrics(null)
    setError(null)

    getEquipmentSummary(globalId)
      .then((result) => {
        if (!cancelled) setMetrics(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Erreur inattendue')
      })

    return () => {
      cancelled = true
    }
  }, [globalId])

  return (
    <div
      className="equipment-popup"
      style={{ position: 'absolute', left: x + 24, top: y, transform: 'translateY(-50%)' }}
      // Un clic dans la popup ne doit pas se propager jusqu'au fond du canvas
      // (qui ferme la popup) ni jusqu'à Cytoscape (pan/zoom).
      onClick={(event) => event.stopPropagation()}
    >
      <div className="equipment-popup__head">
        <span className="equipment-popup__title">{label}</span>
        <button type="button" className="equipment-popup__close" onClick={onClose} aria-label="Fermer">
          ✕
        </button>
      </div>

      {error && <p className="equipment-popup__error">{error}</p>}

      {!error && metrics === null && <p className="equipment-popup__muted">Chargement…</p>}

      {!error && metrics !== null && metrics.length === 0 && (
        <p className="equipment-popup__muted">Aucune télémétrie simulée pour ce type d'équipement.</p>
      )}

      {!error && metrics !== null && metrics.length > 0 && (
        <div className="equipment-popup__metrics">
          {metrics.map((m) => {
            const rawValue = m.current?.value
            const displayValue =
              rawValue !== undefined && rawValue !== null
                ? m.value_labels?.[String(rawValue)] ?? rawValue
                : '—'
            return (
              <div key={m.metric} className="equipment-popup__metric">
                <span className="equipment-popup__metric-name">{m.metric}</span>
                <span className="equipment-popup__metric-value">
                  {displayValue}
                  {typeof displayValue === 'number' && m.unit ? ` ${m.unit}` : ''}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
