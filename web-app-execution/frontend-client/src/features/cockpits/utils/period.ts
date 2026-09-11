/**
 * FILE: features/cockpits/utils/period.ts
 * DESCRIPTION: Conversion des réglages "Période"/"Intervalle" (schéma
 *              CockpitComposition) vers ce qu'attendent les endpoints
 *              bim_api : `start` (epoch ms, /graph/location-readings-summary)
 *              et `bucket_seconds` (/graph/location-readings). Fenêtres
 *              glissantes simples (N jours en arrière depuis maintenant),
 *              pas de bornage calendaire (mois/année civils) -- suffisant
 *              pour cette itération, pas de logique de fuseau horaire à
 *              maintenir.
 */
import type { Interval, PeriodPreset } from '../types'

const PERIOD_MS: Record<PeriodPreset, number> = {
  jour: 24 * 60 * 60 * 1000,
  semaine: 7 * 24 * 60 * 60 * 1000,
  mois: 30 * 24 * 60 * 60 * 1000,
  annee: 365 * 24 * 60 * 60 * 1000,
}

export function periodStartMs(preset: PeriodPreset, now: number = Date.now()): number {
  return now - PERIOD_MS[preset]
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
