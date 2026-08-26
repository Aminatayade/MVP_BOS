/**
 * FILE: features/bim/BimExplorer.tsx
 * DESCRIPTION: Composant composite (upload de maquettes IFC + explorateur de
 *              graphe) — adapté de MVP-bim-integration/frontend_bim/src/App.tsx
 *              pour être monté comme une section du front BOS Light plutôt
 *              qu'une application autonome. Tous les appels passent désormais
 *              par la gateway BOS Light (/api/bim/*), donc par l'auth Keycloak
 *              déjà en place côté ProtectedRoute.
 */

import { useRef, useState } from 'react'
import BimGraphViewer from './components/BimGraphViewer'
import BimModelViewer from './components/BimModelViewer'
import NodeInspector from './components/NodeInspector'
import { getBuildings, getModelSources, ingestBatch, type ModelSource } from './services/arcadeApi'
import { arcadeRowsToNodeList, nodeElement, type CytoscapeElement } from './utils/graphUtils'

// Grafana tourne comme service à part (pas via l'API Gateway) : le navigateur
// le contacte directement, comme Keycloak -- même raisonnement que
// VITE_API_GATEWAY_URL/VITE_KEYCLOAK_URL. "?kiosk" masque la nav bar/le logo
// Grafana dans l'iframe.
const GRAFANA_DASHBOARD_URL = `${import.meta.env.VITE_GRAFANA_URL}/d/bos-equipment-demo/bos-light-equipement-demo?kiosk&theme=dark`

type ValidationResult = {
  success: boolean
  status: number
  schema_version: string | null
  errors: string[]
  warnings: string[]
  stored?: boolean
  storage_url?: string | null
  storage_error?: string | null
}

type GraphResult = {
  status: string
  message: string
  source_key: string
  commands_count: number
  nodes_count: number
  edges_count: number
  equipment_count: number
  unmatched_equipment_count: number
  validation: ValidationResult | null
}

type IngestionResult = {
  validation_storage: ValidationResult | { error: string }
  graph_ingestion: GraphResult | { status: string; message: string }
}

type BatchResult = {
  filename: string
  result: ValidationResult | IngestionResult | { error: string }
}

type BatchResponse = {
  batch: number
  results: BatchResult[]
}

const disciplines = [
  { value: 'ARCHI', label: 'Architecture' },
  { value: 'CVC', label: 'CVC' },
  { value: 'MEP', label: 'MEP' },
  { value: 'PLOMBERIE', label: 'Sanitaire' },
  { value: 'ELEC', label: 'Electricité' },
  { value: 'EQUIPEMENTS_AUTRE', label: 'Équipements autre' },
  { value: 'FACADE', label: 'Façade' },
  { value: 'SECOND_OEUVRE', label: 'Second-oeuvre' },
  { value: 'AMENAGEMENT_EXTERIEUR', label: 'Aménagement extérieur' },
  { value: 'SCENOGRAPHIE', label: 'Scénographie' },
  { value: 'CUISINE', label: 'Cuisine' },
  { value: 'MACHINES', label: 'Machines' },
  { value: 'STRUCTURE', label: 'Structure' },
  { value: 'PERCEMENTS', label: 'Percements' },
]

type SelectedFile = {
  file: File
  discipline: string
}

/**
 * Un dépôt de DOSSIER (pas juste des fichiers) via glisser-déposer n'apparaît
 * pas directement dans dataTransfer.files : il faut parcourir manuellement
 * l'arborescence via l'API FileSystemEntry (webkitGetAsEntry), fichier par
 * fichier, sous-dossier par sous-dossier.
 */
async function collectFilesFromDataTransfer(dataTransfer: DataTransfer): Promise<File[]> {
  const items = Array.from(dataTransfer.items || [])
  const collected: File[] = []

  async function readDirectory(entry: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
    const reader = entry.createReader()
    const all: FileSystemEntry[] = []
    // readEntries ne renvoie pas forcément tout en un seul appel
    // (limite interne du navigateur) : on boucle jusqu'à un lot vide.
    let batch: FileSystemEntry[]
    do {
      batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject))
      all.push(...batch)
    } while (batch.length > 0)
    return all
  }

  async function walk(entry: FileSystemEntry | null): Promise<void> {
    if (!entry) return
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject),
      )
      collected.push(file)
    } else if (entry.isDirectory) {
      const children = await readDirectory(entry as FileSystemDirectoryEntry)
      for (const child of children) {
        await walk(child)
      }
    }
  }

  const hasEntrySupport = items.length > 0 && typeof items[0].webkitGetAsEntry === 'function'

  if (hasEntrySupport) {
    for (const item of items) {
      const entry = item.webkitGetAsEntry?.()
      if (entry) {
        await walk(entry)
      } else if (item.kind === 'file') {
        const file = item.getAsFile()
        if (file) collected.push(file)
      }
    }
    return collected
  }

  // Navigateur sans support de l'API FileSystemEntry : fallback simple,
  // ne fonctionne que pour un dépôt de fichiers (pas de dossier).
  return Array.from(dataTransfer.files || [])
}

export default function BimExplorer() {
  const [open, setOpen] = useState(false)
  const [defaultDiscipline, setDefaultDiscipline] = useState('ARCHI')
  const [zone, setZone] = useState('')
  const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([])
  const [status, setStatus] = useState('')
  const [batchResults, setBatchResults] = useState<BatchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [showVisualizer, setShowVisualizer] = useState(false)
  const [explorerTab, setExplorerTab] = useState<'graph' | 'model' | 'supervision'>('graph')
  const [modelSources, setModelSources] = useState<ModelSource[]>([])
  const [modelSourcesLoaded, setModelSourcesLoaded] = useState(false)
  const [visualElements, setVisualElements] = useState<CytoscapeElement[]>([])
  const [selectedNodeInfo, setSelectedNodeInfo] = useState<any | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const dragCounter = useRef(0)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const addFiles = (files: File[]) => {
    const ifcFiles = files.filter((f) => f.name.toLowerCase().endsWith('.ifc'))
    if (ifcFiles.length === 0) {
      setStatus(files.length === 0 ? 'Aucun fichier détecté.' : 'Aucun fichier .ifc trouvé dans la sélection.')
      return
    }
    setSelectedFiles((prev) => [
      ...prev,
      ...ifcFiles.map((file) => ({ file, discipline: defaultDiscipline })),
    ])
    setStatus(
      ifcFiles.length < files.length
        ? `${ifcFiles.length} fichier(s) .ifc ajouté(s) (${files.length - ifcFiles.length} fichier(s) ignoré(s) : extension non .ifc).`
        : `${ifcFiles.length} fichier(s) ajouté(s).`,
    )
  }

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    addFiles(Array.from(event.target.files ?? []))
    event.target.value = ''
  }

  const handleDrop = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    dragCounter.current = 0
    setDragActive(false)
    const files = await collectFilesFromDataTransfer(event.dataTransfer)
    addFiles(files)
  }

  const handleDragEnter = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    dragCounter.current += 1
    setDragActive(true)
  }

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    dragCounter.current -= 1
    if (dragCounter.current <= 0) {
      dragCounter.current = 0
      setDragActive(false)
    }
  }

  const updateFileDiscipline = (index: number, discipline: string) => {
    setSelectedFiles((prev) => prev.map((entry, i) => (i === index ? { ...entry, discipline } : entry)))
  }

  const handleSubmit = async () => {
    if (selectedFiles.length === 0) {
      setStatus('Sélectionnez au moins un fichier IFC.')
      return
    }
    if (!zone.trim()) {
      setStatus('Indiquez une zone/bâtiment (ex: "Batiment A", "Parking B").')
      return
    }

    setLoading(true)
    setStatus(`Validation en cours (${selectedFiles.length} fichier(s))...`)
    setBatchResults(null)

    try {
      const filesData = await Promise.all(
        selectedFiles.map(async ({ file, discipline }) => ({
          name: file.name,
          discipline,
          content: await new Promise<string>((resolve) => {
            const reader = new FileReader()
            reader.onload = () => {
              const base64 = (reader.result as string).split(',')[1] || ''
              resolve(base64)
            }
            reader.readAsDataURL(file)
          }),
        })),
      )

      const payload = await ingestBatch(zone.trim(), filesData)
      setBatchResults(payload as BatchResponse)
      setStatus(
        selectedFiles.length === 1
          ? 'Ingestion terminée.'
          : `${selectedFiles.length} fichier(s) analysés.`,
      )
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Erreur inattendue')
    } finally {
      setLoading(false)
    }
  }

  const openVisualizer = async () => {
    setStatus('Chargement des bâtiments...')
    setSelectedNodeInfo(null)
    setExplorerTab('graph')
    setModelSourcesLoaded(false)
    setModelSources([])
    try {
      const rows = await getBuildings()
      const nodes = arcadeRowsToNodeList(rows).map((data) => nodeElement(data))
      setVisualElements(nodes)
      setShowVisualizer(true)
      setStatus(
        nodes.length === 0
          ? 'Aucun bâtiment trouvé dans le graphe BIM.'
          : `${nodes.length} bâtiment(s) — cliquez sur un nœud pour déplier (étages, pièces, équipements).`,
      )
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Erreur')
    }
  }

  const openModelTab = async () => {
    setExplorerTab('model')
    if (modelSourcesLoaded) return
    try {
      const found = await getModelSources(zone.trim())
      setModelSources(found)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Erreur')
    } finally {
      setModelSourcesLoaded(true)
    }
  }

  if (!open) {
    return (
      <div className="card card--pad" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
          <div style={{
            width: 40, height: 40, borderRadius: 10, background: 'var(--blue-50)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}>
            <BimIcon />
          </div>
          <div>
            <h3 style={{ fontSize: 15, marginBottom: 3 }}>Explorateur BIM</h3>
            <p className="muted" style={{ fontSize: 13 }}>
              Chargez des maquettes IFC et explorez le graphe bâtiment / étages / pièces / équipements.
            </p>
          </div>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => setOpen(true)}>
          Ouvrir l'explorateur
        </button>
      </div>
    )
  }

  if (showVisualizer) {
    return (
      <div className="explorer-overlay">
        <header className="explorer-overlay__header">
          <div>
            <h2 style={{ color: 'var(--white)', fontSize: 17 }}>Explorateur BIM</h2>
            <p style={{ margin: '4px 0 0', fontSize: 13 }}>{status}</p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            {/* Onglets partageant la même zone sélectionnée : préparation du
                point 7 de la roadmap, qui devra relier graphe et maquette 3D. */}
            <div style={{ display: 'flex', gap: 4, background: 'rgba(255,255,255,0.08)', borderRadius: 8, padding: 3 }}>
              <button
                type="button"
                className={explorerTab === 'graph' ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}
                style={explorerTab !== 'graph' ? { color: '#cbd8e8' } : undefined}
                onClick={() => setExplorerTab('graph')}
              >
                Graphe
              </button>
              <button
                type="button"
                className={explorerTab === 'model' ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}
                style={explorerTab !== 'model' ? { color: '#cbd8e8' } : undefined}
                onClick={openModelTab}
              >
                Maquette 3D
              </button>
              <button
                type="button"
                className={explorerTab === 'supervision' ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm'}
                style={explorerTab !== 'supervision' ? { color: '#cbd8e8' } : undefined}
                onClick={() => setExplorerTab('supervision')}
              >
                Supervision
              </button>
            </div>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              style={{ background: 'transparent', color: 'var(--white)', borderColor: '#2c4c6c' }}
              onClick={() => { setShowVisualizer(false); setVisualElements([]); setSelectedNodeInfo(null) }}
            >
              ← Retour à l'upload
            </button>
          </div>
        </header>
        {explorerTab === 'graph' ? (
          <div style={{ flex: 1, display: 'flex', gap: 12, padding: 16, minHeight: 0, minWidth: 0 }}>
            {/* minWidth/minHeight: 0 indispensables ici : un flex-item vaut par
                défaut min-width/min-height "auto", donc refuse de rétrécir en
                dessous de la taille intrinsèque de son contenu. Le canvas
                Cytoscape à l'intérieur (width/height: 100%) se mesure alors sur
                son propre contenu au lieu de la place disponible, et grossit à
                chaque re-rendu (boucle de redimensionnement) en écrasant le
                panneau de droite. */}
            <div style={{ flex: 1, minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
              <BimGraphViewer
                elements={visualElements}
                onNodeSelect={(data) => setSelectedNodeInfo(data)}
                autoExpandOnClick
                height="100%"
              />
            </div>
            <div className="inspector-panel" style={{ width: 380, flexShrink: 0 }}>
              <NodeInspector node={selectedNodeInfo} onClose={() => setSelectedNodeInfo(null)} />
            </div>
          </div>
        ) : explorerTab === 'model' ? (
          <div style={{ flex: 1, padding: 16, minHeight: 0, minWidth: 0 }}>
            <BimModelViewer sources={modelSources} height="100%" />
          </div>
        ) : (
          <div style={{ flex: 1, padding: 16, minHeight: 0, minWidth: 0 }}>
            {/* Mode kiosk : masque la nav bar/le logo Grafana, le client ne voit
                que les courbes -- pas besoin de savoir que c'est Grafana
                derrière. Le sélecteur pièce/étage reste dans le dashboard
                lui-même pour l'instant (pas encore synchronisé avec le noeud
                sélectionné dans le graphe). */}
            <iframe
              key={GRAFANA_DASHBOARD_URL}
              src={GRAFANA_DASHBOARD_URL}
              title="Supervision"
              style={{ width: '100%', height: '100%', border: 'none', borderRadius: 8, background: '#0b1120' }}
            />
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="card card--pad">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <div className="eyebrow">Import & validation</div>
          <h2 style={{ fontSize: 17, marginBottom: 6 }}>Explorateur BIM</h2>
          <p className="muted" style={{ fontSize: 13, maxWidth: 560 }}>
            Chargez autant de maquettes IFC que vous voulez pour un même bâtiment — une par discipline
            ou plusieurs à la fois — puis laissez le backend valider et ingérer chaque fichier selon sa
            propre discipline.
          </p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>
          Fermer
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
        <div className="field">
          <label>Discipline par défaut (nouveaux fichiers)</label>
          <select className="input" value={defaultDiscipline} onChange={(event) => setDefaultDiscipline(event.target.value)}>
            {disciplines.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <small>Modifiable individuellement dans la liste ci-dessous.</small>
        </div>

        <div className="field">
          <label>Zone / Bâtiment</label>
          <input
            type="text"
            className="input"
            placeholder='ex: "Batiment A", "Parking B"'
            value={zone}
            onChange={(event) => setZone(event.target.value)}
          />
          <small>Sert à rattacher les équipements au bon Room/Floor.</small>
        </div>
      </div>

      <div
        className={dragActive ? 'dropzone dropzone--active' : 'dropzone'}
        style={{ marginTop: 18 }}
        onClick={() => fileInputRef.current?.click()}
        onDragEnter={handleDragEnter}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <span className="dropzone__icon"><UploadIcon /></span>
        <span className="dropzone__title">Cliquez pour importer, ou glissez-déposez vos fichiers/dossier IFC ici</span>
        <span className="dropzone__hint">Fichiers .ifc uniquement — un dossier déposé est parcouru automatiquement</span>
        <input
          ref={fileInputRef}
          type="file"
          accept=".ifc"
          multiple
          onChange={handleFileChange}
          style={{ display: 'none' }}
        />
      </div>

      <div style={{ marginTop: '16px', display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-primary" onClick={handleSubmit} disabled={selectedFiles.length === 0 || loading}>
          {loading ? 'Analyse en cours…' : 'Valider les maquettes'}
        </button>
        <button type="button" className="btn btn-outline" onClick={openVisualizer}>
          Visualiser graphe
        </button>
        {selectedFiles.length > 0 && (
          <button type="button" className="btn btn-danger" onClick={() => setSelectedFiles([])} disabled={loading}>
            Réinitialiser
          </button>
        )}
      </div>

      {selectedFiles.length > 0 && (
        <div style={{ marginTop: '16px' }}>
          <small className="muted">Fichiers sélectionnés (discipline modifiable par fichier) :</small>
          <div style={{ marginTop: 8 }}>
            {selectedFiles.map(({ file, discipline }, index) => (
              <div className="file-row" key={`${file.name}-${index}`}>
                <span className="file-row__name">{file.name}</span>
                <select className="input" style={{ width: 'auto', padding: '5px 8px', fontSize: 12.5 }} value={discipline} onChange={(event) => updateFileDiscipline(index, event.target.value)}>
                  {disciplines.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                <button type="button" className="file-row__remove" onClick={() => setSelectedFiles(selectedFiles.filter((_, i) => i !== index))}>
                  ✕
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {status && <p className="status-banner">{status}</p>}

      {batchResults && (
        <section style={{ marginTop: '10px' }}>
          <h3 style={{ fontSize: 14, marginBottom: 10 }}>Résultats du batch ({batchResults.results.length} fichier(s))</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {batchResults.results.map((item) => {
              const isError = 'error' in item.result
              const isIngestion = !isError && 'validation_storage' in item.result
              const validationResult = !isError && !isIngestion ? (item.result as ValidationResult) : null
              const ingestionResult = isIngestion ? (item.result as IngestionResult) : null
              const ingestionOk =
                isIngestion &&
                ('success' in ingestionResult!.validation_storage ? ingestionResult!.validation_storage.success : false)
              const ok = isIngestion ? ingestionOk : (validationResult?.success ?? false)

              return (
                <div key={item.filename} className={`result-card ${isError ? 'result-card--error' : ok ? 'result-card--ok' : 'result-card--error'}`}>
                  <div className="result-card__head">
                    <span>{item.filename}</span>
                    {isError ? (
                      <span className="badge-outline badge-outline--error">Erreur</span>
                    ) : isIngestion ? (
                      <span className={`badge-outline ${ingestionOk ? 'badge-outline--ok' : 'badge-outline--error'}`}>
                        {ingestionOk ? 'Terminée' : 'Rejetée'}
                      </span>
                    ) : (
                      <span className={`badge-outline ${validationResult!.success ? 'badge-outline--ok' : 'badge-outline--error'}`}>
                        {validationResult!.success ? 'Conforme' : 'Rejetée'}
                      </span>
                    )}
                  </div>

                  {isError && <p style={{ color: 'var(--danger)', fontSize: 12, marginTop: 6 }}>{(item.result as any).error}</p>}

                  {isIngestion && (
                    <>
                      <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--gray-600)', marginTop: 6 }}>
                        <span>
                          Validation/stockage:{' '}
                          {'success' in ingestionResult!.validation_storage
                            ? ingestionResult!.validation_storage.success ? 'conforme' : 'rejetée'
                            : 'erreur'}
                        </span>
                        <span>Graphe: {(ingestionResult!.graph_ingestion as any).status}</span>
                      </div>
                      {'message' in ingestionResult!.graph_ingestion && (
                        <p style={{ fontSize: 12, color: 'var(--gray-600)', marginTop: 4 }}>{ingestionResult!.graph_ingestion.message}</p>
                      )}
                      {'unmatched_equipment_count' in ingestionResult!.graph_ingestion &&
                        ingestionResult!.graph_ingestion.unmatched_equipment_count > 0 && (
                          <p style={{ fontSize: 12, color: 'var(--warning)', marginTop: 4 }}>
                            {ingestionResult!.graph_ingestion.unmatched_equipment_count} /{' '}
                            {ingestionResult!.graph_ingestion.equipment_count} équipement(s) non rattaché(s).
                          </p>
                        )}

                      {'errors' in ingestionResult!.validation_storage &&
                        ingestionResult!.validation_storage.errors.length > 0 && (
                          <div style={{ marginTop: 6 }}>
                            <strong style={{ fontSize: 12 }}>
                              Erreurs ({ingestionResult!.validation_storage.errors.length})
                            </strong>
                            <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--danger)' }}>
                              {ingestionResult!.validation_storage.errors.map((msg) => <li key={msg}>{msg}</li>)}
                            </ul>
                          </div>
                        )}
                      {'warnings' in ingestionResult!.validation_storage &&
                        ingestionResult!.validation_storage.warnings.length > 0 && (
                          <div style={{ marginTop: 6 }}>
                            <strong style={{ fontSize: 12 }}>
                              Avertissements ({ingestionResult!.validation_storage.warnings.length})
                            </strong>
                            <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--warning)' }}>
                              {ingestionResult!.validation_storage.warnings.map((msg) => <li key={msg}>{msg}</li>)}
                            </ul>
                          </div>
                        )}
                    </>
                  )}

                  {validationResult && (
                    <>
                      {validationResult.errors.length > 0 && (
                        <div style={{ marginTop: 6 }}>
                          <strong style={{ fontSize: 12 }}>Erreurs ({validationResult.errors.length})</strong>
                          <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--danger)' }}>
                            {validationResult.errors.map((msg) => <li key={msg}>{msg}</li>)}
                          </ul>
                        </div>
                      )}
                      {validationResult.warnings.length > 0 && (
                        <div style={{ marginTop: 6 }}>
                          <strong style={{ fontSize: 12 }}>Avertissements ({validationResult.warnings.length})</strong>
                          <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--warning)' }}>
                            {validationResult.warnings.map((msg) => <li key={msg}>{msg}</li>)}
                          </ul>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}

function BimIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--blue-500)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 21h18" />
      <path d="M5 21V7l8-4v18" />
      <path d="M19 21V11l-6-4" />
      <path d="M9 9v.01M9 12v.01M9 15v.01M9 18v.01" />
    </svg>
  )
}

function UploadIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
      <path d="M7 9l5-5 5 5" />
      <path d="M12 4v12" />
    </svg>
  )
}
