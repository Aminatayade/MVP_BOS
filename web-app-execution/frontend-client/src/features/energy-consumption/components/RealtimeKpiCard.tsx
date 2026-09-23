/**
 * FILE: features/energy-consumption/components/RealtimeKpiCard.tsx
 * DESCRIPTION: "Valeur temps réel" (kW) -- toujours le dernier relevé connu,
 *              indépendant du mois navigué par les filtres (voir
 *              api_server.py: /graph/energy-consumption-dashboard).
 */
type Props = {
  kw: number
}

export default function RealtimeKpiCard({ kw }: Props) {
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Valeur temps réel</strong>
      <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--text-h)', marginTop: 8 }}>
        {kw} <span style={{ fontSize: 16, fontWeight: 400, color: 'var(--text)' }}>kW</span>
      </div>
    </div>
  )
}
