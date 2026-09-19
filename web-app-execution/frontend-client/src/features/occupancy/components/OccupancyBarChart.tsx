/**
 * FILE: features/occupancy/components/OccupancyBarChart.tsx
 * DESCRIPTION: Widget 1 -- taux d'occupation (%) par jour. Une seconde série
 *              (couleur distincte) s'ajoute quand "Comparer 2 périodes" est
 *              actif (voir OccupancyApp.tsx).
 */
import { useRef } from 'react'
import { useChartInstance } from '../../cockpits/utils/useChartInstance'
import { ACCENT_COLOR, COMPARE_COLOR } from '../colors'

type Props = {
  buckets: string[]
  seriesA: number[]
  seriesB: number[] | null
}

export default function OccupancyBarChart({ buckets, seriesA, seriesB }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useChartInstance(
    canvasRef,
    () => ({
      type: 'bar',
      data: {
        labels: buckets,
        datasets: [
          { label: 'Période sélectionnée', data: seriesA, backgroundColor: ACCENT_COLOR, borderRadius: 4 },
          ...(seriesB ? [{ label: 'Période comparée', data: seriesB, backgroundColor: COMPARE_COLOR, borderRadius: 4 }] : []),
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: !!seriesB, labels: { color: '#9ca3af', font: { size: 10 } } },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.formattedValue}%` } },
        },
        scales: {
          x: { ticks: { color: '#9ca3af', font: { size: 9 }, maxRotation: 45, minRotation: 0 }, grid: { display: false } },
          y: { min: 0, max: 100, ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { color: 'rgba(156,163,175,0.15)' } },
        },
      },
    }),
    [buckets, seriesA, seriesB],
  )

  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Taux d'occupation par jour</strong>
      <div style={{ position: 'relative', width: '100%', height: 240, marginTop: 8 }}>
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
