/**
 * FILE: features/maintenance/components/KpiTicketsCard.tsx
 * DESCRIPTION: Widget 1 -- nombre total d'interventions sur la période/les
 *              filtres sélectionnés.
 */
type Props = {
  total: number
}

export default function KpiTicketsCard({ total }: Props) {
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Total interventions</strong>
      <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--text-h)', marginTop: 8 }}>{total}</div>
      <p className="panel-hint" style={{ marginTop: 4 }}>Sur la période sélectionnée</p>
    </div>
  )
}
