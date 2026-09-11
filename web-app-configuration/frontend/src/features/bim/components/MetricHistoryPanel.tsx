/**
 * FILE: features/bim/components/MetricHistoryPanel.tsx
 * DESCRIPTION: Remplace un appel direct à EquipmentHistoryList dans les
 *              popups de télémétrie -- ajoute un bouton "Détails" qui
 *              bascule entre la liste texte compacte (par défaut) et le
 *              mini graphique Chart.js (EquipmentHistoryChart), pour la
 *              même donnée déjà en mémoire dans les deux cas.
 */
import { useState } from 'react'
import type { EquipmentMetricSummary } from '../services/arcadeApi'
import EquipmentHistoryChart from './EquipmentHistoryChart'
import EquipmentHistoryList from './EquipmentHistoryList'

type Props = {
  metric: EquipmentMetricSummary
  limit?: number
}

export default function MetricHistoryPanel({ metric, limit = 5 }: Props) {
  const [showChart, setShowChart] = useState(false)

  if (metric.history.length === 0) return null

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 2 }}>
        <button
          type="button"
          onClick={() => setShowChart((prev) => !prev)}
          style={{
            background: 'none',
            border: 'none',
            color: '#38bdf8',
            cursor: 'pointer',
            fontSize: 10,
            padding: 0,
          }}
        >
          {showChart ? 'Liste' : 'Détails'}
        </button>
      </div>
      {showChart ? <EquipmentHistoryChart metric={metric} /> : <EquipmentHistoryList metric={metric} limit={limit} />}
    </div>
  )
}
