import { formatMetricValue, truncateLabel } from '../services/arcadeApi'
import { useEquipmentTelemetry } from '../hooks/useEquipmentTelemetry'
import MetricHistoryPanel from './MetricHistoryPanel'

type Props = {
  globalId: string
  zone: string
  label: string
  x: number
  y: number
  onClose: () => void
}

// Popup flottante ancrée sur le nœud Equipment cliqué dans le graphe
// (coordonnées écran fournies par BimGraphViewer via node.renderedPosition()).
// Valeur courante + historique tenus à jour en temps réel via WebSocket, voir
// useEquipmentTelemetry.
export default function EquipmentTelemetryPopup({ globalId, zone, label, x, y, onClose }: Props) {
  const { metrics, error } = useEquipmentTelemetry(globalId, zone)

  return (
    <div
      className="equipment-popup"
      // width/boxSizing en dur : garantit la largeur même si la feuille de
      // style externe n'a pas (encore) été rechargée par le navigateur --
      // la classe CSS reste la source de vérité pour le reste (couleurs,
      // ombre...), seule la contrainte de largeur est dupliquée ici.
      style={{ position: 'absolute', left: x + 24, top: y, transform: 'translateY(-50%)', width: 220, boxSizing: 'border-box' }}
      // Un clic dans la popup ne doit pas se propager jusqu'au fond du canvas
      // (qui ferme la popup) ni jusqu'à Cytoscape (pan/zoom).
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

      {!error && metrics === null && <p className="equipment-popup__muted">Chargement…</p>}

      {!error && metrics !== null && metrics.length === 0 && (
        <p className="equipment-popup__muted">Aucune télémétrie simulée pour ce type d'équipement.</p>
      )}

      {!error && metrics !== null && metrics.length > 0 && (
        <div className="equipment-popup__metrics">
          {metrics.map((m) => (
            <div key={m.metric} className="equipment-popup__metric-block">
              <div className="equipment-popup__metric">
                <span className="equipment-popup__metric-name">{m.metric}</span>
                <span className="equipment-popup__metric-value">{formatMetricValue(m)}</span>
              </div>
              <MetricHistoryPanel metric={m} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
