/**
 * FILE: features/occupancy/components/ThresholdCountsCard.tsx
 * DESCRIPTION: Widget (notes client) -- nombre d'espaces SOUS-utilisés
 *              (occupation < seuil bas) et SATURÉS (occupation > seuil haut),
 *              pour l'occupation ET pour le taux de réservation. L'entre-deux
 *              n'est pas compté (ni sous-utilisé ni saturé).
 */
type Props = {
  seuilBas: number
  seuilHaut: number
  occupancyBelow: number
  occupancyAbove: number
  reservationBelow: number
  reservationAbove: number
}

const rowStyle = { display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '4px 0' }

export default function ThresholdCountsCard({ seuilBas, seuilHaut, occupancyBelow, occupancyAbove, reservationBelow, reservationAbove }: Props) {
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Espaces par rapport aux seuils ({seuilBas}% — {seuilHaut}%)</strong>
      <div style={{ marginTop: 10 }}>
        <div style={rowStyle}><span>Occupation &lt; {seuilBas}% (sous-utilisé)</span><strong>{occupancyBelow}</strong></div>
        <div style={rowStyle}><span>Occupation &gt; {seuilHaut}% (saturé)</span><strong>{occupancyAbove}</strong></div>
        <div style={rowStyle}><span>Réservation &lt; {seuilBas}%</span><strong>{reservationBelow}</strong></div>
        <div style={rowStyle}><span>Réservation &gt; {seuilHaut}%</span><strong>{reservationAbove}</strong></div>
      </div>
    </div>
  )
}
