import { useEffect, useState } from 'react'
import { syncGrafanaZone } from '../services/arcadeApi'

type Props = {
  zone: string
  dashboardUrl: string
}

// Onglet "Supervision" : recale d'abord le dashboard Grafana sur LA zone
// affichée (variables Pièce/Étage, métriques de cette maquette, paramètre
// `zone` des requêtes), PUIS charge l'iframe -- sinon Grafana montrait les
// pièces de la dernière zone ingérée, pas de la zone sélectionnée. Un échec
// de synchro n'empêche pas d'afficher le dashboard (dernier état connu).
export default function SupervisionFrame({ zone, dashboardUrl }: Props) {
  const [readyFor, setReadyFor] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setReadyFor(null)
    syncGrafanaZone(zone)
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setReadyFor(zone)
      })
    return () => {
      cancelled = true
    }
  }, [zone])

  if (readyFor !== zone) {
    return <p className="muted" style={{ padding: 16, fontSize: 13 }}>Synchronisation de Grafana avec « {zone} »…</p>
  }

  return (
    <iframe
      key={`${dashboardUrl}|${zone}`}
      src={dashboardUrl}
      title="Supervision"
      style={{ width: '100%', height: '100%', border: 'none', borderRadius: 8, background: '#0b1120' }}
    />
  )
}
