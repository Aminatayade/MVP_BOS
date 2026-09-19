/**
 * Client pour les routes BIM en lecture seule exposées à BOS Exécution par
 * l'API Gateway (voir api-gateway/bim_proxy.py). Version allégée de
 * web-app-configuration/frontend/.../arcadeApi.ts : seules les deux fonctions
 * nécessaires à la visualisation 3D (point 6 de la roadmap, volet Exécution)
 * -- pas d'upload, pas de validation/ingestion, réservées à Configurateur.
 */

import apiClient from '../../../services/apiClient'

function unwrapArcadeResult(payload: Record<string, unknown>): any[] {
  const arcade: any = payload.result ?? {}
  return arcade.result ?? arcade ?? []
}

function extractErrorMessage(error: unknown, fallback: string): string {
  const anyError = error as { response?: { data?: { error?: string } }; message?: string }
  return anyError.response?.data?.error || anyError.message || fallback
}

export interface ModelSource {
  discipline: string
  storageUrl: string
}

/**
 * Liste les fichiers IFC déjà ingérés pour une zone, un par discipline.
 * Renvoie une liste vide si rien n'a encore été ingéré côté Configurateur --
 * ce n'est pas une erreur, juste l'état attendu tant que l'ingestion n'a pas
 * eu lieu.
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
 * Télécharge un IFC source pour le visualiseur 3D. Passe par apiClient (pas
 * une URL brute) car /api/bim/models/download exige le Bearer token Keycloak.
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
 * Résout le global_id du Building d'une zone -- préalable pour appeler
 * getBuildingConsumption (widget "Consommation électrique").
 */
export async function getBuildingGlobalId(zone: string): Promise<string | null> {
  try {
    const resp = await apiClient.get('/api/bim/graph/buildings', { params: { zone } })
    // /graph/buildings renvoie { ok, result: { result: [...] } } -- deux
    // niveaux d'imbrication (la réponse ArcadeDB brute est elle-même sous
    // result), d'où le double .result que unwrapArcadeResult ne couvre pas
    // ici (il attend result.result directement, pas result.result.result).
    const rows = (resp.data as Record<string, unknown>).result as Record<string, unknown>
    const list = (rows?.result ?? []) as Record<string, unknown>[]
    const first = list[0]
    return (first?.global_id as string | undefined) ?? null
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch building'))
  }
}

export interface FloorConsumption {
  floor_id: string
  floor_name: string
  value: number
  equipment_count: number
  equipment_with_data: number
}

export interface BuildingConsumption {
  building_id: string
  metric: string
  unit: string
  total: number
  by_floor: FloorConsumption[]
}

/**
 * Widget "Consommation électrique" : total bâtiment + répartition par étage.
 * Calcul entièrement délégué à bim_api/ArcadeDB (somme de la dernière valeur
 * connue par équipement, voir /graph/building-consumption côté backend) --
 * ce client ne fait qu'afficher la réponse, aucun recalcul côté frontend.
 * `category` (optionnel) limite aux équipements d'une des 6 catégories
 * EQUIPMENT_CATEGORIES (cvc/electricity/security/sanitary/furniture/controls)
 * -- widget du constructeur de cockpits scopé à un seul type de donnée.
 * N'a de sens QUE pour un scope Bâtiment (voir WidgetBlock.tsx) : c'est le
 * seul cas où le découpage "par étage" renvoyé est exploitable tel quel.
 */
export async function getBuildingConsumption(
  zone: string,
  buildingId: string,
  metric = 'power_instant_kw',
  category?: string,
): Promise<BuildingConsumption> {
  try {
    const resp = await apiClient.get('/api/bim/graph/building-consumption', {
      params: { global_id: buildingId, metric, zone, ...(category ? { category } : {}) },
    })
    return resp.data as BuildingConsumption
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch building consumption'))
  }
}

export interface ReadingPoint {
  ts: string
  value: number
}

/**
 * Courbe temporelle pour un ou plusieurs lieux (Building/Floor/Room, ou une
 * liste de Room pour une typologie) -- même endpoint que les panels Grafana
 * (/graph/location-readings côté bim_api), utilisé par WidgetBlock.tsx
 * (constructeur de cockpits).
 */
export async function getLocationReadings(
  zone: string,
  locationIds: string[],
  metric = 'power_instant_kw',
  bucketSeconds = 300,
  category?: string,
  aggregation?: string,
  rawLimit?: number,
  startMs?: number,
  endMs?: number,
): Promise<ReadingPoint[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/location-readings', {
      params: {
        global_id: locationIds.join(','),
        metric,
        bucket_seconds: bucketSeconds,
        zone,
        ...(category ? { category } : {}),
        ...(aggregation ? { aggregation } : {}),
        ...(rawLimit ? { raw_limit: rawLimit } : {}),
        ...(startMs ? { start: startMs } : {}),
        ...(endMs ? { end: endMs } : {}),
      },
    })
    return (resp.data.readings ?? []) as ReadingPoint[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch location readings'))
  }
}

export interface LocationSummary {
  location_count: number
  equipment_count: number
  aggregation: string
  value: number | null
  unit: string
  sum: number | null
  avg: number | null
  max: number | null
  min: number | null
  sample_count: number
}

/**
 * Agrégat unique (pas une courbe) sur un ou plusieurs lieux -- widget
 * Indicateur du constructeur de cockpits, et widgets Barres/Camembert hors
 * échelle Bâtiment (un par type de donnée sélectionné). Calcul natif
 * ArcadeDB (SUM/AVG/MAX/MIN), voir /graph/location-readings-summary.
 */
export async function getLocationSummary(
  zone: string,
  locationIds: string[],
  metric: string,
  aggregation: 'sum' | 'avg' | 'max' | 'min' = 'sum',
  category?: string,
  startMs?: number,
  endMs?: number,
): Promise<LocationSummary> {
  try {
    const resp = await apiClient.get('/api/bim/graph/location-readings-summary', {
      params: {
        global_id: locationIds.join(','),
        metric,
        aggregation,
        zone,
        ...(category ? { category } : {}),
        ...(startMs ? { start: startMs } : {}),
        ...(endMs ? { end: endMs } : {}),
      },
    })
    return resp.data as LocationSummary
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch location summary'))
  }
}

export interface EquipmentCategoryScope {
  category: string
  equipment_count: number
  equipment_types: { equipment_type: string | null; count: number }[]
  // Métriques RÉELLEMENT simulées pour cette catégorie (union sur ses
  // equipment_types) -- jamais une liste statique, voir
  // JsonEquipmentCatalogAdapter côté bim_api.
  metrics: { name: string; unit: string }[]
}

/**
 * Colonne "Données disponibles" du constructeur de cockpits : catégories
 * d'équipement RÉELLEMENT présentes sous le périmètre spatial donné --
 * jamais une liste statique. Voir /graph/equipment-scope côté bim_api.
 */
export async function getEquipmentScope(zone: string, locationIds: string[]): Promise<EquipmentCategoryScope[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/equipment-scope', {
      params: { global_id: locationIds.join(','), zone },
    })
    return (resp.data.categories ?? []) as EquipmentCategoryScope[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch equipment scope'))
  }
}

export interface EquipmentListItem {
  global_id: string
  name: string
  equipment_type: string
  category: string
}

/**
 * Variante "instances" de getEquipmentScope (qui n'agrège que des comptes) --
 * alimente le sélecteur "cibler un équipement précis" du widget Indicateur.
 * Voir /graph/equipment-list côté bim_api.
 */
export async function getEquipmentList(zone: string, locationIds: string[]): Promise<EquipmentListItem[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/equipment-list', {
      params: { global_id: locationIds.join(','), zone },
    })
    return (resp.data.equipment ?? []) as EquipmentListItem[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch equipment list'))
  }
}

export interface EquipmentMetricSummary {
  metric: string
  unit: string
  current: { value: number; timestamp: string; quality?: string } | null
  value_labels?: Record<string, string>
}

export interface EquipmentSummary {
  global_id: string
  equipment_type: string
  metrics: EquipmentMetricSummary[]
}

/**
 * Valeur courante (+ libellés du catalogue, ex: "OK"/"Défaut") d'UN
 * équipement précis, toutes ses métriques -- alimente le widget Indicateur
 * en mode "équipement ciblé". Voir /timeseries/equipment-summary.
 */
export async function getEquipmentSummary(globalId: string): Promise<EquipmentSummary> {
  try {
    const resp = await apiClient.get('/api/bim/timeseries/equipment-summary', {
      params: { global_id: globalId },
    })
    return resp.data as EquipmentSummary
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch equipment summary'))
  }
}

export interface Typology {
  name: string
  room_count: number
  floor_count: number
}

/**
 * Niveau "Type de pièce" du sélecteur Espace -- typologies présentes dans le
 * bâtiment, unionnées à travers tous les étages (voir /graph/typologies,
 * un nœud Typology existe PAR ÉTAGE côté graphe).
 */
export async function getTypologies(zone: string, buildingId: string): Promise<Typology[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/typologies', {
      params: { global_id: buildingId, zone },
    })
    return (resp.data.typologies ?? []) as Typology[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch typologies'))
  }
}

export interface TypologyRoom {
  room_id: string
  room_name: string
  floor_id: string
  floor_name: string
}

/**
 * Résout un nom de typologie en Room concrets à travers tout le bâtiment --
 * le constructeur de cockpits passe ensuite `rooms.map(r => r.room_id)`
 * comme `space.ids` aux endpoints de lecture (déjà multi-id).
 */
export async function getRoomsByTypology(zone: string, buildingId: string, name: string): Promise<TypologyRoom[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/rooms-by-typology', {
      params: { global_id: buildingId, name, zone },
    })
    return (resp.data.rooms ?? []) as TypologyRoom[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch rooms by typology'))
  }
}

/** Niveau "Pièce spécifique" du sélecteur Espace -- pièces d'UN étage. */
export async function getRoomsByFloor(zone: string, floorId: string): Promise<TypologyRoom[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/rooms-by-floor', {
      params: { global_id: floorId, zone },
    })
    return (resp.data.rooms ?? []) as TypologyRoom[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch rooms by floor'))
  }
}

export interface Floor {
  global_id: string
  name: string
}

/** Niveau "Étage" du sélecteur Espace -- liste des étages d'une zone. */
export async function getFloors(zone: string): Promise<Floor[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/floors', { params: { zone } })
    const rows = (resp.data as Record<string, unknown>).result as Record<string, unknown>
    const list = (rows?.result ?? []) as Record<string, unknown>[]
    return list
      .map((row) => {
        // RETURN f, b.global_id AS building_id (2 colonnes) -- ArcadeDB ne
        // met le nœud à plat sur la ligne QUE quand il est la SEULE colonne
        // retournée ; ici les propriétés du Floor sont imbriquées sous `f`.
        const floor = (row.f ?? row) as Record<string, unknown>
        return { global_id: floor.global_id as string, name: (floor.name as string) ?? (floor.global_id as string) }
      })
      .filter((f) => !!f.global_id)
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch floors'))
  }
}

/**
 * Liste les zones connues (isolation par zone, Phase 1 -- voir
 * /graph/zones côté bim_api) -- remplace le champ texte libre de zone par
 * un vrai menu déroulant, pour éviter les zones fantômes créées par une
 * faute de frappe.
 */
export async function getZones(): Promise<string[]> {
  try {
    const resp = await apiClient.get('/api/bim/graph/zones')
    return (resp.data.zones ?? []) as string[]
  } catch (error) {
    throw new Error(extractErrorMessage(error, 'Failed to fetch zones'))
  }
}
