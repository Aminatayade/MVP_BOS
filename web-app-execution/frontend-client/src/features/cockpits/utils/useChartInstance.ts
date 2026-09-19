/**
 * FILE: features/cockpits/utils/useChartInstance.ts
 * DESCRIPTION: Motif "créer l'instance Chart.js une fois, puis muter
 *              `.data`/`.options` et appeler update('none') au rafraîchi"
 *              -- extrait de l'ancien ConsumptionWidget.tsx où il était
 *              triplé (barres/donut/courbe). Un seul hook réutilisé par
 *              tous les widgets du constructeur de cockpits.
 *
 * L'enregistrement des modules Chart.js (bar/line/doughnut/tooltip/legend)
 * est fait ici, une seule fois -- Chart.register() est idempotent, mais
 * centraliser évite de le redupliquer dans chaque composant de widget.
 */
import { useEffect, useRef } from 'react'
import {
  ArcElement,
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  type ChartConfiguration,
  DoughnutController,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js'

Chart.register(
  BarController, BarElement, CategoryScale, LinearScale,
  ArcElement, DoughnutController, Legend,
  LineController, LineElement, PointElement,
  Tooltip,
)

/**
 * `buildConfig` renvoie `null` tant que les données ne sont pas prêtes (rien
 * n'est créé/mis à jour dans ce cas). Si `config.type` change par rapport à
 * l'instance existante (Chart.js ne permet pas de le faire à chaud),
 * l'instance est détruite et recréée -- sinon, seules `data`/`options` sont
 * mutées en place puis `update('none')` (sans animation, pour ne pas faire
 * clignoter le graphique à chaque rafraîchi périodique).
 */
export function useChartInstance(
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  buildConfig: () => ChartConfiguration | null,
  deps: React.DependencyList,
) {
  const chartRef = useRef<Chart | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const config = buildConfig()
    if (!canvas || !config) return

    // Détruit et recrée aussi quand le canvas lui-même a changé d'identité
    // (pas seulement le type de graphique) -- le widget démonte/remonte son
    // <canvas> entre l'état "Chargement..." et l'affichage du graphique
    // (voir WidgetBlock.tsx), ce qui produit un TOUT NOUVEL élément DOM à
    // chaque rafraîchissement. Sans cette vérification, l'instance Chart.js
    // existante reste liée à l'ANCIEN canvas (retiré du DOM) et continue
    // d'être mise à jour en silence, tandis que le nouveau canvas affiché à
    // l'écran ne reçoit jamais de graphique -- bug constaté : un widget qui
    // s'affiche bien une première fois redevient vide dès que la métrique ou
    // l'intervalle change (donc à chaque nouveau cycle chargement/affichage).
    if (chartRef.current && (chartRef.current.config.type !== config.type || chartRef.current.canvas !== canvas)) {
      chartRef.current.destroy()
      chartRef.current = null
    }

    if (!chartRef.current) {
      chartRef.current = new Chart(canvas, config)
      return
    }

    chartRef.current.data = config.data
    if (config.options) chartRef.current.options = config.options
    chartRef.current.update('none')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    return () => {
      chartRef.current?.destroy()
      chartRef.current = null
    }
  }, [])
}
