/**
 * FILE: features/maintenance/components/ByMetierPieChart.tsx
 * DESCRIPTION: Widget 4 -- répartition des interventions par métier.
 */
import { useRef } from 'react'
import { useChartInstance } from '../../cockpits/utils/useChartInstance'
import type { CountEntry } from '../services/maintenanceApi'
import { CATEGORY_COLORS } from '../colors'

type Props = {
  data: CountEntry[]
}

export default function ByMetierPieChart({ data }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useChartInstance(
    canvasRef,
    () => ({
      type: 'doughnut',
      data: {
        labels: data.map((d) => d.metier ?? ''),
        datasets: [{
          data: data.map((d) => d.count),
          backgroundColor: data.map((_, i) => CATEGORY_COLORS[i % CATEGORY_COLORS.length]),
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { position: 'bottom', labels: { color: '#9ca3af', font: { size: 10 }, boxWidth: 8 } } },
      },
    }),
    [data],
  )

  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Répartition des interventions par métier</strong>
      <div style={{ position: 'relative', width: '100%', height: 220, marginTop: 8 }}>
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
