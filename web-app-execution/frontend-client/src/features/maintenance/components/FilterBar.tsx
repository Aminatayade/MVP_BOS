/**
 * FILE: features/maintenance/components/FilterBar.tsx
 * DESCRIPTION: Barre de filtres du cockpit maintenance. Le filtre "Métier"
 *              est une cascade à 3 niveaux (métier -> sous-catégorie ->
 *              prestataire), PAS un select plat : le prestataire est un
 *              badge en lecture seule dérivé de l'arbre déjà chargé (aucune
 *              requête supplémentaire), affiché seulement quand les deux
 *              premiers niveaux sont renseignés.
 */
import type { CSSProperties } from 'react'
import type { MaintenanceFilters } from '../services/maintenanceApi'

export type FilterValues = {
  metier: string
  sousCategorie: string
  statutWorkflow: string
  priorite: string
  etage: string
  periode: string
}

type Props = {
  filters: MaintenanceFilters
  values: FilterValues
  onChange: (patch: Partial<FilterValues>) => void
}

const selectStyle: CSSProperties = {
  flex: '1 1 200px', padding: '10px 14px', borderRadius: 8,
  border: '1px solid var(--border)', background: 'var(--bg)',
  color: 'var(--text-h)', fontSize: 14, fontFamily: 'var(--sans)',
}

export default function FilterBar({ filters, values, onChange }: Props) {
  const selectedMetier = filters.metiers.find((m) => m.metier === values.metier)
  const selectedSousCategorie = selectedMetier?.sous_categories.find((sc) => sc.sous_categorie === values.sousCategorie)

  return (
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
      <select
        style={selectStyle}
        value={values.metier}
        onChange={(e) => onChange({ metier: e.target.value, sousCategorie: '' })}
      >
        <option value="">Tout métier</option>
        {filters.metiers.map((m) => (
          <option key={m.metier} value={m.metier}>{m.metier}</option>
        ))}
      </select>

      <select
        style={selectStyle}
        value={values.sousCategorie}
        disabled={!selectedMetier}
        onChange={(e) => onChange({ sousCategorie: e.target.value })}
      >
        <option value="">{selectedMetier ? 'Toute sous-catégorie' : 'Choisir un métier d\'abord'}</option>
        {selectedMetier?.sous_categories.map((sc) => (
          <option key={sc.sous_categorie} value={sc.sous_categorie}>{sc.sous_categorie}</option>
        ))}
      </select>

      {selectedSousCategorie && (
        <span
          style={{
            display: 'flex', alignItems: 'center', padding: '0 14px', borderRadius: 8,
            background: 'var(--accent-bg)', color: 'var(--accent)', fontSize: 13, fontWeight: 600,
          }}
        >
          Prestataire : {selectedSousCategorie.prestataire}
        </span>
      )}

      <select style={selectStyle} value={values.statutWorkflow} onChange={(e) => onChange({ statutWorkflow: e.target.value })}>
        <option value="">Tout workflow</option>
        <option value="en_cours">En cours</option>
        <option value="traite">Traité</option>
      </select>

      <select style={selectStyle} value={values.priorite} onChange={(e) => onChange({ priorite: e.target.value })}>
        <option value="">Toute priorité</option>
        <option value="elevee">Élevée</option>
        <option value="moyenne">Moyenne</option>
        <option value="basse">Basse</option>
      </select>

      <select style={selectStyle} value={values.etage} onChange={(e) => onChange({ etage: e.target.value })}>
        <option value="">Tout le bâtiment</option>
        {filters.etages.map((e) => (
          <option key={e} value={e}>{e}</option>
        ))}
      </select>

      <select style={selectStyle} value={values.periode} onChange={(e) => onChange({ periode: e.target.value })}>
        <option value="temps_reel">Temps réel</option>
        <option value="jour">Jour</option>
        <option value="semaine">Semaine</option>
        <option value="mois">Mois</option>
        <option value="trimestre">Trimestre</option>
        <option value="annee">Année</option>
      </select>
    </div>
  )
}
