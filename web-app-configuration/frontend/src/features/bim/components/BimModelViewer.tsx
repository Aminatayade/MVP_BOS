/**
 * FILE: features/bim/components/BimModelViewer.tsx
 * DESCRIPTION: Visualiseur 3D du jumeau numérique (Point 6 de la roadmap),
 *              basé sur ThatOpenEngine (@thatopen/components + fragments).
 *              Charge un IFC par discipline (Archi, Élec, CVC...) dans la
 *              même scène three.js -- c'est ce chargement multiple qui
 *              réalise la "fusion visuelle" demandée, sans traitement
 *              serveur : chaque IFC est coordonné sur son système de
 *              coordonnées de site partagé (option `coordinate` de
 *              IfcLoader.load, laissée à sa valeur par défaut).
 */

import { useEffect, useRef, useState } from 'react'
import * as OBC from '@thatopen/components'
import type { FragmentsModel } from '@thatopen/fragments'
import { downloadModelBytes, type ModelSource } from '../services/arcadeApi'

type Props = {
  sources: ModelSource[]
  height?: string | number
}

type LoadedModel = {
  discipline: string
  model: FragmentsModel
}

export default function BimModelViewer({ sources, height = '600px' }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const componentsRef = useRef<OBC.Components | null>(null)
  const worldRef = useRef<OBC.SimpleWorld<OBC.SimpleScene, OBC.SimpleCamera, OBC.SimpleRenderer> | null>(null)
  const ifcLoaderRef = useRef<OBC.IfcLoader | null>(null)
  const loadedRef = useRef<LoadedModel[]>([])
  const [ready, setReady] = useState(false)
  const [visibleDisciplines, setVisibleDisciplines] = useState<Set<string>>(new Set())
  const [status, setStatus] = useState('Initialisation du moteur 3D…')

  // Initialisation de la scène three.js : une seule fois par montage.
  useEffect(() => {
    if (!containerRef.current) return
    let cancelled = false

    const components = new OBC.Components()
    componentsRef.current = components

    const worlds = components.get(OBC.Worlds)
    const world = worlds.create<OBC.SimpleScene, OBC.SimpleCamera, OBC.SimpleRenderer>()
    world.scene = new OBC.SimpleScene(components)
    world.renderer = new OBC.SimpleRenderer(components, containerRef.current)
    world.camera = new OBC.SimpleCamera(components)
    worldRef.current = world

    components.init()
    world.scene.setup()
    world.camera.controls.setLookAt(15, 15, 15, 0, 0, 0)

    const ifcLoader = components.get(OBC.IfcLoader)
    ifcLoaderRef.current = ifcLoader

    const fragments = components.get(OBC.FragmentsManager)

    ;(async () => {
      try {
        // autoSetWasm résout par défaut le binaire WebAssembly web-ifc depuis
        // un CDN externe -- source d'échecs (mismatch de version entre le
        // binaire résolu et le code JS installé, ou CDN injoignable ; erreur
        // observée : "import object field 'a' is not an Object"). Le binaire
        // exact correspondant à la version de web-ifc installée est copié une
        // fois dans public/wasm/ (voir package.json) et servi par le front
        // lui-même -- cohérent avec le reste du projet, entièrement
        // autohébergé, sans dépendance réseau externe au chargement.
        await ifcLoader.setup({ autoSetWasm: false, wasm: { path: '/wasm/', absolute: false } })
        const workerUrl = await OBC.FragmentsManager.getWorker()
        if (cancelled) return
        fragments.init(workerUrl)
        world.camera.controls.addEventListener('rest', () => fragments.core.update(true))
        if (!cancelled) setReady(true)
      } catch (error) {
        if (!cancelled) {
          setStatus(error instanceof Error ? `Erreur d'initialisation 3D : ${error.message}` : 'Erreur d’initialisation 3D')
        }
      }
    })()

    return () => {
      cancelled = true
      for (const { model } of loadedRef.current) {
        try { model.dispose() } catch { /* ignore dispose errors on unmount */ }
      }
      loadedRef.current = []
      try { components.dispose() } catch { /* ignore dispose errors on unmount */ }
      componentsRef.current = null
      worldRef.current = null
      ifcLoaderRef.current = null
    }
  }, [])

  // Chargement des sources IFC une fois le moteur prêt, ou quand la liste de
  // sources change (ex: changement de zone) : rechargement complet -- une
  // action peu fréquente (choix de zone), pas besoin d'un diff incrémental.
  useEffect(() => {
    if (!ready) return
    const world = worldRef.current
    const ifcLoader = ifcLoaderRef.current
    if (!world || !ifcLoader) return

    let cancelled = false

    ;(async () => {
      for (const { model } of loadedRef.current) {
        try {
          world.scene.three.remove(model.object)
          model.dispose()
        } catch { /* ignore */ }
      }
      loadedRef.current = []

      if (sources.length === 0) {
        setStatus('Aucune maquette IFC ingérée pour cette zone.')
        setVisibleDisciplines(new Set())
        return
      }

      setStatus(`Chargement de ${sources.length} maquette(s)…`)
      const disciplinesLoaded: string[] = []

      for (const source of sources) {
        if (cancelled) return
        try {
          const bytes = await downloadModelBytes(source.storageUrl)
          if (cancelled) return
          const model = await ifcLoader.load(new Uint8Array(bytes), true, source.storageUrl)
          if (cancelled) {
            model.dispose()
            return
          }
          world.scene.three.add(model.object)
          model.useCamera(world.camera.three)
          loadedRef.current.push({ discipline: source.discipline, model })
          disciplinesLoaded.push(source.discipline)
        } catch (error) {
          console.error(`Échec du chargement de la maquette (${source.discipline}):`, error)
        }
      }

      if (cancelled) return
      setVisibleDisciplines(new Set(disciplinesLoaded))
      setStatus(`${disciplinesLoaded.length}/${sources.length} maquette(s) chargée(s).`)
    })()

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, sources])

  const toggleDiscipline = (discipline: string, visible: boolean) => {
    setVisibleDisciplines((prev) => {
      const next = new Set(prev)
      if (visible) next.add(discipline)
      else next.delete(discipline)
      return next
    })
    for (const { discipline: d, model } of loadedRef.current) {
      if (d === discipline) model.object.visible = visible
    }
  }

  const disciplines = Array.from(new Set(sources.map((s) => s.discipline)))

  return (
    <div
      style={{
        width: '100%',
        height,
        border: '1px solid var(--gray-200, #334155)',
        borderRadius: 12,
        overflow: 'hidden',
        background: '#111827',
        position: 'relative',
        display: 'flex',
      }}
    >
      <div ref={containerRef} style={{ flex: 1, minWidth: 0 }} />

      <div
        style={{
          position: 'absolute',
          top: 10,
          left: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          background: 'rgba(17, 24, 39, 0.85)',
          borderRadius: 8,
          padding: '10px 12px',
          maxWidth: 220,
        }}
      >
        <span style={{ fontSize: 11, color: '#94a3b8', marginBottom: 2 }}>{status}</span>
        {disciplines.map((discipline) => (
          <label key={discipline} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: '#e2e8f0' }}>
            <input
              type="checkbox"
              checked={visibleDisciplines.has(discipline)}
              onChange={(event) => toggleDiscipline(discipline, event.target.checked)}
            />
            {discipline}
          </label>
        ))}
      </div>

      <button
        type="button"
        title="Recentrer"
        onClick={() => worldRef.current?.camera.controls.setLookAt(15, 15, 15, 0, 0, 0, true)}
        style={{
          position: 'absolute', top: 10, right: 10, width: 32, height: 32, borderRadius: 6,
          border: '1px solid #334155', background: '#1f2937', color: '#e2e8f0', cursor: 'pointer', fontSize: 16,
        }}
      >
        ⤢
      </button>
    </div>
  )
}
