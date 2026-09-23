/**
 * FILE: features/energy-consumption/colors.ts
 * DESCRIPTION: Chart.js dessine sur un <canvas>, pas dans le DOM -- il ne
 *              peut donc pas résoudre `var(--danger)` etc. comme le ferait
 *              une règle CSS. Les 6 couleurs de sous-catégorie sont calées
 *              sur la maquette fournie par le client (barres empilées
 *              Éclairage/Stores/MCR-CVC/IT/Cuisine/Ascenseurs).
 */
export const SUBCATEGORY_COLORS: Record<string, string> = {
  eclairage: '#f59e0b',
  stores: '#a855f7',
  cvc: '#3b82f6',
  it: '#22c55e',
  cuisine: '#ef4444',
  ascenseurs: '#6b7280',
}

export const SUBCATEGORY_LABELS: Record<string, string> = {
  eclairage: 'Éclairage',
  stores: 'Stores',
  cvc: 'MCR / CVC',
  it: 'IT (datacenter, réseau)',
  cuisine: 'Cuisine et restaurant',
  ascenseurs: 'Ascenseurs',
}

// Synchronisées avec src/index.css (--danger / --success) -- utilisables
// tel quel car PreviousMonthKpiCard est du DOM classique, pas un canvas.
export const DANGER_COLOR = '#d64545'
export const SUCCESS_COLOR = '#227a55'
