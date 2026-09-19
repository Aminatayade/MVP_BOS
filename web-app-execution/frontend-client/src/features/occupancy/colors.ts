/**
 * FILE: features/occupancy/colors.ts
 * DESCRIPTION: Chart.js dessine sur un <canvas>, pas dans le DOM -- il ne
 *              peut donc pas résoudre `var(--accent)` etc. comme le ferait
 *              une règle CSS. Ces constantes DOIVENT rester synchronisées
 *              avec les tokens de src/index.css (mêmes valeurs, pas une
 *              nouvelle palette) -- même motif que features/maintenance/colors.ts.
 */
export const ACCENT_COLOR = '#0d9488' // période principale (barres, "occupé")
export const COMPARE_COLOR = '#fbbf24' // seconde période superposée ("Comparer 2 périodes")
export const FREE_COLOR = '#e5e4e7' // "libre" dans le donut (= --border)
