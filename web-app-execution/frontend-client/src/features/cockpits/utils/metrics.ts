/**
 * FILE: features/cockpits/utils/metrics.ts
 * DESCRIPTION: Dérive les métriques RÉELLEMENT disponibles (avec données)
 *              pour un sous-ensemble de catégories -- factorisé pour être
 *              utilisé À LA FOIS par WidgetSettingsPanel.tsx (menu
 *              "Métrique") et CockpitBuilder.tsx (choix du métier par
 *              défaut d'un nouveau widget), pour ne jamais avoir deux
 *              logiques différentes qui pourraient diverger.
 */
import type { EquipmentCategoryScope } from '../../bim/services/arcadeApi'

export function availableMetricsForScope(
  scope: EquipmentCategoryScope[],
  dataTypes: string[],
): { name: string; unit: string }[] {
  const relevant = dataTypes.length === 0 ? scope : scope.filter((c) => dataTypes.includes(c.category))
  return Array.from(
    new Map(relevant.flatMap((c) => c.metrics).map((m) => [m.name, m])).values(),
  ).sort((a, b) => a.name.localeCompare(b.name))
}
