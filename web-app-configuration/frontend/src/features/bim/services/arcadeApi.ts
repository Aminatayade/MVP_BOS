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
  // `error` : forme des erreurs bim_api/timeseries_api (http.server maison).
  // `detail` : forme par défaut de FastAPI (HTTPException) -- api-gateway et
  // timeseries_api sont FastAPI, donc un 404/422 renvoie `{"detail": "..."}`,
  // pas `{"error": "..."}` (sans ce fallback, ces messages n'étaient jamais
  // affichés et on retombait sur le générique axios "Request failed with
  // status code 404").
  const anyError = error as { response?: { data?: { error?: string; detail?: string } }; message?: string }
  return anyError.response?.data?.error || anyError.response?.data?.detail || anyError.message || fallback
}

export async function getNodeChildren(zone: string, nodeId: string) {
  try {
    const resp = await apiClient.get('/api/bim/graph/neighbors', { params: { global_id: nodeId, zone } })
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'ArcadeDB query failed'))
  }
}

/**
 * Moyenne agrégée d'une métrique pour TOUS les équipements rattachés à un
 * Room/Floor (voir /graph/location-readings côté backend -- même route
 * utilisée par les panels Grafana de l'onglet Supervision). Utilisé ici pour
 * détecter une pièce en surchauffe (voir BimGraphViewer.tsx) : `readings`
 * est trié chronologiquement croissant, le dernier élément est donc la
 * valeur la plus récente.
 */
export async function getLocationReadings(
  zone: string,
  locationId: string,
  metric: string,
  bucketSeconds = 3600,
): Promise<{ value: number; ts: string }[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/location-readings', {
      params: { global_id: locationId, metric, bucket_seconds: bucketSeconds, zone },
    })
    return (resp.data.readings ?? []) as { value: number; ts: string }[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch location readings'))
  }
}

export async function getRoomEquipment(zone: string, nodeId: string) {
  try {
    const resp = await apiClient.get('/api/bim/graph/room-equipment', { params: { global_id: nodeId, zone } })
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch room equipment'))
  }
}

export async function getRootContexts(zone: string) {
  try {
    const resp = await apiClient.get('/api/bim/graph/floors', { params: { zone } })
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch floors'))
  }
}

export interface FloorOption {
  global_id: string
  name?: string
  zone?: string
  buildingId?: string
}

export async function getFloors(zone: string): Promise<FloorOption[]> {
  const rows = await getRootContexts(zone)
  const floors: FloorOption[] = []

  for (const row of rows) {
    const rowRecord = row as Record<string, unknown>
    const candidate = rowRecord.f ?? rowRecord.floor ?? row
    const attrs =
      ((candidate as Record<string, unknown>).attributes as Record<string, unknown> | undefined) ??
      (candidate as Record<string, unknown>)
    const globalId = attrs.global_id as string | undefined
    if (globalId) {
      floors.push({
        global_id: globalId,
        name: attrs.name as string | undefined,
        zone: attrs.zone as string | undefined,
        buildingId: rowRecord.building_id as string | undefined,
      })
    }
  }

  return floors
}

export interface BuildingOption {
  global_id: string
  name?: string
}

/** Liste des bâtiments d'une zone -- alimente l'étape "Choisir un bâtiment"
 * du sélecteur d'Immersion quand une zone en contient plusieurs (une
 * maquette par discipline, chacune avec son propre Building/GlobalId IFC). */
export async function getBuildingsList(zone?: string): Promise<BuildingOption[]> {
  const rows = await getBuildings(zone)
  const buildings: BuildingOption[] = []
  for (const row of rows) {
    const candidate = (row as Record<string, unknown>).b ?? (row as Record<string, unknown>).building ?? row
    const attrs =
      ((candidate as Record<string, unknown>).attributes as Record<string, unknown> | undefined) ??
      (candidate as Record<string, unknown>)
    const globalId = attrs.global_id as string | undefined
    if (globalId) {
      buildings.push({ global_id: globalId, name: attrs.name as string | undefined })
    }
  }
  return buildings
}

export interface RoomOption {
  global_id: string
  name?: string
}

/** Pièces d'un étage donné, pour le sélecteur de téléportation en immersion 3D. */
export async function getFloorRooms(floorId: string, zone?: string): Promise<RoomOption[]> {
  const rows = await getFloorView(floorId, zone)
  const roomOptions: RoomOption[] = []
  for (const row of rows) {
    const roomRows = (row as Record<string, unknown>).rooms
    if (!Array.isArray(roomRows)) continue
    for (const r of roomRows) {
      const globalId = (r as Record<string, unknown>).global_id as string | undefined
      if (globalId) roomOptions.push({ global_id: globalId, name: (r as Record<string, unknown>).name as string | undefined })
    }
  }
  return roomOptions
}

/** Équipements d'un étage donné (rattachés à une pièce OU directement à
 * l'étage) -- utilisé en repli caméra quand l'étage n'a aucune pièce : un
 * IfcBuildingStorey n'a généralement pas de géométrie propre, donc recadrer
 * sur SA boîte englobante ne donne rien ; on recadre plutôt sur l'ensemble
 * de son équipement, qui lui a une géométrie réelle. */
export async function getFloorEquipment(floorId: string, zone?: string): Promise<RoomOption[]> {
  const rows = await getFloorView(floorId, zone)
  const equipmentOptions: RoomOption[] = []
  for (const row of rows) {
    const equipmentRows = (row as Record<string, unknown>).equipment
    if (!Array.isArray(equipmentRows)) continue
    for (const e of equipmentRows) {
      const globalId = (e as Record<string, unknown>).global_id as string | undefined
      if (globalId) equipmentOptions.push({ global_id: globalId, name: (e as Record<string, unknown>).name as string | undefined })
    }
  }
  return equipmentOptions
}

export async function getFloorView(floorId: string, zone?: string) {
  try {
    const resp = await apiClient.get('/api/bim/graph/view', { params: zone ? { floor_id: floorId, zone } : { floor_id: floorId } })
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

/**
 * zone optionnel : sans lui, retombe sur tous les bâtiments ingérés (toutes
 * zones confondues) -- avec lui, scope à la zone courante. Le corriger ici
 * évitait qu'une nouvelle ingestion ait l'air de laisser l'ancienne maquette
 * "en cache" (elle restait juste mélangée avec la nouvelle dans une liste
 * jamais filtrée).
 */
export async function getBuildings(zone?: string) {
  try {
    const resp = await apiClient.get('/api/bim/graph/buildings', { params: zone ? { zone } : undefined })
    return unwrapArcadeResult(resp.data)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch buildings'))
  }
}

/**
 * Supprime intégralement une zone (Building/Floor/Room/Equipment/Typology +
 * leur historique time-series) -- pour nettoyer une maquette de test sans
 * laisser de résidus qui polluent le graphe (plusieurs Building affichés en
 * même temps) ou la variable Grafana "location" (entrées mortes).
 * Irréversible : à confirmer côté UI avant l'appel.
 */
export async function deleteZone(zone: string): Promise<{ deleted_nodes: Record<string, number>; deleted_readings: number }> {
  try {
    const resp = await apiClient.delete('/api/bim/graph/zone', { params: { zone } })
    return resp.data
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to delete zone'))
  }
}

export async function getEquipment(zone: string) {
  try {
    const resp = await apiClient.get('/api/bim/graph/equipment', { params: { zone } })
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

export interface EquipmentMetricSummary {
  metric: string
  unit: string
  value_labels: Record<string, string> | null
  current: { value: number; timestamp: string; quality?: string } | null
  history: { value: number; timestamp: string }[]
}

/**
 * Formate une valeur numérique de métrique pour l'affichage -- valeur
 * courante OU un point d'historique quelconque, même règle dans les deux cas
 * (voir formatMetricValue). Arrondit avant de chercher un `value_labels` :
 * les générateurs de simulation ajoutent volontairement du bruit même aux
 * métriques d'état (0/1) pour éviter des plateaux parfaitement plats -- une
 * valeur comme 1.07 ne matcherait jamais la clé exacte "1" sinon, et
 * retomberait à tort sur le nombre brut au lieu du libellé ("Marche").
 */
export function formatValue(
  rawValue: number | null | undefined,
  metric: Pick<EquipmentMetricSummary, 'unit' | 'value_labels'>,
): string {
  if (rawValue === undefined || rawValue === null) return '—'
  const label = metric.value_labels?.[String(Math.round(rawValue))]
  if (label) return label
  return metric.unit ? `${rawValue} ${metric.unit}` : String(rawValue)
}

/** Valeur COURANTE d'une métrique -- popups graphe et 3D. */
export function formatMetricValue(metric: EquipmentMetricSummary): string {
  return formatValue(metric.current?.value, metric)
}

/**
 * "2026-08-31 07:07:24.957" -> "31/08 07:07". Formatage par découpe de
 * chaîne (pas new Date()) pour afficher exactement l'horodatage stocké en
 * base sans décalage de fuseau introduit par le navigateur.
 */
export function formatHistoryTimestamp(timestamp: string): string {
  const [datePart, timePart] = timestamp.split(' ')
  if (!datePart || !timePart) return timestamp
  const [, month, day] = datePart.split('-')
  const [hh, mm] = timePart.split(':')
  if (!month || !day || !hh || !mm) return timestamp
  return `${day}/${month} ${hh}:${mm}`
}

/**
 * Les noms d'équipement exportés depuis Revit sont souvent très longs
 * ("M_Centrifugal Fan - Rooftop - Upblast:991-1905 LPS:991-1905
 * LPS:602396") -- sans coupe explicite, ce texte pousse le reste de la
 * popup (valeurs d'historique comprises) hors de la largeur visible.
 * Tronqué en JS plutôt que par CSS `text-overflow: ellipsis` seul, pour un
 * rendu garanti même si la feuille de style n'est pas (encore) appliquée.
 */
export function truncateLabel(label: string, maxLength = 32): string {
  return label.length > maxLength ? `${label.slice(0, maxLength - 1)}…` : label
}

/**
 * Valeur courante + historique récent d'un équipement, groupés par métrique
 * -- consommé par la popup flottante du graphe (EquipmentTelemetryPopup).
 * `metrics: []` est une réponse valide (équipement sans télémétrie simulée
 * pour son type, ex: mobilier), pas une erreur.
 */
export async function getEquipmentSummary(
  globalId: string,
  zone: string,
  historyLimit = 50,
): Promise<EquipmentMetricSummary[]> {
  try {
    const resp = await apiClient.get('/api/bim/timeseries/equipment-summary', {
      params: { global_id: globalId, zone, history_limit: historyLimit },
    })
    return (resp.data.metrics ?? []) as EquipmentMetricSummary[]
  } catch (error) {
    // 404 = ce GlobalId n'est pas un nœud Equipment dans le graphe (mur,
    // pièce, mobilier non catalogué...) -- un clic 3D peut toucher
    // n'importe quel type d'élément, pas seulement de l'équipement, donc
    // c'est un état normal (pas de télémétrie) et non une erreur à afficher.
    const status = (error as { response?: { status?: number } }).response?.status
    if (status === 404) return []
    throw new Error(extractErrorMessage(error, 'Failed to fetch equipment summary'))
  }
}

/**
 * Upload + validation + ingestion d'un lot de maquettes IFC.
 * Remplace le fetch('http://127.0.0.1:8000/ingest-batch', ...) d'origine.
 */
export async function ingestBatch(zone: string, files: { name: string; disciplines: string[]; content: string }[]) {
  try {
    const resp = await apiClient.post(
      '/api/bim/ingest-batch',
      { zone, files },
      { timeout: 600_000 },
    )
    return resp.data
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Analysis failed'))
  }
}

/**
 * Liste les zones connues (isolation par zone, Phase 1 -- voir /graph/zones
 * côté bim_api) -- remplace le champ texte libre de zone par un vrai menu
 * déroulant, pour éviter les zones fantômes créées par une faute de frappe.
 */
export async function getZones(): Promise<string[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/zones')
    return (resp.data.zones ?? []) as string[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch zones'))
  }
}
