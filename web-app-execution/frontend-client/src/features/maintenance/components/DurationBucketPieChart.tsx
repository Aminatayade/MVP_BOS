/**
 * FILE: features/maintenance/components/DurationBucketPieChart.tsx
 * DESCRIPTION: Widget 5 -- répartition par tranche de durée de traitement
 *              (<4h / 4-24h / 1-3j / >3j).
 */
import { useRef } from 'react'
import { useChartInstance } from '../../cockpits/utils/useChartInstance'
import type { CountEntry } from '../services/maintenanceApi'
import { SUCCESS_COLOR, WARNING_COLOR, ACCENT_COLOR, DANGER_COLOR } from '../colors'

type Props = {
  data: CountEntry[]
}

// Une couleur par tranche, du plus rapide (vert) au plus lent (rouge) --
// réutilise les tokens sémantiques existants, cohérent avec le sens de
// lecture "plus c'est long, plus c'est grave" déjà utilisé ailleurs (KPI SLA).
const BUCKET_COLORS = [SUCCESS_COLOR, ACCENT_COLOR, WARNING_COLOR, DANGER_COLOR]

export default function DurationBucketPieChart({ data }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useChartInstance(
    canvasRef,
    () => ({
      type: 'doughnut',
      data: {
        labels: data.map((d) => d.bucket ?? ''),
        datasets: [{
          data: data.map((d) => d.count),
          backgroundColor: data.map((_, i) => BUCKET_COLORS[i % BUCKET_COLORS.length]),
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
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Répartition du temps de traitement</strong>
      <div style={{ position: 'relative', width: '100%', height: 220, marginTop: 8 }}>
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
