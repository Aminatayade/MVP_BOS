/**
 * FILE: features/maintenance/components/ThresholdAlertsList.tsx
 * DESCRIPTION: Widget 7 -- liste nommée des métiers/zones dépassant le
 *              seuil configurable (maintenance.py: _apply_threshold, MÊME
 *              fonction que ByZoneBarChart -- voir sa description). Sévérité
 *              visuelle : rouge foncé (--danger) si très au-dessus du seuil,
 *              ambre (--warning) si juste au-dessus -- couleurs sémantiques
 *              déjà existantes dans l'app, pas de nouvelle palette.
 */
import type { AlertEntry } from '../services/maintenanceApi'

type Props = {
  alerts: AlertEntry[]
  seuilAlerte: number
}

const TYPE_LABEL: Record<AlertEntry['type'], string> = {
  zone: 'Étage',
  metier: 'Métier',
}

export default function ThresholdAlertsList({ alerts, seuilAlerte }: Props) {
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>
        Seuils dépassés (&gt; {seuilAlerte} interventions)
      </strong>

      {alerts.length === 0 && (
        <p className="panel-hint" style={{ marginTop: 8 }}>Aucun dépassement sur ce périmètre.</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
        {alerts.map((alert) => {
          const isDanger = alert.severity === 'danger'
          return (
            <div
              key={`${alert.type}-${alert.label}`}
              style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '8px 12px', borderRadius: 8,
                background: isDanger ? 'var(--danger-bg)' : 'var(--warning-bg)',
                color: isDanger ? 'var(--danger)' : 'var(--warning)',
                fontSize: 13, fontWeight: 600,
              }}
            >
              <span>{TYPE_LABEL[alert.type]} {alert.label}</span>
              <span>{alert.count}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
