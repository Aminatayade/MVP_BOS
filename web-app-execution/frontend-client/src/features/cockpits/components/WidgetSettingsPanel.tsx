/**
 * FILE: features/cockpits/components/WidgetSettingsPanel.tsx
 * DESCRIPTION: Panneau de réglages d'UN widget, ouvert en overlay in-place
 *              au clic sur ⚙️ dans WidgetBlock.tsx (pas une modale séparée).
 */
import { useEffect, useState } from 'react'
import { getEquipmentList, getEquipmentSummary, type EquipmentListItem, type EquipmentMetricSummary } from '../../bim/services/arcadeApi'
import { categoryLabel, type Calcul, type CockpitWidget, type DisplayUnit, type Interval } from '../types'

type Props = {
  widget: CockpitWidget
  availableCategories: string[]
  spaceIds: string[]
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

export default function WidgetSettingsPanel({ widget, availableCategories, spaceIds, onChange, onClose }: Props) {
  const toggleDataType = (cat: string) => {
    const has = widget.dataTypes.includes(cat)
    onChange({ ...widget, dataTypes: has ? widget.dataTypes.filter((c) => c !== cat) : [...widget.dataTypes, cat] })
  }

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
    getEquipmentList(spaceIds)
      .then((list) => { if (!cancelled) setEquipmentOptions(list) })
      .catch(() => { if (!cancelled) setEquipmentOptions([]) })
    return () => { cancelled = true }
  }, [isIndicateur, spaceIds.join(',')])

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
