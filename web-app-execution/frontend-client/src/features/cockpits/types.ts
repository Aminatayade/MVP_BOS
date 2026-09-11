/**
 * FILE: features/cockpits/types.ts
 * DESCRIPTION: Schéma JSON d'une composition de cockpit (voir le plan de
 *              conception) -- persisté tel quel dans la colonne `composition`
 *              JSONB de la table `cockpits` (api-gateway). `dataTypes`
 *              réutilise directement les clés EQUIPMENT_CATEGORIES du
 *              backend (bim_graph_ingestion_service.py), `calcul` les clés
 *              de _AGGREGATORS (api_server.py) -- aucune table de
 *              correspondance à maintenir entre frontend et backend.
 */

export type PeriodPreset = 'jour' | 'semaine' | 'mois' | 'annee'

export type SpaceLevel = 'batiment' | 'etage' | 'typologie' | 'piece'

export type WidgetType = 'barres' | 'courbe' | 'camembert' | 'semaine_type' | 'indicateur'

export type Calcul = 'sum' | 'avg' | 'max' | 'min'

export type Interval = 'heure' | 'jour' | 'semaine' | 'mois'

export type DisplayUnit = 'raw' | 'per_m2'

export interface CockpitSpace {
  level: SpaceLevel
  ids: string[] // 1 id sauf typologie (N room ids résolus via getRoomsByTypology)
  label: string
  typologyName: string | null
}

export interface CockpitPeriod {
  preset: PeriodPreset
}

export interface CockpitWidget {
  id: string
  type: WidgetType
  title: string
  metric: string // ex: power_instant_kw, temperature, co2... -- une métrique EquipmentReading
  dataTypes: string[] // sous-ensemble des 6 clés EQUIPMENT_CATEGORIES -- [] = toutes
  calcul: Calcul
  interval: Interval
  unit: DisplayUnit
  // Widgets 'indicateur' uniquement, tous deux optionnels/indépendants :
  target?: number | null // objectif/budget -- affiche l'écart en % sous la valeur
  targetEquipmentId?: string | null // équipement précis ciblé -- remplace la lecture par espace (space.ids)
}

export interface CockpitComposition {
  version: 1
  period: CockpitPeriod
  comparePeriods: CockpitPeriod[] // réservé, toujours vide en v1 (voir plan -- "Comparer une période" déféré)
  space: CockpitSpace
  widgets: CockpitWidget[]
}

export interface CockpitMeta {
  id?: string
  name: string
  description: string
  category: string
  tags: string[]
  owner_username?: string
}

// Libellés FR pour les 6 catégories EQUIPMENT_CATEGORIES du backend --
// simple copie UI d'un vocabulaire déjà fixé côté ingestion, pas une
// nouvelle taxonomie inventée ici.
export const CATEGORY_LABELS: Record<string, string> = {
  cvc: 'CVC / Équipements généraux',
  electricity: 'Électricité',
  security: 'Sécurité',
  sanitary: 'Sanitaire',
  furniture: 'Mobilier',
  controls: 'Automates / GTB',
}

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category
}

let widgetCounter = 0
export function makeWidgetId(): string {
  widgetCounter += 1
  return `w${Date.now()}_${widgetCounter}`
}

export const DEFAULT_METRIC = 'power_instant_kw'

export function defaultComposition(spaceLevel: SpaceLevel, spaceIds: string[], spaceLabel: string): CockpitComposition {
  return {
    version: 1,
    period: { preset: 'semaine' },
    comparePeriods: [],
    space: { level: spaceLevel, ids: spaceIds, label: spaceLabel, typologyName: null },
    widgets: [],
  }
}

export function defaultWidget(type: WidgetType, metric = DEFAULT_METRIC): CockpitWidget {
  const titles: Record<WidgetType, string> = {
    indicateur: 'Nouvel indicateur', barres: 'Nouvelles barres', camembert: 'Nouveau camembert',
    semaine_type: 'Nouvelle semaine type', courbe: 'Nouvelle courbe',
  }
  return {
    id: makeWidgetId(), type, title: titles[type], metric,
    dataTypes: [], calcul: 'sum', interval: type === 'courbe' || type === 'semaine_type' ? 'heure' : 'jour',
    unit: 'raw', target: null, targetEquipmentId: null,
  }
}

/**
 * "Consommation électrique" (ancien panneau fixe) recréé comme composition
 * -- épinglé dans la bibliothèque, pas un vrai cockpit possédé tant que
 * l'utilisateur ne l'a pas explicitement enregistré (fork-on-save).
 * dataTypes: [] partout = "tous les équipements", comportement identique à
 * l'ancien panneau (aucun filtre catégorie).
 */
export function legacySeed(buildingId: string, buildingLabel: string): CockpitComposition {
  return {
    version: 1,
    period: { preset: 'semaine' },
    comparePeriods: [],
    space: { level: 'batiment', ids: [buildingId], label: buildingLabel, typologyName: null },
    widgets: [
      { ...defaultWidget('indicateur'), title: 'Consommation totale' },
      { ...defaultWidget('barres'), title: 'Par étage' },
      { ...defaultWidget('camembert'), title: 'Répartition' },
      { ...defaultWidget('courbe'), title: 'Tendance récente' },
    ],
  }
}
