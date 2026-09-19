/**
 * FILE: features/shared/ZonePicker.tsx
 * DESCRIPTION: Sélecteur de zone/bâtiment réutilisé par les apps BOS Exécution
 *              (Jumeau numérique, Cockpit Énergie). Lecture seule : liste les
 *              zones déjà ingérées côté Configurateur via getZones(), pas de
 *              création de zone ici (contrairement au Configurateur).
 */
import { useEffect, useState } from 'react'
import { getZones } from '../bim/services/arcadeApi'

type Props = {
  zone: string
  onZoneChange: (zone: string) => void
  onOpen: () => void
  loading: boolean
  label?: string
}

export default function ZonePicker({ zone, onZoneChange, onOpen, loading, label = 'Afficher' }: Props) {
  const [knownZones, setKnownZones] = useState<string[]>([])

  useEffect(() => {
    getZones().then(setKnownZones).catch(() => setKnownZones([]))
  }, [])

  return (
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
      <select
        value={zone}
        onChange={(event) => onZoneChange(event.target.value)}
        style={{
          flex: '1 1 220px', padding: '10px 14px', borderRadius: 8,
          border: '1px solid var(--border)', background: 'var(--bg)',
          color: 'var(--text-h)', fontSize: 14, fontFamily: 'var(--sans)',
        }}
      >
        <option value="">Choisir une zone...</option>
        {knownZones.map((z) => (
          <option key={z} value={z}>{z}</option>
        ))}
      </select>
      <button type="button" className="btn btn-primary" onClick={onOpen} disabled={loading || !zone}>
        {loading ? 'Chargement…' : label}
      </button>
    </div>
  )
}
