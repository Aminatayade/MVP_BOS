import { useEffect, useRef, useState } from 'react'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import dagre from 'cytoscape-dagre'
import { getLocationReadings, getNodeChildren, getRoomEquipment } from '../services/arcadeApi'
import type { CytoscapeElement } from '../utils/graphUtils'
import { edgeElement, equipmentGroupPlaceholder, nodeElement, nodeGroupPlaceholder, parseArcadeNode } from '../utils/graphUtils'
import EquipmentTelemetryPopup from './EquipmentTelemetryPopup'
import RoomTelemetryPopup from './RoomTelemetryPopup'

cytoscape.use(dagre)

export const DAGRE_LAYOUT = {
  name: 'dagre',
  rankDir: 'LR',
  nodeSep: 14,
  rankSep: 70,
  animate: true,
} as const

const OVERHEAT_THRESHOLD_CELSIUS = 35
const ROOM_TEMPERATURE_REFRESH_MS = 30_000

// Au-delà de ce nombre d'enfants d'un même type sous un nœud déplié
// (typiquement des Room sous un Level), on les replie en un seul nœud
// groupé plutôt que de les afficher tous -- c'est ce qui transforme
// l'affichage en "soleil" illisible sur les étages à beaucoup de pièces.
// Typology est délibérément EXCLU de ce regroupement : TypologyPanel
// (panneau "Contexte de pièces") s'alimente uniquement des nœuds Typology
// réellement présents dans Cytoscape (onTypologiesChange plus bas) -- les
// replier les ferait disparaître du panneau tant que l'utilisateur n'a pas
// cliqué pour déplier, ce qui reproduirait le bug déjà corrigé "rien
// n'apparaît dans Contexte de pièces".
const GROUP_THRESHOLD = 4
const GROUPABLE_TYPE_LABELS: Record<string, string> = {
  Room: 'pièces',
  Space: 'pièces',
}

// Sous-groupage du placeholder "N équipements" (voir la branche
// data.context === 'room-equipment' plus bas) : sans ça, matérialiser le
// groupe dépose TOUS les équipements d'un coup -- jusqu'à plusieurs
// dizaines sur un étage entier -- exactement le "soleil" illisible
// remonté. Regroupé par `category` (cvc/electricity/security/sanitary/
// furniture/controls, déjà présente sur chaque nœud Equipment pour
// l'icône), qui réutilise le mécanisme générique de placeholder
// (nodeGroupPlaceholder / context "node-group") déjà en place pour
// Room/Space.
const EQUIPMENT_CATEGORY_LABELS: Record<string, string> = {
  cvc: 'équipements CVC',
  electricity: 'équipements électriques',
  security: 'équipements sécurité',
  sanitary: 'équipements sanitaires',
  furniture: 'mobilier',
  controls: 'automates/contrôleurs',
}

/**
 * Colore une pièce en rouge (classe `.overheated`, voir le style Cytoscape
 * plus bas) quand la température moyenne de ses équipements atteint le
 * seuil. Best-effort : une pièce sans équipement CVC/température (ou en
 * échec réseau) reste dans son état courant plutôt que de planter le rendu
 * du graphe.
 */
async function refreshRoomTemperature(cy: Core, roomId: string): Promise<void> {
  try {
    const readings = await getLocationReadings(roomId, 'temperature')
    const latest = readings[readings.length - 1]
    const node = cy.getElementById(roomId)
    if (node.empty()) return
    node.toggleClass('overheated', latest !== undefined && latest.value >= OVERHEAT_THRESHOLD_CELSIUS)
  } catch {
    // Pas de télémétrie température pour cette pièce (pas d'équipement CVC
    // rattaché) ou requête en échec -- ne jamais faire planter le graphe
    // pour ça, la pièce reste simplement dans son état de couleur courant.
  }
}

type Props = {
  elements?: CytoscapeElement[]
  onNodeSelect?: (data: Record<string, unknown>) => void
  onPlaceholderClick?: (data: Record<string, unknown>) => void
  className?: string
  height?: string | number
  autoExpandOnClick?: boolean
  // Sélection croisée (Mode Duo, voir BimExplorer.tsx) : un GlobalId
  // sélectionné côté maquette 3D -- sélectionne et recadre le nœud
  // correspondant s'il est déjà présent dans le graphe affiché (pas de
  // dépliage automatique : un nœud pas encore chargé reste simplement non
  // trouvé, cohérent avec le dépliage manuel du reste du graphe).
  focusNodeId?: string | null
  // Panneau "Contexte de pièces" (Mode Duo, voir TypologyPanel.tsx) : les
  // nœuds Typology ajoutés au graphe (dépliage d'un étage) vivent
  // uniquement dans l'instance Cytoscape interne, jamais dans la prop
  // `elements` du parent -- ce callback est le seul moyen pour le parent de
  // savoir "quelles typologies sont actuellement visibles dans le graphe".
  onTypologiesChange?: (typologies: { id: string; label: string }[]) => void
}

function toElementDefinitions(elements: CytoscapeElement[]): ElementDefinition[] {
  return elements.map((element) => {
    if ('source' in element.data || 'target' in element.data) {
      return { group: 'edges', data: element.data }
    }
    return { group: 'nodes', data: element.data }
  })
}

/**
 * Referme le sous-arbre ouvert par le dépliage de `nodeId` -- ré-clic sur un
 * nœud déjà déplié (voir l'appel dans le handler de tap) : bascule
 * ouvrir/fermer, sans toucher au reste du graphe. `successors()` ne suit
 * que les arêtes SORTANTES depuis ce nœud -- exactement les éléments
 * ajoutés par SES propres dépliages, jamais un nœud partagé ajouté par un
 * autre parent (ex: une Room membre d'une Typology déjà dépliée ailleurs).
 * `nodeId` lui-même n'est PAS retiré : il redevient simplement re-dépliable.
 */
function collapseSubtree(
  cy: Core,
  nodeId: string,
  expandedRef: { current: Set<string> },
  groupedChildrenRef: { current: Map<string, CytoscapeElement['data'][]> },
) {
  const node = cy.getElementById(nodeId)
  if (node.empty()) return
  const descendants = node.successors()
  descendants.nodes().forEach((n) => {
    groupedChildrenRef.current.delete(n.id())
    expandedRef.current.delete(n.id())
  })
  descendants.remove()
  expandedRef.current.delete(nodeId)
}

function runLayout(cy: Core) {
  // Les nœuds ajoutés après un clic (dépliage) peuvent se retrouver hors du
  // viewport courant avec le layout LR : on recadre systématiquement une fois
  // le layout terminé, sinon "rien ne se passe" visuellement alors que les
  // nœuds sont bien là, juste hors cadre.
  cy.one('layoutstop', () => cy.fit(cy.elements(), 40))
  // cytoscape-dagre n'a pas de types officiels (déclaré `any` dans
  // types/cytoscape-dagre.d.ts) : les types du cœur cytoscape ne connaissent
  // pas `animationDuration` sur un layout nommé "dagre", d'où le cast.
  cy.layout({ ...DAGRE_LAYOUT, animationDuration: 400 } as cytoscape.LayoutOptions).run()
}

export default function BimGraphViewer({
  elements = [],
  onNodeSelect,
  onPlaceholderClick,
  className,
  height = '600px',
  autoExpandOnClick = true,
  focusNodeId,
  onTypologiesChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cyRef = useRef<Core | null>(null)
  const expandedRef = useRef<Set<string>>(new Set())
  // Contenu réel (déjà résolu par /graph/neighbors) d'un groupe replié
  // Room/Space -- placeholderId -> liste de nœuds à matérialiser au clic.
  const groupedChildrenRef = useRef<Map<string, CytoscapeElement['data'][]>>(new Map())
  const [equipmentPopup, setEquipmentPopup] = useState<
    { nodeId: string; x: number; y: number; label: string } | null
  >(null)
  const [roomPopup, setRoomPopup] = useState<
    { nodeId: string; x: number; y: number; label: string } | null
  >(null)

  // onNodeSelect est typiquement une arrow function inline côté parent (nouvelle
  // référence à CHAQUE rendu). Si ces callbacks étaient dans les dépendances de
  // l'effet ci-dessous, l'appel à onNodeSelect (qui déclenche un setState côté
  // parent) recréait l'instance Cytoscape EN PLEIN MILIEU du fetch async de
  // dépliage : cy.add(...) s'exécutait ensuite sur une instance déjà détruite,
  // sans erreur visible. Les refs cassent ce cycle sans imposer useCallback au parent.
  const onNodeSelectRef = useRef(onNodeSelect)
  onNodeSelectRef.current = onNodeSelect
  const onPlaceholderClickRef = useRef(onPlaceholderClick)
  onPlaceholderClickRef.current = onPlaceholderClick
  const onTypologiesChangeRef = useRef(onTypologiesChange)
  onTypologiesChangeRef.current = onTypologiesChange

  useEffect(() => {
    if (!containerRef.current) return

    let cancelled = false

    const initCy = () => {
      if (cancelled || !containerRef.current) return

      const { clientWidth, clientHeight } = containerRef.current
      if (!clientWidth || !clientHeight) {
        window.setTimeout(initCy, 120)
        return
      }

      if (cyRef.current) {
        cyRef.current.destroy()
        cyRef.current = null
      }

      const cy = cytoscape({
        container: containerRef.current,
        elements: toElementDefinitions(elements),
        minZoom: 0.05,
        maxZoom: 8,
        wheelSensitivity: 0.2,
        userZoomingEnabled: true,
        userPanningEnabled: true,
        boxSelectionEnabled: false,
        autoungrabify: false,
        style: [
          {
            selector: 'node',
            style: {
              label: 'data(label)',
              color: '#e2e8f0',
              'text-halign': 'right',
              'text-valign': 'center',
              'text-margin-x': 6,
              'font-size': 9,
              'font-family': 'Inter, ui-sans-serif, system-ui',
              width: 24,
              height: 24,
              'background-color': '#475569',
              'background-image': 'data(icon)',
              'background-fit': 'cover',
              'background-clip': 'node',
              'border-width': 1.5,
              'border-color': '#64748b',
            },
          },
          {
            selector: 'node[type="Context"], node[type="Site"]',
            style: {
              'background-color': '#f97316',
              shape: 'round-rectangle',
              width: 'label',
              height: 22,
              padding: '6px',
              'font-weight': 600,
            },
          },
          {
            selector: 'node[type="Building"]',
            style: { 'background-color': '#0891b2', 'border-color': '#06b6d4' },
          },
          {
            selector: 'node[type="Floor"]',
            style: { 'background-color': '#06b6d4', 'border-color': '#22d3ee' },
          },
          {
            selector: 'node[type="Room"], node[type="Space"]',
            style: { 'background-color': '#7c3aed', 'border-color': '#a78bfa' },
          },
          {
            // Groupe de pièces par typologie (bureau, réunion, sanitaire...),
            // déjà créé à l'ingestion (HAS_GROUP depuis l'étage, HAS_MEMBER
            // vers chaque pièce) mais jamais stylé jusqu'ici -- se confondait
            // avec le style générique gris.
            selector: 'node[type="Typology"]',
            style: {
              'background-color': '#f59e0b',
              'border-color': '#fbbf24',
              shape: 'round-rectangle',
              width: 'label',
              padding: '8px',
            },
          },
          {
            // Placée APRÈS la règle Room ci-dessus : Cytoscape applique les
            // règles de style dans l'ordre, celle-ci doit gagner quand la
            // pièce est en surchauffe (voir refreshRoomTemperature).
            selector: 'node.overheated',
            style: { 'background-color': '#dc2626', 'border-color': '#f87171' },
          },
          {
            selector: 'node[type="Equipment"]',
            style: {
              'background-color': '#a3e635',
              color: '#1a202c',
              shape: 'round-rectangle',
              width: 30,
              height: 18,
              'font-size': 8,
              'border-color': '#84cc16',
            },
          },
          {
            selector: 'node[?placeholder]',
            style: {
              'background-color': '#334155',
              'border-color': '#94a3b8',
              'border-style': 'dashed',
            },
          },
          {
            selector: 'node:selected',
            style: { 'border-color': '#fbbf24', 'border-width': 3 },
          },
          {
            selector: 'edge',
            style: {
              width: 2,
              'line-color': '#4a5568',
              'line-opacity': 0.85,
              'target-arrow-color': '#4a5568',
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
            },
          },
        ],
        layout: DAGRE_LAYOUT,
      })

      cy.on('tap', 'node', async (event) => {
        const node = event.target
        const data = node.data() as Record<string, unknown>
        onNodeSelectRef.current?.(data)

        // Popup flottante ancrée au nœud, uniquement pour un vrai Equipment
        // (pas un nœud groupé "N équipements", dont le type 'EquipmentGroup'
        // contiendrait aussi la sous-chaîne "equipment").
        const isRealEquipment =
          data.placeholder !== true && String(data.type ?? '').toLowerCase().includes('equipment')
        const isRoom = data.placeholder !== true && ['Room', 'Space'].includes(String(data.type ?? ''))
        if (isRealEquipment) {
          const renderedPosition = node.renderedPosition()
          setEquipmentPopup({
            nodeId: node.id(),
            x: renderedPosition.x,
            y: renderedPosition.y,
            label: String(data.label ?? data.name ?? node.id()),
          })
          setRoomPopup(null)
        } else if (isRoom) {
          const renderedPosition = node.renderedPosition()
          setRoomPopup({
            nodeId: node.id(),
            x: renderedPosition.x,
            y: renderedPosition.y,
            label: String(data.label ?? data.name ?? node.id()),
          })
          setEquipmentPopup(null)
        } else {
          setEquipmentPopup(null)
          setRoomPopup(null)
        }

        if (data.placeholder === true) {
          // Nœud groupé "N équipements" : on ne va chercher la vraie liste
          // qu'ici, au clic, pour ne jamais charger d'un coup les dizaines
          // d'équipements d'une pièce en même temps que sa hiérarchie spatiale.
          if (data.context === 'room-equipment' && typeof data.parentId === 'string') {
            const parentId = data.parentId
            if (!expandedRef.current.has(node.id())) {
              try {
                const rows = await getRoomEquipment(parentId)
                const byCategory = new Map<string, CytoscapeElement['data'][]>()
                for (const raw of rows) {
                  const parsed = parseArcadeNode((raw as Record<string, unknown>).node ?? raw)
                  if (!parsed || parsed.id === parentId) continue
                  const key = parsed.category || 'autre'
                  const bucket = byCategory.get(key)
                  if (bucket) bucket.push(parsed)
                  else byCategory.set(key, [parsed])
                }

                for (const [category, nodes] of byCategory) {
                  if (nodes.length > GROUP_THRESHOLD) {
                    const labelPlural = EQUIPMENT_CATEGORY_LABELS[category] ?? `équipements (${category})`
                    const placeholder = nodeGroupPlaceholder(parentId, `equipcat-${category}`, nodes.length, labelPlural)
                    if (cy.$id(placeholder.data.id).empty()) {
                      groupedChildrenRef.current.set(placeholder.data.id, nodes)
                      cy.add(placeholder)
                      cy.add(edgeElement(parentId, placeholder.data.id))
                    }
                    continue
                  }

                  for (const parsed of nodes) {
                    if (cy.$id(parsed.id).empty()) cy.add(nodeElement(parsed))
                    const edgeId = `${parentId}-${parsed.id}`
                    if (cy.$id(edgeId).empty()) cy.add(edgeElement(parentId, parsed.id))
                  }
                }
                expandedRef.current.add(node.id())
                node.remove()
                runLayout(cy)
              } catch (error) {
                console.error(`Échec du dépliage des équipements de ${parentId}:`, error)
              }
            }
            return
          }

          // Groupe Room/Space replié à l'expansion d'un nœud (voir
          // GROUP_THRESHOLD) : le contenu est déjà en mémoire depuis le
          // /graph/neighbors initial, pas de fetch supplémentaire ici.
          if (data.context === 'node-group' && typeof data.parentId === 'string') {
            const parentId = data.parentId
            const nodes = groupedChildrenRef.current.get(node.id())
            if (nodes) {
              for (const parsed of nodes) {
                if (cy.$id(parsed.id).empty()) cy.add(nodeElement(parsed))
                const edgeId = `${parentId}-${parsed.id}`
                if (cy.$id(edgeId).empty()) cy.add(edgeElement(parentId, parsed.id))
              }
              groupedChildrenRef.current.delete(node.id())
              node.remove()
              runLayout(cy)
            }
            return
          }

          onPlaceholderClickRef.current?.(data)
          return
        }

        if (!autoExpandOnClick) return

        const nodeId = data.id as string | undefined
        if (!nodeId) return

        if (expandedRef.current.has(nodeId)) {
          // Ré-clic sur un nœud déjà déplié : bascule, le referme (retire
          // ses enfants) sans toucher au reste du graphe -- pour rouvrir,
          // recliquer dessus relance simplement un dépliage normal.
          collapseSubtree(cy, nodeId, expandedRef, groupedChildrenRef)
          // Une popup ouverte peut pointer sur un nœud qui vient de
          // disparaître avec la branche refermée.
          setEquipmentPopup((prev) => (prev && cy.getElementById(prev.nodeId).empty() ? null : prev))
          setRoomPopup((prev) => (prev && cy.getElementById(prev.nodeId).empty() ? null : prev))
          runLayout(cy)
          return
        }

        try {
          const rows = await getNodeChildren(nodeId)
          const byType = new Map<string, CytoscapeElement['data'][]>()

          for (const row of rows) {
            const record = row as Record<string, unknown>
            const neighbors = record.neighbors ?? record.m ?? record.result ?? []
            const equipmentCount = Number(record.equipment_count ?? 0)

            if (Array.isArray(neighbors)) {
              for (const neighbor of neighbors) {
                const parsed = parseArcadeNode(neighbor)
                if (!parsed || parsed.id === nodeId) continue
                // Le match backend (n)-[r]-(m) est NON ORIENTÉ : il renvoie
                // aussi le PARENT du nœud qu'on déplie comme "voisin" (ex:
                // Site en dépliant Building). Sans ce filtre, une arête
                // supplémentaire Building->Site se rajoutait en double de
                // celle déjà créée (Site->Building) au dépliage de Site. On
                // saute tout voisin déjà relié à nodeId, peu importe le sens
                // de l'arête existante.
                if (cy.$id(nodeId).edgesWith(cy.$id(parsed.id)).length > 0) continue
                const bucket = byType.get(parsed.type)
                if (bucket) bucket.push(parsed)
                else byType.set(parsed.type, [parsed])
              }
            }

            if (equipmentCount > 0) {
              const placeholder = equipmentGroupPlaceholder(nodeId, equipmentCount)
              if (cy.$id(placeholder.data.id).empty()) {
                cy.add(placeholder)
                cy.add(edgeElement(nodeId, placeholder.data.id))
              }
            }
          }

          for (const [type, nodes] of byType) {
            const labelPlural = GROUPABLE_TYPE_LABELS[type]
            if (labelPlural && nodes.length > GROUP_THRESHOLD) {
              const placeholder = nodeGroupPlaceholder(nodeId, type, nodes.length, labelPlural)
              if (cy.$id(placeholder.data.id).empty()) {
                groupedChildrenRef.current.set(placeholder.data.id, nodes)
                cy.add(placeholder)
                cy.add(edgeElement(nodeId, placeholder.data.id))
              }
              continue
            }

            for (const parsed of nodes) {
              if (cy.$id(parsed.id).empty()) cy.add(nodeElement(parsed))
              const edgeId = `${nodeId}-${parsed.id}`
              if (cy.$id(edgeId).empty()) cy.add(edgeElement(nodeId, parsed.id))
            }
          }

          expandedRef.current.add(nodeId)
          runLayout(cy)
        } catch (error) {
          // Non bloquant pour l'UI, mais visible en console plutôt que silencieux :
          // un échec ici ressemblait à "rien ne se passe au clic" sans aucun indice.
          console.error(`Échec du dépliage du nœud ${nodeId}:`, error)
        }
      })

      cy.on('tap', (event) => {
        if (event.target === cy) {
          cy.$('node:selected').unselect()
          setEquipmentPopup(null)
          setRoomPopup(null)
        }
      })

      // Le nœud (ou la vue) peut bouger pendant que la popup est ouverte
      // (pan, zoom, glisser-déposer du nœud) : on la recale à chaque frame
      // pertinente plutôt que de la laisser dériver du nœud qu'elle décrit.
      cy.on('pan zoom position drag', () => {
        setEquipmentPopup((prev) => {
          if (!prev) return prev
          const target = cy.getElementById(prev.nodeId)
          if (target.empty()) return null
          const renderedPosition = target.renderedPosition()
          return { ...prev, x: renderedPosition.x, y: renderedPosition.y }
        })
        setRoomPopup((prev) => {
          if (!prev) return prev
          const target = cy.getElementById(prev.nodeId)
          if (target.empty()) return null
          const renderedPosition = target.renderedPosition()
          return { ...prev, x: renderedPosition.x, y: renderedPosition.y }
        })
      })

      // Toute pièce/étage ajouté au graphe (chargement initial, dépliage
      // d'un nœud, changement de `elements`...) déclenche immédiatement une
      // vérification de sa température -- un seul listener couvre tous les
      // points d'ajout (cy.add) plutôt que d'instrumenter chacun séparément.
      // Floor est inclus en plus de Room/Space : sur les maquettes réelles
      // testées, les capteurs de température sont souvent rattachés
      // directement à l'étage (LOCATED_IN un Floor), pas à une pièce
      // précise -- se limiter à Room laisserait la fonctionnalité inerte.
      cy.on('add', 'node[type="Room"], node[type="Space"], node[type="Floor"]', (event) => {
        refreshRoomTemperature(cy, event.target.id())
      })

      // Panneau "Contexte de pièces" (TypologyPanel.tsx, Mode Duo) : les
      // nœuds Typology n'existent que dans CETTE instance Cytoscape, jamais
      // dans la prop `elements` du parent -- recalcule la liste complète à
      // chaque ajout/retrait pour rester exact (ex: un changement
      // d'`elements` retire tout puis rajoute, 'remove' + 'add' se
      // déclenchent l'un après l'autre, le résultat final reste correct).
      cy.on('add remove', 'node[type="Typology"]', () => {
        const typologies = cy.nodes('[type="Typology"]').map((node) => {
          const data = node.data() as Record<string, unknown>
          return { id: node.id(), label: String(data.label ?? data.name ?? node.id()) }
        })
        onTypologiesChangeRef.current?.(typologies)
      })

      cyRef.current = cy
    }

    initCy()

    // Cytoscape mesure et met en cache la position/taille de son conteneur au
    // moment de la création, pour convertir les clics souris en coordonnées
    // du graphe. Si un parent flexbox redimensionne ensuite ce conteneur
    // (ex: apparition du panneau NodeInspector, polices qui finissent de
    // charger, redimensionnement de la fenêtre) SANS que la fenêtre elle-même
    // ne se redimensionne, ce cache devient obsolète : les clics visuellement
    // sur un nœud sont alors reçus décalés, jusqu'à un resize explicite.
    // ResizeObserver détecte tout changement de taille du conteneur lui-même
    // (pas seulement de la fenêtre) et force Cytoscape à se resynchroniser.
    const resizeObserver = new ResizeObserver(() => {
      cyRef.current?.resize()
    })
    resizeObserver.observe(containerRef.current)

    // Les pièces déjà présentes dans le graphe ne reçoivent pas d'autre
    // déclencheur ('add' ne se répète pas pour un nœud déjà là) -- ce
    // minuteur les revérifie périodiquement pour que la couleur suive une
    // simulation qui continue de tourner après l'affichage initial.
    const temperatureRefreshInterval = window.setInterval(() => {
      const cy = cyRef.current
      if (!cy) return
      cy.nodes('[type="Room"], [type="Space"], [type="Floor"]').forEach((node) => {
        refreshRoomTemperature(cy, node.id())
      })
    }, ROOM_TEMPERATURE_REFRESH_MS)

    return () => {
      cancelled = true
      window.clearInterval(temperatureRefreshInterval)
      resizeObserver.disconnect()
      expandedRef.current.clear()
      groupedChildrenRef.current.clear()
      if (cyRef.current) {
        try {
          cyRef.current.destroy()
        } catch {
          // ignore destroy errors on unmount
        }
        cyRef.current = null
      }
    }
  }, [autoExpandOnClick])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy || elements.length === 0) return

    cy.elements().remove()
    cy.add(toElementDefinitions(elements))
    expandedRef.current.clear()
    groupedChildrenRef.current.clear()
    // Le nœud décrit par une popup ouverte vient d'être retiré du canvas :
    // la garder affichée pointerait sur un élément qui n'existe plus.
    setEquipmentPopup(null)
    setRoomPopup(null)
    runLayout(cy)
  }, [elements])

  // Sélection croisée (Mode Duo) : un élément cliqué en 3D fournit un
  // GlobalId -- sélectionne le nœud correspondant s'il est déjà présent
  // dans le graphe affiché et recentre la vue dessus (animation légère,
  // cohérente avec runLayout qui anime aussi ses recadrages).
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || !focusNodeId) return
    const node = cy.getElementById(focusNodeId)
    if (node.empty()) return
    cy.$('node:selected').unselect()
    node.select()
    cy.animate({ center: { eles: node } }, { duration: 300 })
  }, [focusNodeId])

  const zoomBy = (factor: number) => {
    const cy = cyRef.current
    if (!cy) return
    cy.zoom({ level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } })
  }

  const fitToView = () => {
    const cy = cyRef.current
    if (!cy) return
    cy.fit(cy.elements(), 40)
  }

  return (
    <div
      className={className}
      style={{
        width: '100%',
        height,
        border: '1px solid #334155',
        borderRadius: 12,
        overflow: 'hidden',
        background: '#111827',
        position: 'relative',
      }}
    >
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {/* Boutons explicites : la molette/le trackpad pour zoomer/déplacer peut être
          capricieux selon le matériel/navigateur — ceci marche toujours. */}
      <div
        style={{
          position: 'absolute',
          top: 10,
          right: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
        }}
      >
        {[
          { label: '+', title: 'Zoomer', onClick: () => zoomBy(1.3) },
          { label: '−', title: 'Dézoomer', onClick: () => zoomBy(1 / 1.3) },
          { label: '⤢', title: 'Tout afficher', onClick: fitToView },
        ].map((btn) => (
          <button
            key={btn.label}
            type="button"
            title={btn.title}
            onClick={btn.onClick}
            style={{
              width: 32,
              height: 32,
              borderRadius: 6,
              border: '1px solid #334155',
              background: '#1f2937',
              color: '#e2e8f0',
              cursor: 'pointer',
              fontSize: 16,
              lineHeight: 1,
            }}
          >
            {btn.label}
          </button>
        ))}
      </div>
      {equipmentPopup && (
        <EquipmentTelemetryPopup
          key={equipmentPopup.nodeId}
          globalId={equipmentPopup.nodeId}
          label={equipmentPopup.label}
          x={equipmentPopup.x}
          y={equipmentPopup.y}
          onClose={() => setEquipmentPopup(null)}
        />
      )}
      {roomPopup && (
        <RoomTelemetryPopup
          key={roomPopup.nodeId}
          roomId={roomPopup.nodeId}
          label={roomPopup.label}
          x={roomPopup.x}
          y={roomPopup.y}
          onClose={() => setRoomPopup(null)}
        />
      )}
    </div>
  )
}

export function useBimGraphActions(cyRef: React.RefObject<Core | null>, expandedRef: React.RefObject<Set<string>>) {
  const addElements = (elements: CytoscapeElement[]) => {
    const cy = cyRef.current
    if (!cy) return

    for (const element of elements) {
      const id = element.data.id
      if (cy.$id(id).empty()) {
        cy.add(toElementDefinitions([element]))
      }
    }
    runLayout(cy)
  }

  const expandNode = async (nodeId: string) => {
    const cy = cyRef.current
    if (!cy || expandedRef.current?.has(nodeId)) return

    const rows = await getNodeChildren(nodeId)
    for (const row of rows) {
      const neighbors =
        (row as Record<string, unknown>).neighbors ??
        (row as Record<string, unknown>).m ??
        (row as Record<string, unknown>).result ??
        []

      if (!Array.isArray(neighbors)) continue

      for (const neighbor of neighbors) {
        const parsed = parseArcadeNode(neighbor)
        if (!parsed || parsed.id === nodeId) continue
        if (cy.$id(parsed.id).empty()) cy.add(nodeElement(parsed))
        const edgeId = `${nodeId}-${parsed.id}`
        if (cy.$id(edgeId).empty()) cy.add(edgeElement(nodeId, parsed.id))
      }
    }

    expandedRef.current?.add(nodeId)
    runLayout(cy)
  }

  const collapseNode = (nodeId: string) => {
    const cy = cyRef.current
    if (!cy) return

    cy.edges(`[source = "${nodeId}"], [target = "${nodeId}"]`).remove()
    cy.nodes().forEach((node) => {
      if (node.degree() === 0 && node.id() !== 'SPATIAL_CONTEXT') {
        node.remove()
      }
    })

    expandedRef.current?.delete(nodeId)
    runLayout(cy)
  }

  return { addElements, expandNode, collapseNode }
}
