/**
 * FILE: features/bim/hooks/useEquipmentTelemetry.ts
 * DESCRIPTION: Valeur courante + historique d'un équipement, tenus à jour en
 *              temps réel via WebSocket -- remplace le polling HTTP toutes
 *              les 10s utilisé précédemment par les deux popups de
 *              télémétrie (EquipmentTelemetryPopup.tsx pour le graphe,
 *              BimModelViewer.tsx pour la maquette 3D). Un seul hook
 *              partagé : la logique (fetch initial + flux WS + fusion des
 *              points entrants dans l'état local) est identique dans les
 *              deux popups, seul l'affichage diffère.
 *
 * Séquence : un fetch REST initial (getEquipmentSummary) donne la forme
 * complète de chaque métrique (unité, value_labels, historique de départ),
 * PUIS une connexion WebSocket vers la gateway (/api/bim/ws/equipment/:id)
 * pousse chaque nouvelle mesure NATS dès qu'elle est publiée -- le serveur
 * ne fait que relayer le flux NATS existant (voir api-gateway/nats_bridge.py),
 * aucun polling côté serveur non plus.
 *
 * `subscribeToEquipmentLive`/`useEquipmentLivePoint` exposent le bout WS seul
 * (sans état "metrics" intégré), pour RoomTelemetryPopup.tsx : une pièce
 * affiche plusieurs capteurs regroupés PAR MÉTRIQUE (pas par équipement),
 * donc l'état groupé vit dans le composant parent -- chaque capteur n'a
 * besoin que d'un flux de points bruts à lui faire remonter.
 */
import { useEffect, useRef, useState } from 'react'
import keycloak from '../../../services/keycloak'
import { getEquipmentSummary, type EquipmentMetricSummary } from '../services/arcadeApi'

const MAX_HISTORY_POINTS = 50

export type LivePoint = { metric: string; value: number; timestamp: string; quality?: string }

function websocketUrl(globalId: string): string {
  const gatewayUrl = (import.meta.env.VITE_API_GATEWAY_URL as string) ?? ''
  const wsBase = gatewayUrl.replace(/^http/, 'ws')
  return `${wsBase}/api/bim/ws/equipment/${globalId}?token=${keycloak.token ?? ''}`
}

/** Ouvre le WebSocket d'un équipement et retourne une fonction de nettoyage. */
export function subscribeToEquipmentLive(equipmentId: string, onPoint: (point: LivePoint) => void): () => void {
  const ws = new WebSocket(websocketUrl(equipmentId))
  ws.onmessage = (event) => {
    try {
      onPoint(JSON.parse(event.data))
    } catch {
      // message malformé -- ignoré, pas de quoi interrompre le flux
    }
  }
  return () => ws.close()
}

/**
 * Abonnement WS "nu" à un équipement, sans état interne -- l'appelant décide
 * quoi faire de chaque point (voir RoomTelemetryPopup.tsx). `onPoint` passe
 * par une ref pour ne pas ré-ouvrir la connexion à chaque rendu du parent
 * (un callback inline y est recréé à chaque fois).
 */
export function useEquipmentLivePoint(equipmentId: string | null | undefined, onPoint: (point: LivePoint) => void): void {
  const onPointRef = useRef(onPoint)
  onPointRef.current = onPoint

  useEffect(() => {
    if (!equipmentId) return
    return subscribeToEquipmentLive(equipmentId, (point) => onPointRef.current(point))
  }, [equipmentId])
}

export function useEquipmentTelemetry(globalId: string | null | undefined) {
  const [metrics, setMetrics] = useState<EquipmentMetricSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setMetrics(null)
    setError(null)
    if (!globalId) return

    let cancelled = false
    let unsubscribe: (() => void) | null = null

    getEquipmentSummary(globalId)
      .then((result) => {
        if (cancelled) return
        setMetrics(result)
        // Rien à écouter en direct pour un équipement sans métrique simulée
        // (ex: mobilier) -- pas la peine d'ouvrir un WebSocket pour ça.
        if (result.length === 0) return

        unsubscribe = subscribeToEquipmentLive(globalId, (point) => {
          if (cancelled) return
          setMetrics((prev) => {
            if (!prev) return prev
            return prev.map((m) => {
              if (m.metric !== point.metric) return m
              const history = [...m.history, { value: point.value, timestamp: point.timestamp }].slice(
                -MAX_HISTORY_POINTS,
              )
              return {
                ...m,
                current: { value: point.value, timestamp: point.timestamp, quality: point.quality },
                history,
              }
            })
          })
        })
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Erreur inattendue')
      })

    return () => {
      cancelled = true
      unsubscribe?.()
    }
  }, [globalId])

  return { metrics, error }
}
