export type CytoscapeElement = {
  data: {
    id: string
    label: string
    type: string
    category?: string
    icon?: string
    [key: string]: unknown
  }
}

export function parseArcadeNode(raw: unknown): CytoscapeElement['data'] | null {
  if (!raw || typeof raw !== 'object') return null
  const node = raw as Record<string, unknown>
  const attrs = (node.attributes as Record<string, unknown> | undefined) ?? node
  const globalId = attrs.global_id as string | undefined
  if (!globalId) return null

  const type =
    (node['@type'] as string | undefined) ??
    (node['@class'] as string | undefined) ??
    (attrs.type as string | undefined) ??
    (Array.isArray(node.labels) ? (node.labels[0] as string) : undefined) ??
    'Node'

  const category = (attrs.category as string | undefined)?.toLowerCase()
  const isEquipment = String(type).toLowerCase().includes('equipment')
  const icon = category || isEquipment ? `/icons/${category || 'generic'}.svg` : undefined

  return {
    id: globalId,
    label: (attrs.name as string | undefined) ?? (attrs.ifc_type as string | undefined) ?? globalId,
    type,
    category,
    icon,
    ifc_type: attrs.ifc_type,
    name: attrs.name,
    properties: attrs.psets ?? attrs.properties,
  }
}

export function nodeElement(data: CytoscapeElement['data']): CytoscapeElement {
  return { data }
}

export function edgeElement(source: string, target: string): CytoscapeElement {
  return { data: { id: `${source}-${target}`, source, target, label: '', type: 'edge' } }
}

export function buildElementsFromFloorView(rows: unknown[]): CytoscapeElement[] {
  const nodesMap = new Map<string, CytoscapeElement>()
  const edges: CytoscapeElement[] = []

  for (const row of rows) {
    if (!row || typeof row !== 'object') continue
    const record = row as Record<string, unknown>
    const floor = record.f ?? record.floor
    const rooms = (record.rooms ?? record.r ?? []) as unknown[]
    const equipment = (record.equipment ?? record.e ?? []) as unknown[]

    const floorData = parseArcadeNode(floor)
    if (floorData) nodesMap.set(floorData.id, nodeElement(floorData))

    for (const room of rooms) {
      const roomData = parseArcadeNode(room)
      if (!roomData) continue
      nodesMap.set(roomData.id, nodeElement(roomData))
      if (floorData) edges.push(edgeElement(floorData.id, roomData.id))
    }

    for (const item of equipment) {
      const equipData = parseArcadeNode(item)
      if (!equipData) continue
      nodesMap.set(equipData.id, nodeElement(equipData))

      const firstRoom = rooms[0]
      const roomData = parseArcadeNode(firstRoom)
      if (roomData) {
        edges.push(edgeElement(roomData.id, equipData.id))
      } else if (floorData) {
        edges.push(edgeElement(floorData.id, equipData.id))
      }
    }
  }

  return [...nodesMap.values(), ...edges]
}

export function buildSpatialContextTree(): CytoscapeElement[] {
  const rootId = 'SPATIAL_CONTEXT'
  const root: CytoscapeElement = {
    data: { id: rootId, label: 'SpatialContext', type: 'Context' },
  }

  const placeholders: CytoscapeElement[] = [
    { data: { id: 'CTX_BUILDINGS', label: 'Bâtiments', type: 'Context', placeholder: true, context: 'buildings' } },
    { data: { id: 'CTX_FLOORS', label: 'Étages', type: 'Context', placeholder: true, context: 'floors' } },
    { data: { id: 'CTX_EQUIP', label: 'Équipements', type: 'Context', placeholder: true, context: 'equipment' } },
  ]

  const edges = placeholders.map((node) => edgeElement(rootId, node.data.id))
  return [root, ...placeholders, ...edges]
}

export function arcadeRowsToNodeList(rows: unknown[]): CytoscapeElement['data'][] {
  const nodes: CytoscapeElement['data'][] = []
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue
    const record = row as Record<string, unknown>
    const candidates = [
      record.f,
      record.floor,
      record.b,
      record.building,
      record.e,
      record.equipment,
      record.node,
      record.n,
      record,
    ]
    for (const candidate of candidates) {
      const parsed = parseArcadeNode(candidate)
      if (parsed) nodes.push(parsed)
    }
  }
  return nodes
}
