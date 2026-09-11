/**
 * FILE: features/bim/components/EquipmentHistoryChart.tsx
 * DESCRIPTION: Mini graphique d'évolution d'une métrique (quelques heures
 *              d'historique), affiché derrière le bouton "Détails" des
 *              popups de télémétrie -- alternative visuelle à
 *              EquipmentHistoryList (liste texte) pour la MÊME donnée déjà
 *              en mémoire (metric.history, tenue à jour en direct par
 *              WebSocket) : aucune requête réseau supplémentaire ici.
 *
 * Chart.js plutôt qu'un panel Grafana embarqué : la donnée est déjà côté
 * client, un iframe Grafana (auth cross-origin, rechargement complet)
 * serait disproportionné pour une mini-vue dans une popup flottante --
 * Grafana reste l'outil du cockpit dashboard complet, pas de ce micro-usage.
 * Import ciblé (pas 'chart.js/auto') pour ne charger que les briques utiles
 * à un simple graphique en ligne.
 */
import { useEffect, useRef } from 'react'
import {
  CategoryScale,
  Chart,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js'
import { formatHistoryTimestamp, formatValue, type EquipmentMetricSummary } from '../services/arcadeApi'

Chart.register(CategoryScale, LinearScale, LineController, LineElement, PointElement, Tooltip)

type Props = {
  metric: EquipmentMetricSummary
  height?: number
}

export default function EquipmentHistoryChart({ metric, height = 110 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const chartRef = useRef<Chart | null>(null)

  // Un seul Chart créé au montage, puis mis à jour en place (`update('none')`,
  // sans animation) à chaque nouveau point WebSocket -- recréer l'instance à
  // chaque rendu ferait clignoter le graphique en direct.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const labels = metric.history.map((point) => formatHistoryTimestamp(point.timestamp))
    const values = metric.history.map((point) => point.value)

    const chart = chartRef.current
    if (!chart) {
      chartRef.current = new Chart(canvas, {
        type: 'line',
        data: {
          labels,
          datasets: [
            {
              data: values,
              borderColor: '#38bdf8',
              backgroundColor: 'rgba(56, 189, 248, 0.15)',
              borderWidth: 1.5,
              pointRadius: 0,
              pointHoverRadius: 3,
              fill: true,
              tension: 0.25,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          scales: {
            x: {
              ticks: { color: '#64748b', maxTicksLimit: 4, font: { size: 9 } },
              grid: { display: false },
            },
            y: {
              ticks: {
                color: '#64748b',
                font: { size: 9 },
                callback: (value) => formatValue(Number(value), metric),
              },
              grid: { color: '#334155' },
            },
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => formatValue(Number(ctx.parsed.y), metric),
              },
            },
          },
        },
      })
      return
    }

    chart.data.labels = labels
    chart.data.datasets[0].data = values
    chart.update('none')
  }, [metric])

  // Destruction explicite au démontage (toggle "Liste"/re-fermeture de la
  // popup) -- Chart.js ne libère pas tout seul le canvas sinon.
  useEffect(() => {
    return () => {
      chartRef.current?.destroy()
      chartRef.current = null
    }
  }, [])

  // Chart.js (responsive: true) calcule la taille du canvas à partir de
  // son PARENT -- un style inline sur le <canvas> lui-même ne suffit pas et
  // laissait le canvas grandir sans limite (popup démesurée, graphique
  // quasi vide observés en test). Le parent doit avoir `position: relative`
  // + une hauteur explicite pour que le calcul soit borné.
  return (
    <div style={{ position: 'relative', width: '100%', height }}>
      <canvas ref={canvasRef} />
    </div>
  )
}
