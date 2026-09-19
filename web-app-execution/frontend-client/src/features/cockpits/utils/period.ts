/**
 * FILE: features/cockpits/utils/period.ts
 * DESCRIPTION: Conversion des réglages "Période" (schéma CockpitComposition)
 *              vers ce qu'attendent les endpoints bim_api : `start`/`end`
 *              (epoch ms, /graph/location-readings-summary et, depuis
 *              l'ajout de la pagination "Mois précédent/suivant",
 *              /graph/location-readings aussi).
 *
 *              Bornage CALENDAIRE (jour/semaine/mois/année civils en UTC),
 *              pas une fenêtre glissante depuis "maintenant" -- nécessaire
 *              pour que "Mois précédent" pointe vers un mois exact et pas
 *              vers "il y a 30 à 60 jours". `offset` décale d'unités
 *              calendaires entières (0 = période en cours, -1 = précédente,
 *              jamais positif : on ne navigue jamais dans le futur). UTC
 *              plutôt que le fuseau du navigateur : même raisonnement que
 *              typicalWeek.ts, évite tout décalage de bord de journée selon
 *              où tourne le navigateur.
 */
import type { Interval, PeriodPreset } from '../types'

export interface PeriodRange {
  startMs: number
  endMs: number
}

const WEEKDAY_LABELS_FR = [
  'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
  'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre',
]

export function periodRange(preset: PeriodPreset, offset: number, now: number = Date.now()): PeriodRange {
  const d = new Date(now)
  if (preset === 'jour') {
    const start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + offset)
    return { startMs: start, endMs: start + 24 * 60 * 60 * 1000 }
  }
  if (preset === 'semaine') {
    // Lundi comme premier jour -- getUTCDay() : 0=Dimanche.
    const mondayOffset = (d.getUTCDay() + 6) % 7
    const startOfThisWeek = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - mondayOffset)
    const start = startOfThisWeek + offset * 7 * 24 * 60 * 60 * 1000
    return { startMs: start, endMs: start + 7 * 24 * 60 * 60 * 1000 }
  }
  if (preset === 'mois') {
    const start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + offset, 1)
    const end = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + offset + 1, 1)
    return { startMs: start, endMs: end }
  }
  // 'annee'
  const start = Date.UTC(d.getUTCFullYear() + offset, 0, 1)
  const end = Date.UTC(d.getUTCFullYear() + offset + 1, 0, 1)
  return { startMs: start, endMs: end }
}

/** Libellé court affiché entre les boutons Précédent/Suivant. */
export function formatPeriodLabel(preset: PeriodPreset, offset: number, now: number = Date.now()): string {
  const { startMs } = periodRange(preset, offset, now)
  const d = new Date(startMs)
  if (preset === 'jour') {
    return offset === 0 ? "Aujourd'hui" : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', timeZone: 'UTC' })
  }
  if (preset === 'semaine') {
    const endD = new Date(startMs + 6 * 24 * 60 * 60 * 1000)
    return `${d.getUTCDate()} ${WEEKDAY_LABELS_FR[d.getUTCMonth()].slice(0, 3)} - ${endD.getUTCDate()} ${WEEKDAY_LABELS_FR[endD.getUTCMonth()].slice(0, 3)}`
  }
  if (preset === 'mois') {
    return `${WEEKDAY_LABELS_FR[d.getUTCMonth()]} ${d.getUTCFullYear()}`
  }
  return `${d.getUTCFullYear()}`
}

const INTERVAL_SECONDS: Record<Interval, number> = {
  heure: 3600,
  jour: 86400,
  semaine: 604800,
  mois: 2592000,
}

export function intervalToBucketSeconds(interval: Interval): number {
  return INTERVAL_SECONDS[interval]
}
