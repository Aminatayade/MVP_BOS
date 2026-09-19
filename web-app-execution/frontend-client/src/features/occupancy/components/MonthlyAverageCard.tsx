/**
 * FILE: features/occupancy/components/MonthlyAverageCard.tsx
 * DESCRIPTION: Widget (notes client) -- occupation moyenne mensuelle depuis
 *              le début de l'année, indépendante du filtre "Période".
 */
type Props = {
  pct: number
}

export default function MonthlyAverageCard({ pct }: Props) {
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Occupation moyenne mensuelle</strong>
      <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--text-h)', marginTop: 8 }}>{pct}%</div>
      <p className="panel-hint" style={{ marginTop: 4 }}>Depuis le début de l'année</p>
    </div>
  )
}
