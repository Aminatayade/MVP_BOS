/**
 * FILE: features/bim/BimExplorer.tsx
 * DESCRIPTION: Composant composite (upload de maquettes IFC + explorateur de
 *              graphe) — adapté de MVP-bim-integration/frontend_bim/src/App.tsx
 *              pour être monté comme une section du front BOS Light plutôt
 *              qu'une application autonome. Tous les appels passent désormais
 *              par la gateway BOS Light (/api/bim/*), donc par l'auth Keycloak
 *              déjà en place côté ProtectedRoute.
 */

import { useState } from 'react'
import BimGraphViewer from './components/BimGraphViewer'
import NodeInspector from './components/NodeInspector'
import { getBuildings, ingestBatch } from './services/arcadeApi'
import { arcadeRowsToNodeList, nodeElement, type CytoscapeElement } from './utils/graphUtils'

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

export default function BimExplorer() {
  const [open, setOpen] = useState(false)
  const [defaultDiscipline, setDefaultDiscipline] = useState('ARCHI')
  const [zone, setZone] = useState('')
  const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([])
  const [status, setStatus] = useState('')
  const [batchResults, setBatchResults] = useState<BatchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [showVisualizer, setShowVisualizer] = useState(false)
  const [visualElements, setVisualElements] = useState<CytoscapeElement[]>([])
  const [selectedNodeInfo, setSelectedNodeInfo] = useState<any | null>(null)

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    setSelectedFiles((prev) => [
      ...prev,
      ...files.map((file) => ({ file, discipline: defaultDiscipline })),
    ])
    event.target.value = ''
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

  if (!open) {
    return (
      <section style={{ marginTop: '30px' }}>
        <div
          style={{
            border: '1px solid #ccc',
            borderRadius: '8px',
            padding: '20px',
            backgroundColor: '#f9f9f9',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <div>
            <h2 style={{ margin: '0 0 6px 0' }}>🗺️ Explorateur BIM</h2>
            <p style={{ margin: 0, fontSize: '14px', color: '#555' }}>
              Chargez des maquettes IFC (par discipline) et explorez le graphe bâtiment / étages / pièces / équipements.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setOpen(true)}
            style={{ padding: '10px 20px', fontSize: '14px', backgroundColor: '#007bff', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
          >
            Ouvrir l'explorateur
          </button>
        </div>
      </section>
    )
  }

  if (showVisualizer) {
    return (
      <div style={{ position: 'fixed', inset: 0, background: '#0b1120', color: '#f8fafc', display: 'flex', flexDirection: 'column', zIndex: 1000 }}>
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '1rem 1.5rem',
            borderBottom: '1px solid #1f2937',
          }}
        >
          <div>
            <h2 style={{ margin: 0 }}>Explorateur de graphe</h2>
            <p style={{ margin: '4px 0 0', color: '#94a3b8', fontSize: 14 }}>{status}</p>
          </div>
          <button
            type="button"
            onClick={() => { setShowVisualizer(false); setVisualElements([]); setSelectedNodeInfo(null) }}
          >
            ← Retour à l'upload
          </button>
        </header>
        <div style={{ flex: 1, display: 'flex', gap: 12, padding: 12, minHeight: 0, minWidth: 0 }}>
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
          <div style={{ width: 380, flexShrink: 0, background: '#1f2937', borderRadius: 8, overflow: 'auto' }}>
            <NodeInspector node={selectedNodeInfo} onClose={() => setSelectedNodeInfo(null)} />
          </div>
        </div>
      </div>
    )
  }

  return (
    <section style={{ marginTop: '30px', border: '1px solid #ccc', borderRadius: '8px', padding: '20px', backgroundColor: '#f9f9f9' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ margin: '0 0 6px 0' }}>🗺️ Explorateur BIM</h2>
          <p style={{ margin: 0, fontSize: '14px', color: '#555', maxWidth: 640 }}>
            Chargez autant de maquettes IFC que vous voulez pour un même bâtiment — une par discipline
            ou plusieurs à la fois — puis laissez le backend valider et ingérer chaque fichier selon sa
            propre discipline.
          </p>
        </div>
        <button type="button" onClick={() => setOpen(false)} style={{ padding: '6px 12px', fontSize: '13px', cursor: 'pointer' }}>
          Fermer
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px', marginTop: '20px' }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span>Discipline par défaut (nouveaux fichiers)</span>
          <select value={defaultDiscipline} onChange={(event) => setDefaultDiscipline(event.target.value)}>
            {disciplines.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <small style={{ color: '#777' }}>Modifiable individuellement dans la liste ci-dessous.</small>
        </label>

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span>Zone / Bâtiment</span>
          <input
            type="text"
            placeholder='ex: "Batiment A", "Parking B"'
            value={zone}
            onChange={(event) => setZone(event.target.value)}
          />
          <small style={{ color: '#777' }}>Sert à rattacher les équipements au bon Room/Floor.</small>
        </label>

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span>Fichier(s) IFC</span>
          <input type="file" accept=".ifc" multiple onChange={handleFileChange} />
          <small style={{ color: '#777' }}>
            {selectedFiles.length === 0 ? 'Aucun fichier choisi' : `${selectedFiles.length} fichier(s) sélectionné(s)`}
          </small>
        </label>
      </div>

      <div style={{ marginTop: '16px', display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={selectedFiles.length === 0 || loading}
          style={{ padding: '10px 20px', fontSize: '14px', backgroundColor: '#007bff', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
        >
          {loading ? 'Analyse en cours…' : 'Valider les maquettes'}
        </button>
        <button
          type="button"
          onClick={openVisualizer}
          style={{ padding: '10px 20px', fontSize: '14px', cursor: 'pointer' }}
        >
          Visualiser graphe
        </button>
        {selectedFiles.length > 0 && (
          <button
            type="button"
            onClick={() => setSelectedFiles([])}
            disabled={loading}
            style={{ padding: '10px 20px', fontSize: '14px', backgroundColor: '#dc3545', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
          >
            Réinitialiser
          </button>
        )}
      </div>

      {selectedFiles.length > 0 && (
        <div style={{ marginTop: '16px' }}>
          <small style={{ color: '#666' }}>Fichiers sélectionnés (discipline modifiable par fichier) :</small>
          <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0 0' }}>
            {selectedFiles.map(({ file, discipline }, index) => (
              <li key={`${file.name}-${index}`} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: 6 }}>
                <span style={{ flex: 1, fontSize: 13 }}>{file.name}</span>
                <select value={discipline} onChange={(event) => updateFileDiscipline(index, event.target.value)}>
                  {disciplines.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setSelectedFiles(selectedFiles.filter((_, i) => i !== index))}
                  style={{ background: 'none', border: 'none', color: '#ef4444', cursor: 'pointer', padding: '0 0.25rem', fontSize: '1.1rem' }}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p style={{ marginTop: '16px', fontSize: 13, color: '#333' }}>{status}</p>

      {batchResults && (
        <section style={{ marginTop: '16px' }}>
          <h3 style={{ fontSize: 15 }}>Résultats du batch ({batchResults.results.length} fichier(s))</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {batchResults.results.map((item) => {
              const isError = 'error' in item.result
              const isIngestion = !isError && 'validation_storage' in item.result
              const validationResult = !isError && !isIngestion ? (item.result as ValidationResult) : null
              const ingestionResult = isIngestion ? (item.result as IngestionResult) : null

              return (
                <div key={item.filename} style={{ border: '1px solid #ddd', borderRadius: 6, padding: 12, background: 'white' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, fontWeight: 600 }}>
                    <span>{item.filename}</span>
                    {isError ? (
                      <span style={{ color: '#dc3545' }}>Erreur</span>
                    ) : isIngestion ? (
                      <span style={{ color: '#10b981' }}>Terminée</span>
                    ) : (
                      <span style={{ color: validationResult!.success ? '#10b981' : '#dc3545' }}>
                        {validationResult!.success ? 'Conforme' : 'Rejetée'}
                      </span>
                    )}
                  </div>

                  {isError && <p style={{ color: '#dc3545', fontSize: 12 }}>{(item.result as any).error}</p>}

                  {isIngestion && (
                    <>
                      <div style={{ display: 'flex', gap: 16, fontSize: 12, color: '#555', marginTop: 6 }}>
                        <span>
                          Validation/stockage:{' '}
                          {'success' in ingestionResult!.validation_storage
                            ? ingestionResult!.validation_storage.success ? 'conforme' : 'rejetée'
                            : 'erreur'}
                        </span>
                        <span>Graphe: {(ingestionResult!.graph_ingestion as any).status}</span>
                      </div>
                      {'message' in ingestionResult!.graph_ingestion && (
                        <p style={{ fontSize: 12, color: '#555', marginTop: 4 }}>{ingestionResult!.graph_ingestion.message}</p>
                      )}
                      {'unmatched_equipment_count' in ingestionResult!.graph_ingestion &&
                        ingestionResult!.graph_ingestion.unmatched_equipment_count > 0 && (
                          <p style={{ fontSize: 12, color: '#b45309', marginTop: 4 }}>
                            ⚠️ {ingestionResult!.graph_ingestion.unmatched_equipment_count} /{' '}
                            {ingestionResult!.graph_ingestion.equipment_count} équipement(s) non rattaché(s).
                          </p>
                        )}
                    </>
                  )}

                  {validationResult && (
                    <>
                      {validationResult.errors.length > 0 && (
                        <div style={{ marginTop: 6 }}>
                          <strong style={{ fontSize: 12 }}>Erreurs ({validationResult.errors.length})</strong>
                          <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: '#dc3545' }}>
                            {validationResult.errors.map((msg) => <li key={msg}>{msg}</li>)}
                          </ul>
                        </div>
                      )}
                      {validationResult.warnings.length > 0 && (
                        <div style={{ marginTop: 6 }}>
                          <strong style={{ fontSize: 12 }}>Avertissements ({validationResult.warnings.length})</strong>
                          <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: '#b45309' }}>
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
    </section>
  )
}
