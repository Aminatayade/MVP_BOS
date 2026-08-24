/**
 * Client pour les routes BIM exposées par l'API Gateway BOS Light
 * (proxy vers bim_api / timeseries_api — voir api-gateway/bim_proxy.py).
 *
 * Contrairement à la version d'origine (MVP-bim-integration/frontend_bim), ce
 * module passe par `apiClient` (axios + intercepteur JWT Keycloak) plutôt que
 * par `fetch` brut : les routes /api/bim/* de la gateway exigent désormais un
 * rôle Administrateur/Configurateur, donc le Bearer token est obligatoire.
 */

import apiClient from '../../../services/apiClient'

// Retourne `any[]` (pas `unknown[]`) délibérément : la forme des lignes
// ArcadeDB varie selon la requête Cypher (attributs à plat ou sous `.result`),
// et BimGraphViewer/NodeInspector indexent ces lignes dynamiquement — les
// forcer dans un type strict n'apporterait pas de sécurité réelle ici.
function unwrapArcadeResult(payload: Record<string, unknown>): any[] {
  const arcade: any = payload.result ?? {}
  return arcade.result ?? arcade ?? []
}

function extractErrorMessage(error: unknown, fallback: string): string {
  const anyError = error as { response?: { data?: { error?: string } }; message?: string }
  return anyError.response?.data?.error || anyError.message || fallback
}

export async function getNodeChildren(nodeId: string) {
  try {
    const resp = await apiClient.get('/api/bim/graph/neighbors', { params: { global_id: nodeId } })
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'ArcadeDB query failed'))
  }
}

export async function getRoomEquipment(nodeId: string) {
  try {
    const resp = await apiClient.get('/api/bim/graph/room-equipment', { params: { global_id: nodeId } })
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch room equipment'))
  }
}

export async function getRootContexts() {
  try {
    const resp = await apiClient.get('/api/bim/graph/floors')
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch floors'))
  }
}

export async function getFloors(): Promise<{ global_id: string; name?: string }[]> {
  const rows = await getRootContexts()
  const floors: { global_id: string; name?: string }[] = []

  for (const row of rows) {
    const candidate = (row as Record<string, unknown>).f ?? (row as Record<string, unknown>).floor ?? row
    const attrs =
      ((candidate as Record<string, unknown>).attributes as Record<string, unknown> | undefined) ??
      (candidate as Record<string, unknown>)
    const globalId = attrs.global_id as string | undefined
    if (globalId) floors.push({ global_id: globalId, name: attrs.name as string | undefined })
  }

  return floors
}

export async function getFloorView(floorId: string) {
  try {
    const resp = await apiClient.get('/api/bim/graph/view', { params: { floor_id: floorId } })
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch floor view'))
  }
}

export interface ModelSource {
  discipline: string
  storageUrl: string
}

/**
 * Liste les fichiers IFC deja ingeres pour une zone, un par discipline
 * (Point 6 de la roadmap : jumeau numerique). Chaque backbone spatial
 * (Building/Floor/Room) et chaque Equipment porte sa propre `storage_url` --
 * la requete graphe cote backend deduplique deja, on ne fait qu'adapter la
 * forme de la ligne ici.
 */
export async function getModelSources(zone: string): Promise<ModelSource[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/sources', { params: { zone } })
    const rows = unwrapArcadeResult(resp.data)
    const sources: ModelSource[] = []
    for (const row of rows) {
      const discipline = (row as Record<string, unknown>).discipline as string | undefined
      const storageUrl = (row as Record<string, unknown>).storage_url as string | undefined
      if (discipline && storageUrl) sources.push({ discipline, storageUrl })
    }
    return sources
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch model sources'))
  }
}

/**
 * Telecharge un IFC source pour le visualiseur 3D. Passe par apiClient (pas
 * une URL brute dans un <img>/<a>) car la route /api/bim/models/download,
 * comme toutes les autres routes /api/bim/*, exige le Bearer token Keycloak
 * -- une URL directe ne pourrait pas porter cet en-tete.
 */
export async function downloadModelBytes(storageUrl: string): Promise<ArrayBuffer> {
  try {
    const resp = await apiClient.get('/api/bim/models/download', {
      params: { url: storageUrl },
      responseType: 'arraybuffer',
    })
    return resp.data as ArrayBuffer
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to download IFC model'))
  }
}

export async function getBuildings() {
  try {
    const resp = await apiClient.get('/api/bim/graph/buildings')
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch buildings'))
  }
}

export async function getEquipment() {
  try {
    const resp = await apiClient.get('/api/bim/graph/equipment')
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch equipment'))
  }
}

export interface EquipmentReading {
  ts: string
  metric: string
  value: number
  unit: string
  quality: string
  source: string
}

export async function getEquipmentReadings(
  globalId: string,
  options: { metric?: string; from?: string; to?: string; limit?: number } = {},
): Promise<EquipmentReading[]> {
  try {
    const resp = await apiClient.get('/api/bim/timeseries/readings', {
      params: {
        global_id: globalId,
        metric: options.metric,
        from: options.from,
        to: options.to,
        limit: options.limit,
      },
    })
    return resp.data.readings as EquipmentReading[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch equipment readings'))
  }
}

/**
 * Upload + validation + ingestion d'un lot de maquettes IFC.
 * Remplace le fetch('http://127.0.0.1:8000/ingest-batch', ...) d'origine.
 */
export async function ingestBatch(zone: string, files: { name: string; discipline: string; content: string }[]) {
  try {
    const resp = await apiClient.post(
      '/api/bim/ingest-batch',
      { zone, files },
      { timeout: 120_000 },
    )
    return resp.data
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Analysis failed'))
  }
}
