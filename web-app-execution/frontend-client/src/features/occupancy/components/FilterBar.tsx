/**
 * FILE: features/occupancy/components/FilterBar.tsx
 * DESCRIPTION: Barre de filtres du cockpit occupation. La plage horaire est
 *              un choix parmi 3 préréglages fixes convenus avec le client
 *              (PAS un sélecteur d'heure libre, cf. spec). Le bouton
 *              "Comparer 2 périodes" révèle un décalage (en unités de la
 *              période choisie) pour la seconde série superposée sur les
 *              barres -- ex. période=mois, décalage=-12 -> même mois l'an
 *              dernier.
 */
import type { CSSProperties } from 'react'
import type { Floor } from '../../bim/services/arcadeApi'
import type { OccupancyPeriod } from '../period'

export type FilterValues = {
  plage: string
  typeEspace: string
  etage: string
  periode: OccupancyPeriod
  offset: number
  seuilBas: number
  seuilHaut: number
  compareEnabled: boolean
  compareOffset: number
}

type Props = {
  floors: Floor[]
  values: FilterValues
  onChange: (patch: Partial<FilterValues>) => void
  onSaveSeuils: (seuilBas: number, seuilHaut: number) => void
}

const selectStyle: CSSProperties = {
  flex: '1 1 200px', padding: '10px 14px', borderRadius: 8,
  border: '1px solid var(--border)', background: 'var(--bg)',
  color: 'var(--text-h)', fontSize: 14, fontFamily: 'var(--sans)',
}

const PLAGES = [
  { value: '7-19', label: '7h - 19h' },
  { value: '7-12', label: '7h - 12h' },
  { value: '12-19', label: '12h - 19h' },
]

const TYPES_ESPACE = [
  { value: 'salle_reunion', label: 'Salle de réunion' },
  { value: 'poste_travail', label: 'Poste de travail' },
  { value: 'bulle', label: 'Bulle' },
]

const PERIODES: { value: OccupancyPeriod; label: string }[] = [
  { value: 'jour', label: 'Jour' },
  { value: 'semaine', label: 'Semaine' },
  { value: 'mois', label: 'Mois' },
  { value: 'trimestre', label: 'Trimestre' },
  { value: 'annee', label: 'Année' },
]

export default function FilterBar({ floors, values, onChange, onSaveSeuils }: Props) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 14 }}>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <select style={selectStyle} value={values.plage} onChange={(e) => onChange({ plage: e.target.value })}>
          {PLAGES.map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </select>

        <select style={selectStyle} value={values.typeEspace} onChange={(e) => onChange({ typeEspace: e.target.value })}>
          <option value="">Tout type d'espace</option>
          {TYPES_ESPACE.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>

        <select style={selectStyle} value={values.etage} onChange={(e) => onChange({ etage: e.target.value })}>
          <option value="">Tout le bâtiment</option>
          {floors.map((f) => (
            <option key={f.global_id} value={f.global_id}>{f.name}</option>
          ))}
        </select>

        <select
          style={selectStyle}
          value={values.periode}
          onChange={(e) => onChange({ periode: e.target.value as OccupancyPeriod, offset: 0 })}
        >
          {PERIODES.map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </select>

        <button type="button" className="btn btn-secondary" onClick={() => onChange({ offset: values.offset - 1 })}>
          ← Période précédente
        </button>
        <button type="button" className="btn btn-secondary" disabled={values.offset >= 0} onClick={() => onChange({ offset: values.offset + 1 })}>
          Période suivante →
        </button>
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)' }}>
          Seuil bas (sous-utilisé, %)
          <input
            type="number"
            min={0}
            max={100}
            value={values.seuilBas}
            onChange={(e) => onChange({ seuilBas: Number(e.target.value) })}
            onBlur={() => onSaveSeuils(values.seuilBas, values.seuilHaut)}
            style={{ width: 70, padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text-h)' }}
          />
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)' }}>
          Seuil haut (saturé, %)
          <input
            type="number"
            min={0}
            max={100}
            value={values.seuilHaut}
            onChange={(e) => onChange({ seuilHaut: Number(e.target.value) })}
            onBlur={() => onSaveSeuils(values.seuilBas, values.seuilHaut)}
            style={{ width: 70, padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text-h)' }}
          />
        </label>

        <button
          type="button"
          className={values.compareEnabled ? 'btn btn-primary' : 'btn btn-secondary'}
          onClick={() => onChange({ compareEnabled: !values.compareEnabled })}
        >
          Comparer 2 périodes
        </button>

        {values.compareEnabled && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)' }}>
            Décalage (ex. -12 = même {values.periode} l'an dernier)
            <input
              type="number"
              value={values.compareOffset}
              onChange={(e) => onChange({ compareOffset: Number(e.target.value) })}
              style={{ width: 70, padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text-h)' }}
            />
          </label>
        )}
      </div>
    </div>
  )
}
