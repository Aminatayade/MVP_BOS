/**
 * FILE: features/bim/BimViewer.tsx
 * DESCRIPTION: Visualisation du jumeau numérique côté BOS Exécution (point 6
 *              de la roadmap, volet "client"). Strictement en lecture : pas
 *              d'upload ni de validation/ingestion IFC ici -- ça reste le
 *              rôle du Configurateur. Si aucune maquette n'a encore été
 *              ingérée pour la zone saisie, l'état vide l'explique
 *              clairement plutôt que d'afficher une erreur.
 *
 * Le module énergie n'est plus un panneau fixe ("Consommation électrique")
 * mais un système à deux écrans -- bibliothèque de cockpits (CockpitLibrary)
 * et constructeur composable (CockpitBuilder), voir features/cockpits/.
 */

import { useEffect, useState } from 'react'
import BimModelViewer from './components/BimModelViewer'
import { getBuildingGlobalId, getModelSources, type ModelSource } from './services/arcadeApi'
import CockpitLibrary from '../cockpits/CockpitLibrary'
import CockpitBuilder from '../cockpits/CockpitBuilder'
import { defaultComposition, type CockpitComposition, type CockpitMeta } from '../cockpits/types'
import keycloak from '../../services/keycloak'

type EnergyView = 'library' | 'builder'

export default function BimViewer() {
  const [zone, setZone] = useState('')
  const [loadedZone, setLoadedZone] = useState<string | null>(null)
  const [sources, setSources] = useState<ModelSource[]>([])
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)

  const [buildingId, setBuildingId] = useState<string | null>(null)
  const [energyView, setEnergyView] = useState<EnergyView>('library')
  const [builderSeed, setBuilderSeed] = useState<{ composition: CockpitComposition; meta?: CockpitMeta } | null>(null)

  const currentUsername = keycloak.tokenParsed?.preferred_username as string | undefined
  const buildingLabel = loadedZone ?? ''

  const openZone = async () => {
    const trimmed = zone.trim()
    if (!trimmed) {
      setStatus('Indiquez une zone/bâtiment (ex: "Batiment A").')
      return
    }
    setLoading(true)
    setStatus('Recherche des maquettes déjà ingérées...')
    try {
      const found = await getModelSources(trimmed)
      setSources(found)
      setLoadedZone(trimmed)
      setStatus(
        found.length === 0
          ? `Aucune maquette disponible pour « ${trimmed} ». L'ingestion se fait depuis le Configurateur.`
          : `${found.length} maquette(s) chargée(s) pour « ${trimmed} ».`,
      )
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Erreur')
    } finally {
      setLoading(false)
    }
  }

  // Le module énergie a besoin du Building de la zone, indépendamment de la
  // présence d'une maquette 3D (aucune maquette IFC n'empêche d'avoir déjà
  // un graphe/de la télémétrie ingérés pour cette zone).
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
      <h3>🏗️ Jumeau numérique</h3>
      <p className="panel-hint">
        Consultez la maquette 3D d'une zone déjà ingérée depuis le Configurateur. BOS
        Exécution ne permet pas d'importer de nouvelles maquettes.
      </p>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        <input
          type="text"
          value={zone}
          onChange={(event) => setZone(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && openZone()}
          placeholder='ex: "Batiment A"'
          style={{
            flex: '1 1 220px', padding: '10px 14px', borderRadius: 8,
            border: '1px solid var(--border)', background: 'var(--bg)',
            color: 'var(--text-h)', fontSize: 14, fontFamily: 'var(--sans)',
          }}
        />
        <button type="button" className="btn btn-primary" onClick={openZone} disabled={loading}>
          {loading ? 'Chargement…' : 'Afficher'}
        </button>
      </div>

      {status && <p className="panel-hint">{status}</p>}

      {loadedZone && sources.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <BimModelViewer sources={sources} height="480px" />
        </div>
      )}

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
