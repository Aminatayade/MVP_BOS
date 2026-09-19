/**
 * FILE: features/maintenance/colors.ts
 * DESCRIPTION: Chart.js dessine sur un <canvas>, pas dans le DOM -- il ne
 *              peut donc pas résoudre `var(--danger)` etc. comme le ferait
 *              une règle CSS. Ces constantes DOIVENT rester synchronisées
 *              avec les tokens `--accent`/`--danger`/`--warning`/`--success`
 *              de src/index.css (mêmes valeurs, pas une nouvelle palette).
 */
export const ACCENT_COLOR = '#0d9488'
export const DANGER_COLOR = '#d64545'
export const WARNING_COLOR = '#a15c07'
export const SUCCESS_COLOR = '#227a55'

// Palette catégorielle pour les camemberts (répartition par métier / par
// tranche de durée) -- même logique que FLOOR_COLORS dans
// features/cockpits/components/WidgetBlock.tsx, propre à ce cockpit.
export const CATEGORY_COLORS = ['#38bdf8', '#a3e635', '#fbbf24', '#f472b6', '#c084fc', '#fb923c']
