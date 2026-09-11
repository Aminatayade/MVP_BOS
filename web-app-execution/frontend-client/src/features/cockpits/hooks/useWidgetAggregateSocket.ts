/**
 * FILE: features/cockpits/hooks/useWidgetAggregateSocket.ts
 * DESCRIPTION: Abonnement WebSocket au recalcul agrégé d'UN widget de cockpit
 *              (/api/bim/ws/widget-aggregate, api-gateway) -- remplace le
 *              polling REST toutes les 30s de WidgetBlock.tsx. Le serveur ne
 *              réimplémente aucune agrégation : chaque message poussé est la
 *              réponse d'un rappel interne au MÊME endpoint REST que
 *              WidgetBlock.tsx utilisait déjà (voir api-gateway/widget_aggregate.py),
 *              donc la forme des payloads reçus ici est volontairement proche
 *              de la réponse REST correspondante, jamais un format inventé.
 *
 * Reconnexion avec backoff exponentiel (1s, 2s, 4s, ..., plafonné à 30s) sur
 * `onclose`/`onerror` -- corrige explicitement l'absence totale de
 * reconnexion du hook existant useEquipmentTelemetry.ts (une popup de
 * télémétrie qu'on laisse ouverte plusieurs minutes cesse silencieusement de
 * se mettre à jour dès la moindre coupure réseau ; un cockpit affiché en
 * continu sur un écran ne doit pas hériter de cette même lacune).
 */
import { useEffect, useRef } from 'react'
import keycloak from '../../../services/keycloak'

export type WidgetAggregatePayload =
  | { type: 'indicateur'; value: number; unit: string }
  | { type: 'barres' | 'camembert'; building: true; bars: { label: string; value: number }[]; unit: string }
  | { type: 'barres' | 'camembert'; building: false; entries: { code: string; value: number; unit: string }[] }
  | { type: 'courbe'; datasets: { code: string; points: { ts: string; value: number }[] }[] }
  | { type: 'semaine_type'; points: { ts: string; value: number }[] }

export type WidgetAggregateParams = {
  widgetType: string
  spaceIds: string[]
  spaceLevel: string
  metric: string
  calcul: string
  dataTypes: string[]
  bucketSeconds: number
  startMs?: number
}

const MAX_BACKOFF_MS = 30_000

function websocketUrl(p: WidgetAggregateParams): string {
  const gatewayUrl = (import.meta.env.VITE_API_GATEWAY_URL as string) ?? ''
  const wsBase = gatewayUrl.replace(/^http/, 'ws')
  const query = new URLSearchParams({
    token: keycloak.token ?? '',
    widget_type: p.widgetType,
    space_ids: p.spaceIds.join(','),
    space_level: p.spaceLevel,
    metric: p.metric,
    calcul: p.calcul,
    data_types: p.dataTypes.join(','),
    bucket_seconds: String(p.bucketSeconds),
    ...(p.startMs ? { start_ms: String(p.startMs) } : {}),
  })
  return `${wsBase}/api/bim/ws/widget-aggregate?${query.toString()}`
}

/**
 * `onPayload` passe par une ref pour ne pas ré-ouvrir la connexion à chaque
 * rendu du parent (un callback inline y est recréé à chaque fois) -- même
 * motif que useEquipmentLivePoint dans useEquipmentTelemetry.ts.
 */
export function useWidgetAggregateSocket(
  params: WidgetAggregateParams | null,
  onPayload: (payload: WidgetAggregatePayload) => void,
): void {
  const onPayloadRef = useRef(onPayload)
  onPayloadRef.current = onPayload

  const key = params
    ? [
        params.widgetType,
        params.spaceIds.join(','),
        params.spaceLevel,
        params.metric,
        params.calcul,
        params.dataTypes.join(','),
        params.bucketSeconds,
        params.startMs ?? '',
      ].join('|')
    : null

  useEffect(() => {
    if (!params) return

    let cancelled = false
    let ws: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let backoffMs = 1000

    const connect = () => {
      if (cancelled) return
      ws = new WebSocket(websocketUrl(params))
      ws.onopen = () => {
        backoffMs = 1000
      }
      ws.onmessage = (event) => {
        try {
          onPayloadRef.current(JSON.parse(event.data))
        } catch {
          // message malformé -- ignoré, pas de quoi interrompre le flux
        }
      }
      ws.onclose = () => {
        if (cancelled) return
        retryTimer = setTimeout(connect, backoffMs)
        backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS)
      }
      ws.onerror = () => {
        ws?.close()
      }
    }

    connect()

    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
      ws?.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
}
