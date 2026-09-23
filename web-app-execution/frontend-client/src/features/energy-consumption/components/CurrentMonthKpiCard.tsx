/**
 * FILE: features/energy-consumption/components/CurrentMonthKpiCard.tsx
 * DESCRIPTION: "Mois actuel" (kWh) -- total sur le mois affiché par les
 *              filtres (offset de navigation), tous jours confondus.
 */
type Props = {
  kwh: number
  monthLabel: string
}

export default function CurrentMonthKpiCard({ kwh, monthLabel }: Props) {
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Mois actuel</strong>
      <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--text-h)', marginTop: 8 }}>
        {kwh.toLocaleString('fr-FR')} <span style={{ fontSize: 16, fontWeight: 400, color: 'var(--text)' }}>kWh</span>
      </div>
      <p className="panel-hint" style={{ marginTop: 4 }}>{monthLabel}</p>
    </div>
  )
}
