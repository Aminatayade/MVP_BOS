/**
 * FILE: features/maintenance/components/ByZoneBarChart.tsx
 * DESCRIPTION: Widget 6 -- interventions par zone/étage, barres horizontales.
 *              `over_threshold` vient du backend (maintenance.py:
 *              _apply_threshold), la MÊME fonction qui alimente le widget 7
 *              (ThresholdAlertsList) -- ce composant ne recalcule jamais
 *              lui-même une comparaison au seuil, il se contente d'afficher
 *              ce que le serveur a déjà décidé, pour que les deux widgets ne
 *              puissent jamais diverger.
 */
import { useRef } from 'react'
import { useChartInstance } from '../../cockpits/utils/useChartInstance'
import type { ZoneEntry } from '../services/maintenanceApi'
import { ACCENT_COLOR, DANGER_COLOR } from '../colors'

type Props = {
  data: ZoneEntry[]
}

export default function ByZoneBarChart({ data }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useChartInstance(
    canvasRef,
    () => ({
      type: 'bar',
      data: {
        labels: data.map((d) => d.zone),
        datasets: [{
          data: data.map((d) => d.count),
          backgroundColor: data.map((d) => (d.over_threshold ? DANGER_COLOR : ACCENT_COLOR)),
          borderRadius: 4,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { color: 'rgba(156,163,175,0.15)' } },
          y: { ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { display: false } },
        },
      },
    }),
    [data],
  )

  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Interventions par zone</strong>
      <div style={{ position: 'relative', width: '100%', height: 220, marginTop: 8 }}>
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
