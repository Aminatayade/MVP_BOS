/**
 * FILE: features/energy-consumption/components/PreviousMonthKpiCard.tsx
 * DESCRIPTION: "Mois n-1" (kWh) + variation % vs le mois actuel -- toujours
 *              le mois calendaire précédent (offset - 1), pas une
 *              comparaison optionnelle comme le cockpit Occupancy. Couleur
 *              rouge/verte selon le signe -- seul endroit de ce cockpit où
 *              les tokens CSS var(--danger)/var(--success) sont utilisables
 *              tel quel (DOM classique, pas un canvas Chart.js).
 */
type Props = {
  kwh: number | null
  pct: number | null
  monthLabel: string
}

export default function PreviousMonthKpiCard({ kwh, pct, monthLabel }: Props) {
  const color = pct === null ? 'var(--text-h)' : pct > 0 ? 'var(--danger)' : 'var(--success)'
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Mois n-1</strong>
      <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--text-h)', marginTop: 8 }}>
        {kwh === null ? '—' : kwh.toLocaleString('fr-FR')} <span style={{ fontSize: 16, fontWeight: 400, color: 'var(--text)' }}>kWh</span>
      </div>
      <p className="panel-hint" style={{ marginTop: 4 }}>{monthLabel}</p>
      {pct !== null && (
        <p style={{ marginTop: 4, fontSize: 13, fontWeight: 600, color }}>
          {pct > 0 ? '+' : ''}{pct}% vs n-1
        </p>
      )}
    </div>
  )
}
