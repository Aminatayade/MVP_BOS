/**
 * FILE: features/bim/JumeauNumeriqueApp.tsx
 * DESCRIPTION: App "Jumeau numérique" de BOS Exécution -- volet 3D isolé du
 *              cockpit énergie (voir CockpitEnergieApp.tsx), accessible
 *              depuis l'écran d'accueil (features/home/AppLauncher.tsx).
 *              Strictement en lecture : pas d'upload ni de validation/
 *              ingestion IFC ici -- ça reste le rôle du Configurateur.
 */
import { useState } from 'react'
import BimModelViewer from './components/BimModelViewer'
import { getModelSources, type ModelSource } from './services/arcadeApi'
import ZonePicker from '../shared/ZonePicker'

export default function JumeauNumeriqueApp() {
  const [zone, setZone] = useState('')
  const [loadedZone, setLoadedZone] = useState<string | null>(null)
  const [sources, setSources] = useState<ModelSource[]>([])
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)

  const openZone = async () => {
    const trimmed = zone.trim()
    if (!trimmed) return
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

  return (
    <div className="panel" style={{ marginTop: 24 }}>
      <h3>🏗️ Jumeau numérique</h3>
      <p className="panel-hint">
        Consultez la maquette 3D d'une zone déjà ingérée depuis le Configurateur.
      </p>

      <ZonePicker zone={zone} onZoneChange={setZone} onOpen={openZone} loading={loading} />

      {status && <p className="panel-hint">{status}</p>}

      {loadedZone && sources.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <BimModelViewer sources={sources} height="480px" />
        </div>
      )}
    </div>
  )
}
