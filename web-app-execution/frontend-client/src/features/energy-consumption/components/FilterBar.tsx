/**
 * FILE: features/energy-consumption/components/FilterBar.tsx
 * DESCRIPTION: Barre de filtres du cockpit "Consommation énergétique" --
 *              plage de temps (mois actuel/antérieur, navigation
 *              précédent/suivant), plage horaire (3 préréglages fixes
 *              convenus avec le client, PAS un sélecteur d'heure libre),
 *              type de consommation, sous-catégorie. Pas de filtre
 *              zone/étage : ce cockpit est toujours scopé à la zone entière
 *              (spec client).
 */
import type { CSSProperties } from 'react'
import { formatPeriodLabel } from '../../cockpits/utils/period'
import { SUBCATEGORY_LABELS } from '../colors'

export type FilterValues = {
  offset: number
  plage: string
  typeConsommation: string
  sousCategorie: string
}

type Props = {
  values: FilterValues
  onChange: (patch: Partial<FilterValues>) => void
}

const selectStyle: CSSProperties = {
  flex: '1 1 200px', padding: '10px 14px', borderRadius: 8,
  border: '1px solid var(--border)', background: 'var(--bg)',
  color: 'var(--text-h)', fontSize: 14, fontFamily: 'var(--sans)',
}

const PLAGES = [
  { value: 'toute_journee', label: 'Toute la journée' },
  { value: 'ouvrable', label: 'Période ouvrable (8h-19h)' },
  { value: 'hors_ouvrable', label: 'Hors période ouvrable' },
]

const TYPES_CONSOMMATION = [
  { value: 'total', label: 'Total' },
  { value: 'electrique', label: 'Électrique' },
  { value: 'thermique', label: 'Thermique' },
  { value: 'fioul', label: 'Fioul' },
]

const SOUS_CATEGORIES = Object.entries(SUBCATEGORY_LABELS)

export default function FilterBar({ values, onChange }: Props) {
  return (
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14, alignItems: 'center' }}>
      <button type="button" className="btn btn-secondary" onClick={() => onChange({ offset: values.offset - 1 })}>
        ← Mois précédent
      </button>
      <strong style={{ fontSize: 14, color: 'var(--text-h)', minWidth: 140, textAlign: 'center' }}>
        {formatPeriodLabel('mois', values.offset)}
      </strong>
      <button
        type="button"
        className="btn btn-secondary"
        disabled={values.offset >= 0}
        onClick={() => onChange({ offset: values.offset + 1 })}
      >
        Mois suivant →
      </button>

      <select style={selectStyle} value={values.plage} onChange={(e) => onChange({ plage: e.target.value })}>
        {PLAGES.map((p) => (
          <option key={p.value} value={p.value}>{p.label}</option>
        ))}
      </select>

      <select
        style={selectStyle}
        value={values.typeConsommation}
        onChange={(e) => onChange({ typeConsommation: e.target.value })}
      >
        {TYPES_CONSOMMATION.map((t) => (
          <option key={t.value} value={t.value}>{t.label}</option>
        ))}
      </select>

      <select
        style={selectStyle}
        value={values.sousCategorie}
        onChange={(e) => onChange({ sousCategorie: e.target.value })}
      >
        <option value="">Toutes sous-catégories</option>
        {SOUS_CATEGORIES.map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </select>
    </div>
  )
}
