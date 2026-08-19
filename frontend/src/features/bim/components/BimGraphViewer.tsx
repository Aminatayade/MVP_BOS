import { useEffect, useRef } from 'react'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import dagre from 'cytoscape-dagre'
import { getNodeChildren, getRoomEquipment } from '../services/arcadeApi'
import type { CytoscapeElement } from '../utils/graphUtils'
import { edgeElement, equipmentGroupPlaceholder, nodeElement, parseArcadeNode } from '../utils/graphUtils'

cytoscape.use(dagre)

export const DAGRE_LAYOUT = {
  name: 'dagre',
  rankDir: 'LR',
  nodeSep: 25,
  rankSep: 120,
  animate: true,
} as const

type Props = {
  elements?: CytoscapeElement[]
  onNodeSelect?: (data: Record<string, unknown>) => void
  onPlaceholderClick?: (data: Record<string, unknown>) => void
  className?: string
  height?: string | number
  autoExpandOnClick?: boolean
}

function toElementDefinitions(elements: CytoscapeElement[]): ElementDefinition[] {
  return elements.map((element) => {
    if ('source' in element.data || 'target' in element.data) {
      return { group: 'edges', data: element.data }
    }
    return { group: 'nodes', data: element.data }
  })
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
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cyRef = useRef<Core | null>(null)
  const expandedRef = useRef<Set<string>>(new Set())

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
              'text-margin-x': 10,
              'font-size': 11,
              'font-family': 'Inter, ui-sans-serif, system-ui',
              width: 36,
              height: 36,
              'background-color': '#475569',
              'background-image': 'data(icon)',
              'background-fit': 'cover',
              'background-clip': 'node',
              'border-width': 2,
              'border-color': '#64748b',
            },
          },
          {
            selector: 'node[type="Context"], node[type="Site"]',
            style: {
              'background-color': '#f97316',
              shape: 'round-rectangle',
              width: 'label',
              height: 32,
              padding: '10px',
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
            selector: 'node[type="Equipment"]',
            style: {
              'background-color': '#a3e635',
              color: '#1a202c',
              shape: 'round-rectangle',
              width: 44,
              height: 28,
              'font-size': 10,
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

        if (data.placeholder === true) {
          // Nœud groupé "N équipements" : on ne va chercher la vraie liste
          // qu'ici, au clic, pour ne jamais charger d'un coup les dizaines
          // d'équipements d'une pièce en même temps que sa hiérarchie spatiale.
          if (data.context === 'room-equipment' && typeof data.parentId === 'string') {
            const parentId = data.parentId
            if (!expandedRef.current.has(node.id())) {
              try {
                const rows = await getRoomEquipment(parentId)
                for (const raw of rows) {
                  const parsed = parseArcadeNode((raw as Record<string, unknown>).node ?? raw)
                  if (!parsed || parsed.id === parentId) continue

                  if (cy.$id(parsed.id).empty()) cy.add(nodeElement(parsed))

                  const edgeId = `${parentId}-${parsed.id}`
                  if (cy.$id(edgeId).empty()) cy.add(edgeElement(parentId, parsed.id))
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

          onPlaceholderClickRef.current?.(data)
          return
        }

        if (!autoExpandOnClick) return

        const nodeId = data.id as string | undefined
        if (!nodeId || expandedRef.current.has(nodeId)) return

        try {
          const rows = await getNodeChildren(nodeId)
          for (const row of rows) {
            const record = row as Record<string, unknown>
            const neighbors = record.neighbors ?? record.m ?? record.result ?? []
            const equipmentCount = Number(record.equipment_count ?? 0)

            if (Array.isArray(neighbors)) {
              for (const neighbor of neighbors) {
                const parsed = parseArcadeNode(neighbor)
                if (!parsed || parsed.id === nodeId) continue

                if (cy.$id(parsed.id).empty()) {
                  cy.add(nodeElement(parsed))
                }

                const edgeId = `${nodeId}-${parsed.id}`
                if (cy.$id(edgeId).empty()) {
                  cy.add(edgeElement(nodeId, parsed.id))
                }
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
        }
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

    return () => {
      cancelled = true
      resizeObserver.disconnect()
      expandedRef.current.clear()
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
    runLayout(cy)
  }, [elements])

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
