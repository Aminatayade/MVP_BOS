/**
 * FILE: features/cockpits/components/WidgetSettingsPanel.tsx
 * DESCRIPTION: Panneau de réglages d'UN widget, ouvert en overlay in-place
 *              au clic sur ⚙️ dans WidgetBlock.tsx (pas une modale séparée).
 */
import { useEffect, useState } from 'react'
import {
  getEquipmentList, getEquipmentSummary,
  type EquipmentCategoryScope, type EquipmentListItem, type EquipmentMetricSummary,
} from '../../bim/services/arcadeApi'
import { categoryLabel, type Calcul, type CockpitWidget, type DisplayUnit, type Interval } from '../types'
import { availableMetricsForScope } from '../utils/metrics'

type Props = {
  widget: CockpitWidget
  scope: EquipmentCategoryScope[]
  spaceIds: string[]
  zone: string
  onChange: (widget: CockpitWidget) => void
  onClose: () => void
}

const CALCUL_OPTIONS: { value: Calcul; label: string }[] = [
  { value: 'sum', label: 'Somme' },
  { value: 'avg', label: 'Moyenne' },
  { value: 'max', label: 'Maximum' },
  { value: 'min', label: 'Minimum' },
]

const INTERVAL_OPTIONS: { value: Interval; label: string }[] = [
  { value: 'heure', label: 'Heure' },
  { value: 'jour', label: 'Jour' },
  { value: 'semaine', label: 'Semaine' },
  { value: 'mois', label: 'Mois' },
]

export default function WidgetSettingsPanel({ widget, scope, spaceIds, zone, onChange, onClose }: Props) {
  const availableCategories = scope.map((c) => c.category).filter((c) => c !== '_unknown')

  const toggleDataType = (cat: string) => {
    const has = widget.dataTypes.includes(cat)
    onChange({ ...widget, dataTypes: has ? widget.dataTypes.filter((c) => c !== cat) : [...widget.dataTypes, cat] })
  }

  // Métriques RÉELLEMENT disponibles pour les données affichées du widget --
  // dataTypes vide ("toutes") = union sur toutes les catégories du
  // périmètre ; sinon restreint aux catégories cochées. Sans ça, le champ
  // metric restait figé sur DEFAULT_METRIC ("power_instant_kw") pour tout
  // widget scopé à un espace, quel que soit le bâtiment -- un bâtiment sans
  // compteur électrique affichait alors silencieusement 0 partout, sans
  // aucun moyen de choisir une métrique qui a réellement des données
  // (ex: débit_instant_m3h, température).
  const availableMetrics = availableMetricsForScope(scope, widget.dataTypes)

  // Si widget.metric ne correspond à AUCUNE option de la liste (ex: encore
  // DEFAULT_METRIC "power_instant_kw" alors que ce bâtiment n'a aucune
  // donnée pour cette métrique), un <select> React affiche silencieusement
  // la PREMIÈRE option comme "sélectionnée" sans jamais déclencher onChange
  // -- l'utilisateur voit une métrique cohérente à l'écran, mais widget.metric
  // reste inchangé et le widget continue de charger l'ancienne métrique sans
  // données (piège constaté : "j'ai bien choisi débit mais c'est toujours
  // vide", alors qu'aucune requête avec ce metric n'était jamais partie).
  // On resynchronise explicitement dès que la liste réelle ne contient plus
  // la valeur actuelle, plutôt que de compter sur ce comportement implicite.
  useEffect(() => {
    if (widget.targetEquipmentId) return
    if (availableMetrics.length === 0) return
    if (availableMetrics.some((m) => m.name === widget.metric)) return
    onChange({ ...widget, metric: availableMetrics[0].name })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widget.metric, widget.targetEquipmentId, availableMetrics.map((m) => m.name).join(',')])

  const isIndicateur = widget.type === 'indicateur'
  const [equipmentOptions, setEquipmentOptions] = useState<EquipmentListItem[]>([])
  const [equipmentMetrics, setEquipmentMetrics] = useState<EquipmentMetricSummary[]>([])

  // Liste des équipements du périmètre actuel -- chargée uniquement pour un
  // widget Indicateur (seul type qui peut cibler un équipement précis).
  useEffect(() => {
    if (!isIndicateur || spaceIds.length === 0) {
      setEquipmentOptions([])
      return
    }
    let cancelled = false
    getEquipmentList(zone, spaceIds)
      .then((list) => { if (!cancelled) setEquipmentOptions(list) })
      .catch(() => { if (!cancelled) setEquipmentOptions([]) })
    return () => { cancelled = true }
  }, [isIndicateur, spaceIds.join(','), zone])

  // Métriques disponibles pour l'équipement actuellement ciblé -- peuple le
  // second sélecteur "Quelle mesure ?" (chaque type d'équipement du
  // catalogue de simulation a son propre jeu de métriques, ex: un automate
  // n'a que "state", une sonde n'a que "temperature").
  useEffect(() => {
    if (!widget.targetEquipmentId) {
      setEquipmentMetrics([])
      return
    }
    let cancelled = false
    getEquipmentSummary(widget.targetEquipmentId)
      .then((summary) => { if (!cancelled) setEquipmentMetrics(summary.metrics) })
      .catch(() => { if (!cancelled) setEquipmentMetrics([]) })
    return () => { cancelled = true }
  }, [widget.targetEquipmentId])

  const selectEquipment = (equipmentId: string) => {
    if (!equipmentId) {
      onChange({ ...widget, targetEquipmentId: null })
      return
    }
    onChange({ ...widget, targetEquipmentId: equipmentId })
  }

  return (
    <div
      style={{
        position: 'absolute', inset: 0, background: 'var(--bg)', border: '1px solid var(--border)',
        borderRadius: 12, padding: 14, zIndex: 5, overflowY: 'auto',
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Réglages du widget</strong>
        <button type="button" onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 16 }}>✕</button>
      </div>

      <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>Titre</label>
      <input
        type="text" value={widget.title} onChange={(e) => onChange({ ...widget, title: e.target.value })}
        className="input" style={{ width: '100%', marginBottom: 12, fontSize: 12, padding: '6px 8px' }}
      />

      {isIndicateur && (
        <>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>
            Cibler un équipement précis (optionnel)
          </label>
          <select
            className="input" style={{ width: '100%', marginBottom: 4, fontSize: 12, padding: '6px 8px' }}
            value={widget.targetEquipmentId ?? ''}
            onChange={(e) => selectEquipment(e.target.value)}
          >
            <option value="">— Utiliser l'espace sélectionné —</option>
            {equipmentOptions.map((eq) => (
              <option key={eq.global_id} value={eq.global_id}>{eq.name}</option>
            ))}
          </select>
          {widget.targetEquipmentId && (
            <>
              <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginTop: 8, marginBottom: 4 }}>
                Quelle mesure ?
              </label>
              <select
                className="input" style={{ width: '100%', marginBottom: 12, fontSize: 12, padding: '6px 8px' }}
                value={widget.metric}
                onChange={(e) => onChange({ ...widget, metric: e.target.value })}
              >
                {equipmentMetrics.length === 0 && <option value={widget.metric}>{widget.metric}</option>}
                {equipmentMetrics.map((m) => (
                  <option key={m.metric} value={m.metric}>{m.metric} ({m.unit})</option>
                ))}
              </select>
            </>
          )}
          {!widget.targetEquipmentId && <div style={{ marginBottom: 12 }} />}
        </>
      )}

      {!widget.targetEquipmentId && (
        <>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>
            Données affichées {widget.dataTypes.length === 0 && '(toutes)'}
          </label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 12 }}>
            {availableCategories.length === 0 && (
              <span className="panel-hint" style={{ margin: 0, fontSize: 11 }}>Aucune donnée disponible pour cet espace.</span>
            )}
            {availableCategories.map((cat) => (
              <label key={cat} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                <input type="checkbox" checked={widget.dataTypes.includes(cat)} onChange={() => toggleDataType(cat)} />
                {categoryLabel(cat)}
              </label>
            ))}
          </div>

          <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>Métrique</label>
          <select
            className="input" style={{ width: '100%', marginBottom: 4, fontSize: 12, padding: '6px 8px' }}
            value={widget.metric}
            onChange={(e) => onChange({ ...widget, metric: e.target.value })}
          >
            {availableMetrics.length === 0 && <option value={widget.metric}>{widget.metric}</option>}
            {availableMetrics.map((m) => (
              <option key={m.name} value={m.name}>{m.name}{m.unit ? ` (${m.unit})` : ''}</option>
            ))}
          </select>
          <p className="panel-hint" style={{ fontSize: 10.5, marginBottom: 12 }}>
            Seules les métriques ayant réellement des données pour les "Données affichées" ci-dessus sont proposées.
          </p>
        </>
      )}

      {widget.type === 'courbe' && (
        <>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>Style d'affichage</label>
          <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
              <input
                type="radio" name={`chart-style-${widget.id}`} checked={(widget.chartStyle ?? 'ligne') === 'ligne'}
                onChange={() => onChange({ ...widget, chartStyle: 'ligne' })}
              />
              Ligne
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
              <input
                type="radio" name={`chart-style-${widget.id}`} checked={widget.chartStyle === 'barres'}
                onChange={() => onChange({ ...widget, chartStyle: 'barres' })}
              />
              Barres (une par période, façon "Répartition Énergies")
            </label>
          </div>
        </>
      )}

      <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>Méthode de calcul</label>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        {CALCUL_OPTIONS.map((opt) => (
          <label key={opt.value} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
            <input type="radio" name={`calcul-${widget.id}`} checked={widget.calcul === opt.value} onChange={() => onChange({ ...widget, calcul: opt.value })} />
            {opt.label}
          </label>
        ))}
      </div>

      <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>Intervalle de temps interne</label>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        {INTERVAL_OPTIONS.map((opt) => (
          <label key={opt.value} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
            <input type="radio" name={`interval-${widget.id}`} checked={widget.interval === opt.value} onChange={() => onChange({ ...widget, interval: opt.value })} />
            {opt.label}
          </label>
        ))}
      </div>

      <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>Unité d'affichage</label>
      <div style={{ display: 'flex', gap: 10 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
          <input
            type="radio" name={`unit-${widget.id}`} checked={widget.unit === 'raw'}
            onChange={() => onChange({ ...widget, unit: 'raw' as DisplayUnit })}
          />
          Valeur brute
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, opacity: 0.5 }} title="Aucune donnée de surface (m²) n'est encore capturée à l'ingestion des maquettes -- bascule réservée pour une itération future.">
          <input type="radio" disabled />
          Par m² (bientôt disponible)
        </label>
      </div>

      {isIndicateur && !widget.targetEquipmentId && (
        <>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginTop: 12, marginBottom: 4 }}>
            Objectif / budget (optionnel)
          </label>
          <input
            type="number"
            value={widget.target ?? ''}
            onChange={(e) => onChange({ ...widget, target: e.target.value === '' ? null : Number(e.target.value) })}
            className="input" style={{ width: '100%', fontSize: 12, padding: '6px 8px' }}
            placeholder="ex: 1500"
          />
          <p className="panel-hint" style={{ fontSize: 10.5, marginTop: 4 }}>
            Affiche l'écart en % sous la valeur (ex: consommation du mois vs budget).
          </p>
        </>
      )}
    </div>
  )
}
