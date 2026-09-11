/**
 * FILE: features/bim/components/TypologyPanel.tsx
 * DESCRIPTION: Barre latérale persistante listant les groupes de pièces par
 *              typologie (bureau, réunion, résidentiel... déjà créés à
 *              l'ingestion via HAS_GROUP/HAS_MEMBER, voir
 *              bim_graph_ingestion_service.py) -- inspiré du panneau
 *              "Contexte de pièces > Typologies" de SpinalCore, mais en
 *              colonne fixe à côté du graphe/3D (Mode Duo) plutôt qu'en
 *              popup flottante qui se superposait au graphe (retour
 *              utilisateur explicite).
 *
 * La liste de typologies vient du graphe déjà chargé (BimExplorer.tsx filtre
 * `visualElements` sur type==="Typology") -- pas de nouvel appel réseau pour
 * la lister, seulement pour le détail d'UNE typologie dépliée (pièces
 * membres + température moyenne, via les routes déjà utilisées par
 * RoomTelemetryPopup/le suivi de surchauffe).
 */
import { useState } from 'react'
import { getLocationReadings, getNodeChildren, truncateLabel } from '../services/arcadeApi'
import { parseArcadeNode } from '../utils/graphUtils'

type Props = {
  typologies: { id: string; label: string }[]
  onColorRooms?: (roomIds: string[], color: string) => void
}

type RoomRef = { id: string; label: string }
type TypologyDetail = { rooms: RoomRef[]; temperature: number | null }
type DetailState = TypologyDetail | 'loading' | 'error'

// Couleur par défaut DIFFÉRENTE par typologie (pas de l'orange uniforme pour
// tout le monde) -- purement indicative, l'utilisateur peut la changer via
// le sélecteur de couleur. Palette cyclique si plus de typologies que de
// couleurs.
const DEFAULT_PALETTE = ['#3b82f6', '#22c55e', '#f59e0b', '#ec4899', '#a855f7', '#14b8a6', '#ef4444', '#eab308']

export default function TypologyPanel({ typologies, onColorRooms }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [details, setDetails] = useState<Record<string, DetailState>>({})
  const [coloredId, setColoredId] = useState<string | null>(null)
  const [colors, setColors] = useState<Record<string, string>>({})

  const colorFor = (typologyId: string, index: number) =>
    colors[typologyId] ?? DEFAULT_PALETTE[index % DEFAULT_PALETTE.length]

  const loadDetail = async (typologyId: string) => {
    if (details[typologyId]) return
    setDetails((prev) => ({ ...prev, [typologyId]: 'loading' }))
    try {
      const rows = await getNodeChildren(typologyId)
      const rooms: RoomRef[] = []
      for (const row of rows) {
        const neighbors = (row as Record<string, unknown>).neighbors
        if (!Array.isArray(neighbors)) continue
        for (const neighbor of neighbors) {
          const parsed = parseArcadeNode(neighbor)
          if (parsed && (parsed.type === 'Room' || parsed.type === 'Space')) {
            rooms.push({ id: parsed.id, label: parsed.label })
          }
        }
      }

      let temperature: number | null = null
      if (rooms.length > 0) {
        try {
          const readings = await getLocationReadings(rooms.map((r) => r.id).join(','), 'temperature')
          const latest = readings[readings.length - 1]
          if (latest) temperature = latest.value
        } catch {
          // Pas de température pour ce groupe (ex: sanitaires) -- pas une erreur.
        }
      }
      setDetails((prev) => ({ ...prev, [typologyId]: { rooms, temperature } }))
    } catch {
      setDetails((prev) => ({ ...prev, [typologyId]: 'error' }))
    }
  }

  const toggle = (typologyId: string) => {
    if (expandedId === typologyId) {
      setExpandedId(null)
      return
    }
    setExpandedId(typologyId)
    loadDetail(typologyId)
  }

  const toggleColor = (typologyId: string, index: number) => {
    const detail = details[typologyId]
    if (!detail || detail === 'loading' || detail === 'error') return
    if (coloredId === typologyId) {
      onColorRooms?.([], '')
      setColoredId(null)
    } else {
      onColorRooms?.(
        detail.rooms.map((r) => r.id),
        colorFor(typologyId, index),
      )
      setColoredId(typologyId)
    }
  }

  const changeColor = (typologyId: string, index: number, color: string) => {
    setColors((prev) => ({ ...prev, [typologyId]: color }))
    // La coloration 3D déjà active pour CETTE typologie doit refléter le
    // nouveau choix immédiatement, pas seulement au prochain clic sur
    // "Coloriser en 3D".
    if (coloredId === typologyId) {
      const detail = details[typologyId]
      if (detail && detail !== 'loading' && detail !== 'error') {
        onColorRooms?.(
          detail.rooms.map((r) => r.id),
          color,
        )
      }
    }
  }

  return (
    <div
      style={{
        background: '#1f2937',
        border: '1px solid #334155',
        borderRadius: 10,
        padding: 12,
        color: '#e2e8f0',
        fontSize: 12.5,
        height: '100%',
        boxSizing: 'border-box',
        overflowY: 'auto',
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 2 }}>Contexte de pièces</div>
      <div style={{ color: '#94a3b8', fontSize: 11, marginBottom: 10 }}>Typologies</div>

      {typologies.length === 0 && (
        <p className="equipment-popup__muted">Dépliez un étage dans le graphe pour voir ses typologies.</p>
      )}

      {typologies.map((typology, index) => {
        const detail = details[typology.id]
        const isOpen = expandedId === typology.id
        const color = colorFor(typology.id, index)
        return (
          <div key={typology.id} style={{ marginBottom: 6 }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                background: '#111827',
                border: '1px solid #334155',
                borderRadius: 6,
                padding: '6px 8px 6px 10px',
              }}
            >
              {/* Pastille + sélecteur natif superposés : le natif est
                  transparent et couvre la pastille, un clic ouvre donc le
                  color picker du navigateur sans bouton dédié. */}
              <span style={{ position: 'relative', width: 16, height: 16, flexShrink: 0 }}>
                <span
                  style={{
                    position: 'absolute',
                    inset: 0,
                    borderRadius: 4,
                    background: color,
                    border: '1px solid rgba(255,255,255,0.3)',
                    pointerEvents: 'none',
                  }}
                />
                <input
                  type="color"
                  value={color}
                  onChange={(event) => changeColor(typology.id, index, event.target.value)}
                  title="Changer la couleur"
                  style={{ position: 'absolute', inset: 0, opacity: 0, cursor: 'pointer', border: 'none', padding: 0 }}
                />
              </span>
              <button
                type="button"
                onClick={() => toggle(typology.id)}
                style={{
                  flex: 1,
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  background: 'none',
                  border: 'none',
                  color: '#e2e8f0',
                  cursor: 'pointer',
                  fontSize: 12.5,
                  padding: 0,
                }}
              >
                <span title={typology.label}>{truncateLabel(typology.label, 20)}</span>
                <span style={{ color: '#94a3b8' }}>{isOpen ? '▾' : '▸'}</span>
              </button>
            </div>

            {isOpen && (
              <div style={{ padding: '6px 10px', fontSize: 11.5 }}>
                {detail === 'loading' && <span className="equipment-popup__muted">Chargement…</span>}
                {detail === 'error' && <span className="equipment-popup__error">Erreur de chargement</span>}
                {detail && detail !== 'loading' && detail !== 'error' && (
                  <>
                    <div className="equipment-popup__metric">
                      <span className="equipment-popup__metric-name">Pièces</span>
                      <span className="equipment-popup__metric-value">{detail.rooms.length}</span>
                    </div>
                    {detail.temperature !== null && (
                      <div className="equipment-popup__metric">
                        <span className="equipment-popup__metric-name">Température moy.</span>
                        <span className="equipment-popup__metric-value">{detail.temperature.toFixed(1)} °C</span>
                      </div>
                    )}
                    {onColorRooms && detail.rooms.length > 0 && (
                      <button
                        type="button"
                        onClick={() => toggleColor(typology.id, index)}
                        style={{
                          marginTop: 6,
                          width: '100%',
                          background: coloredId === typology.id ? '#334155' : color,
                          color: coloredId === typology.id ? '#e2e8f0' : '#1a202c',
                          border: 'none',
                          borderRadius: 6,
                          padding: '6px 8px',
                          cursor: 'pointer',
                          fontSize: 12,
                          fontWeight: 600,
                        }}
                      >
                        {coloredId === typology.id ? 'Retirer la coloration' : 'Coloriser en 3D'}
                      </button>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
