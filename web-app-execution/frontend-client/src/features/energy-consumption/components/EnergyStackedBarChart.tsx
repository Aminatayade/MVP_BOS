/**
 * FILE: features/energy-consumption/components/EnergyStackedBarChart.tsx
 * DESCRIPTION: Barres empilées journalières, un dataset par sous-catégorie
 *              -- même config Chart.js (stacked x/y) que le mode "courbe en
 *              barres empilées" du constructeur générique
 *              (features/cockpits/components/WidgetBlock.tsx, isCourbeBars),
 *              reprise ici telle quelle plutôt que réinventée.
 */
import { useRef } from 'react'
import { useChartInstance } from '../../cockpits/utils/useChartInstance'
import { SUBCATEGORY_COLORS, SUBCATEGORY_LABELS } from '../colors'

type Props = {
  days: string[]
  subcategories: string[]
  series: Record<string, number[]>
}

export default function EnergyStackedBarChart({ days, subcategories, series }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useChartInstance(
    canvasRef,
    () => {
      if (days.length === 0) return null
      return {
        type: 'bar',
        data: {
          labels: days.map((d) => d.slice(8, 10)), // "01", "02"... (jour du mois)
          datasets: subcategories.map((subcat) => ({
            label: SUBCATEGORY_LABELS[subcat] ?? subcat,
            data: series[subcat] ?? days.map(() => 0),
            backgroundColor: SUBCATEGORY_COLORS[subcat] ?? '#9ca3af',
            borderRadius: 2,
          })),
        },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          plugins: {
            legend: { position: 'bottom', labels: { color: '#9ca3af', font: { size: 10 }, boxWidth: 10 } },
            tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.formattedValue} kWh` } },
          },
          scales: {
            x: { stacked: true, ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { display: false } },
            y: { stacked: true, ticks: { color: '#9ca3af', font: { size: 9 } }, grid: { color: 'rgba(156,163,175,0.15)' } },
          },
        },
      }
    },
    [days, subcategories, series],
  )

  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Consommation par jour (kWh)</strong>
      <div style={{ position: 'relative', width: '100%', height: 320, marginTop: 8 }}>
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
