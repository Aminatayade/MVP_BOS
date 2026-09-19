/**
 * FILE: features/occupancy/components/OccupancyDonutChart.tsx
 * DESCRIPTION: Widget 2 -- % d'occupation global sur la plage horaire et la
 *              période considérées, détail en nombre absolu en légende
 *              (ex. "1240 occupés / 760 libres").
 */
import { useRef } from 'react'
import { useChartInstance } from '../../cockpits/utils/useChartInstance'
import { ACCENT_COLOR, FREE_COLOR } from '../colors'

type Props = {
  occupiedPct: number
  occupiedCount: number
  freeCount: number
}

export default function OccupancyDonutChart({ occupiedPct, occupiedCount, freeCount }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useChartInstance(
    canvasRef,
    () => ({
      type: 'doughnut',
      data: {
        labels: [`${occupiedCount} occupés`, `${freeCount} libres`],
        datasets: [{ data: [occupiedCount, freeCount], backgroundColor: [ACCENT_COLOR, FREE_COLOR] }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { position: 'bottom', labels: { color: '#9ca3af', font: { size: 10 }, boxWidth: 8 } } },
      },
    }),
    [occupiedCount, freeCount],
  )

  return (
    <div className="panel" style={{ position: 'relative' }}>
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Occupation globale</strong>
      <div style={{ position: 'relative', width: '100%', height: 220, marginTop: 8 }}>
        <canvas ref={canvasRef} />
        <div
          style={{
            position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
            pointerEvents: 'none', flexDirection: 'column', marginBottom: 40,
          }}
        >
          <span style={{ fontSize: 26, fontWeight: 700, color: 'var(--text-h)' }}>{occupiedPct}%</span>
        </div>
      </div>
    </div>
  )
}
