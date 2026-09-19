/**
 * FILE: features/cockpits/CockpitEnergieApp.tsx
 * DESCRIPTION: App "Cockpit Énergie" de BOS Exécution -- volet pilotage
 *              énergétique isolé du jumeau numérique 3D (voir
 *              features/bim/JumeauNumeriqueApp.tsx), accessible depuis
 *              l'écran d'accueil (features/home/AppLauncher.tsx).
 */
import { useEffect, useState } from 'react'
import { getBuildingGlobalId } from '../bim/services/arcadeApi'
import ZonePicker from '../shared/ZonePicker'
import CockpitLibrary from './CockpitLibrary'
import CockpitBuilder from './CockpitBuilder'
import { defaultComposition, type CockpitComposition, type CockpitMeta } from './types'
import keycloak from '../../services/keycloak'

type EnergyView = 'library' | 'builder'

export default function CockpitEnergieApp() {
  const [zone, setZone] = useState('')
  const [loadedZone, setLoadedZone] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)

  const [buildingId, setBuildingId] = useState<string | null>(null)
  const [energyView, setEnergyView] = useState<EnergyView>('library')
  const [builderSeed, setBuilderSeed] = useState<{ composition: CockpitComposition; meta?: CockpitMeta } | null>(null)

  const currentUsername = keycloak.tokenParsed?.preferred_username as string | undefined
  const buildingLabel = loadedZone ?? ''

  const openZone = async () => {
    const trimmed = zone.trim()
    if (!trimmed) return
    setLoading(true)
    setStatus('Recherche du bâtiment pour cette zone...')
    try {
      const id = await getBuildingGlobalId(trimmed)
      setLoadedZone(trimmed)
      setStatus(
        id === null
          ? `Aucun bâtiment trouvé pour « ${trimmed} ». L'ingestion se fait depuis le Configurateur.`
          : '',
      )
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Erreur')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setBuildingId(null)
    setEnergyView('library')
    setBuilderSeed(null)
    if (!loadedZone) return
    getBuildingGlobalId(loadedZone)
      .then(setBuildingId)
      .catch(() => setBuildingId(null))
  }, [loadedZone])

  return (
    <div className="panel" style={{ marginTop: 24 }}>
      <h3>⚡ Cockpit Énergie</h3>
      <p className="panel-hint">
        Pilotez la consommation énergétique d'une zone déjà ingérée depuis le Configurateur.
      </p>

      <ZonePicker zone={zone} onZoneChange={setZone} onOpen={openZone} loading={loading} />

      {status && <p className="panel-hint">{status}</p>}

      {loadedZone && buildingId && energyView === 'library' && (
        <CockpitLibrary
          buildingId={buildingId}
          buildingLabel={buildingLabel}
          currentUsername={currentUsername}
          onOpenCockpit={(composition, meta) => { setBuilderSeed({ composition, meta }); setEnergyView('builder') }}
          onCreateNew={() => { setBuilderSeed({ composition: defaultComposition('batiment', [buildingId], buildingLabel) }); setEnergyView('builder') }}
        />
      )}

      {loadedZone && buildingId && energyView === 'builder' && builderSeed && (
        <CockpitBuilder
          zone={loadedZone}
          buildingId={buildingId}
          buildingLabel={buildingLabel}
          seed={builderSeed.composition}
          seedMeta={builderSeed.meta}
          currentUsername={currentUsername}
          onBack={() => setEnergyView('library')}
          onSaved={() => setEnergyView('library')}
        />
      )}
    </div>
  )
}
