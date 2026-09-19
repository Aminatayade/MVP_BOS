/**
 * FILE: features/occupancy/components/ReservationRateCard.tsx
 * DESCRIPTION: Widget 3 -- taux de réservation = occupation mesurée /
 *              réservation déclarée, détecte le no-show.
 */
type Props = {
  pct: number | null
}

export default function ReservationRateCard({ pct }: Props) {
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Taux de réservation</strong>
      <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--text-h)', marginTop: 8 }}>
        {pct === null ? '—' : `${pct}%`}
      </div>
      <p className="panel-hint" style={{ marginTop: 4 }}>
        {pct === null ? 'Aucune réservation sur ce périmètre' : 'Occupation mesurée / réservation déclarée'}
      </p>
    </div>
  )
}
