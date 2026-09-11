/**
 * FILE: features/cockpits/utils/typicalWeek.ts
 * DESCRIPTION: Widget "Semaine type" -- moyenne par (jour de semaine, heure)
 *              sur plusieurs semaines d'historique. Aucun mode d'agrégation
 *              backend n'existe pour ça (_bucket_and_aggregate côté
 *              api_server.py ne bucket que par fenêtre de taille fixe, pas
 *              par modulo jour-de-semaine) -- calculé ici côté client à
 *              partir de points bruts/1h déjà bucketés par
 *              /graph/location-readings, cohérent avec le fait que
 *              ConsumptionWidget.tsx faisait déjà du "façonnage final" de
 *              graphique côté frontend plutôt que côté backend.
 */
import type { ReadingPoint } from '../../bim/services/arcadeApi'

const WEEKDAY_LABELS = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam']

export interface TypicalWeekPoint {
  label: string // ex: "Lun 08h"
  value: number
  sampleCount: number
}

/**
 * `ts` arrive au format "YYYY-MM-DD HH:MM:SS[.mmm]" (voir formatHistoryTick
 * dans l'ancien ConsumptionWidget.tsx) -- découpé par chaîne plutôt que
 * `new Date()` pour éviter tout décalage de fuseau horaire introduit par le
 * navigateur, même motif que côté Configurateur.
 */
function parseWeekdayHour(ts: string): { weekday: number; hour: number } | null {
  const [datePart, timePart] = ts.split(' ')
  if (!datePart || !timePart) return null
  const [y, m, d] = datePart.split('-').map(Number)
  const hour = Number(timePart.split(':')[0])
  if (!y || !m || !d || Number.isNaN(hour)) return null
  const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay()
  return { weekday, hour }
}

export function computeTypicalWeek(points: ReadingPoint[]): TypicalWeekPoint[] {
  // index = weekday*24 + hour, dans l'ordre Lun->Dim pour un affichage plus
  // naturel qu'en partant de Dimanche.
  const buckets: number[][] = Array.from({ length: 7 * 24 }, () => [])

  for (const point of points) {
    const parsed = parseWeekdayHour(point.ts)
    if (!parsed) continue
    // getUTCDay() : 0=Dimanche -> on décale pour que 0=Lundi.
    const mondayFirst = (parsed.weekday + 6) % 7
    buckets[mondayFirst * 24 + parsed.hour].push(point.value)
  }

  return buckets.map((values, index) => {
    const weekday = Math.floor(index / 24)
    const hour = index % 24
    const label = `${WEEKDAY_LABELS[(weekday + 1) % 7]} ${String(hour).padStart(2, '0')}h`
    const value = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0
    return { label, value: Math.round(value * 100) / 100, sampleCount: values.length }
  })
}
