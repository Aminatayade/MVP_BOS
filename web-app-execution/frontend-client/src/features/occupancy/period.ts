/**
 * FILE: features/occupancy/period.ts
 * DESCRIPTION: Bornage calendaire (UTC) jour/semaine/mois/trimestre/année
 *              pour le cockpit occupation -- même logique que
 *              features/cockpits/utils/period.ts (offset=0 période en
 *              cours, offset=-1 précédente, jamais positif), étendue avec
 *              "trimestre" (absent de l'utilitaire cockpits, propre aux
 *              cockpits énergie). Copie plutôt qu'extension du fichier
 *              partagé : éviter de coupler ce module au schéma de
 *              composition des cockpits énergie (PeriodPreset y est un type
 *              persisté en base, pas une simple énumération locale).
 */
export type OccupancyPeriod = 'jour' | 'semaine' | 'mois' | 'trimestre' | 'annee'

export interface PeriodRange {
  startMs: number
  endMs: number
}

export function periodRange(preset: OccupancyPeriod, offset: number, now: number = Date.now()): PeriodRange {
  const d = new Date(now)
  if (preset === 'jour') {
    const start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + offset)
    return { startMs: start, endMs: start + 24 * 60 * 60 * 1000 }
  }
  if (preset === 'semaine') {
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
  if (preset === 'trimestre') {
    const quarterStartMonth = Math.floor(d.getUTCMonth() / 3) * 3
    const start = Date.UTC(d.getUTCFullYear(), quarterStartMonth + offset * 3, 1)
    const end = Date.UTC(d.getUTCFullYear(), quarterStartMonth + offset * 3 + 3, 1)
    return { startMs: start, endMs: end }
  }
  // 'annee'
  const start = Date.UTC(d.getUTCFullYear() + offset, 0, 1)
  const end = Date.UTC(d.getUTCFullYear() + offset + 1, 0, 1)
  return { startMs: start, endMs: end }
}

const LABELS_FR = [
  'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
  'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre',
]

/** Libellé court affiché entre les boutons Précédent/Suivant. */
export function formatPeriodLabel(preset: OccupancyPeriod, offset: number, now: number = Date.now()): string {
  const { startMs } = periodRange(preset, offset, now)
  const d = new Date(startMs)
  if (preset === 'jour') {
    return offset === 0 ? "Aujourd'hui" : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', timeZone: 'UTC' })
  }
  if (preset === 'semaine') {
    const endD = new Date(startMs + 6 * 24 * 60 * 60 * 1000)
    return `${d.getUTCDate()} ${LABELS_FR[d.getUTCMonth()].slice(0, 3)} - ${endD.getUTCDate()} ${LABELS_FR[endD.getUTCMonth()].slice(0, 3)}`
  }
  if (preset === 'mois') {
    return `${LABELS_FR[d.getUTCMonth()]} ${d.getUTCFullYear()}`
  }
  if (preset === 'trimestre') {
    const quarter = Math.floor(d.getUTCMonth() / 3) + 1
    return `T${quarter} ${d.getUTCFullYear()}`
  }
  return `${d.getUTCFullYear()}`
}
