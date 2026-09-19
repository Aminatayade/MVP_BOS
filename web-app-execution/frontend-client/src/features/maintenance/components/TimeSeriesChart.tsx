/**
 * FILE: features/maintenance/components/TimeSeriesChart.tsx
 * DESCRIPTION: Widget 3 -- nombre d'interventions par unité de temps (jour
 *              ou mois selon la période choisie, décidé côté serveur --
 *              voir maintenance.py:get_dashboard).
 */
import { useRef } from 'react'
import { useChartInstance } from '../../cockpits/utils/useChartInstance'
import type { TimeSeriesPoint } from '../services/maintenanceApi'
import { ACCENT_COLOR } from '../colors'

type Props = {
  bucket: string
  points: TimeSeriesPoint[]
}

export default function TimeSeriesChart({ bucket, points }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useChartInstance(
    canvasRef,
    () => ({
      type: 'bar',
      data: {
        labels: points.map((p) => p.label),
        datasets: [{ data: points.map((p) => p.count), backgroundColor: ACCENT_COLOR, borderRadius: 4 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#9ca3af', font: { size: 9 }, maxRotation: 45, minRotation: 0 }, grid: { display: false } },
          y: { ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { color: 'rgba(156,163,175,0.15)' } },
        },
      },
    }),
    [points],
  )

  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Nombre d'interventions</strong>
      <p className="panel-hint" style={{ marginTop: 2, marginBottom: 8 }}>Par {bucket === 'mois' ? 'mois' : 'jour'}</p>
      <div style={{ position: 'relative', width: '100%', height: 220 }}>
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
