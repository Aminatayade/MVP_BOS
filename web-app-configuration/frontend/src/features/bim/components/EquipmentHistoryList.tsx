/**
 * FILE: features/bim/components/EquipmentHistoryList.tsx
 * DESCRIPTION: Liste compacte des dernières valeurs d'historique d'une
 *              métrique (horodatage + valeur exacte) -- pas de graphique,
 *              juste les données. Réutilisée par la popup graphe ET la
 *              popup 3D.
 */
import { formatHistoryTimestamp, formatValue, type EquipmentMetricSummary } from '../services/arcadeApi'

type Props = {
  metric: EquipmentMetricSummary
  limit?: number
}

export default function EquipmentHistoryList({ metric, limit = 5 }: Props) {
  if (metric.history.length === 0) return null

  // Le plus récent en premier -- `history` arrive du backend trié
  // chronologiquement croissant (voir query_readings, ORDER BY ts ASC).
  const recent = metric.history.slice(-limit).reverse()

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 11 }}>
      {recent.map((point, i) => (
        <div key={`${point.timestamp}-${i}`} style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
          <span style={{ color: '#94a3b8' }}>{formatHistoryTimestamp(point.timestamp)}</span>
          <span style={{ color: '#e2e8f0', fontWeight: 600 }}>{formatValue(point.value, metric)}</span>
        </div>
      ))}
    </div>
  )
}
