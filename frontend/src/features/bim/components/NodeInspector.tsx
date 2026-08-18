import { useEffect, useState } from 'react'
import { getNodeChildren } from '../services/arcadeApi'

type Props = {
  node: any | null
  onClose?: () => void
  onExpand?: (id: string) => void
  onCollapse?: (id: string) => void
}

export default function NodeInspector({ node, onClose, onExpand, onCollapse }: Props) {
  // Les hooks doivent toujours s'exécuter dans le même ordre à chaque rendu :
  // le early-return "pas de nœud sélectionné" est donc placé APRÈS les hooks
  // (contrairement à la version d'origine, qui retournait avant `useState`).
  const [equipment, setEquipment] = useState<any[]>([])

  useEffect(() => {
    if (!node) {
      setEquipment([])
      return
    }

    let cancelled = false
    async function loadEquipment() {
      try {
        const rows = await getNodeChildren(node.id)
        const eq: any[] = []
        for (const row of rows) {
          const m = row['neighbors'] || row['m'] || row['result'] || []
          for (const nb of m) {
            const d = nb.attributes || nb
            if (!d || !d.global_id) continue
            const t = nb['@type'] || d.type || ''
            // treat any node with category or type including 'Equipment' as equipment
            if (t.toLowerCase().includes('equipment') || d.category || d.ifc_type === 'IfcDistributionElement') {
              eq.push({ id: d.global_id, name: d.name || d.label || d.global_id, category: (d.category || 'generic').toLowerCase(), desc: d.description || d.ifc_type || JSON.stringify(d.psets || d.properties || {}) })
            }
          }
        }
        if (!cancelled) setEquipment(eq)
      } catch (e) {
        if (!cancelled) setEquipment([])
      }
    }
    setEquipment([])
    loadEquipment()
    return () => { cancelled = true }
  }, [node])

  if (!node) return <div className="p-4 text-sm text-gray-300">Cliquez sur un nœud pour voir les détails.</div>

  const psets = node.psets || node.properties || {}

  return (
    <div className="p-4 text-sm text-gray-100">
      <div className="flex justify-between items-start">
        <div>
          <h3 className="text-lg font-semibold">{node.label || node.name || node.id}</h3>
          <p className="text-xs text-gray-400">{node.type || node.ifc_type}</p>
        </div>
        <div className="flex gap-2">
          {onExpand && (
            <button onClick={() => onExpand(node.id)} className="px-2 py-1 bg-blue-600 rounded text-xs">Expand</button>
          )}
          {onCollapse && (
            <button onClick={() => onCollapse(node.id)} className="px-2 py-1 bg-gray-600 rounded text-xs">Collapse</button>
          )}
          {onClose && (
            <button onClick={onClose} className="text-gray-400 hover:text-gray-200">✕</button>
          )}
        </div>
      </div>

      <div className="mt-3">
        <dl className="text-xs text-gray-300">
          <dt className="font-medium">Global ID</dt>
          <dd className="mb-2">{node.id}</dd>

          {node.name && (
            <>
              <dt className="font-medium">Name</dt>
              <dd className="mb-2">{node.name}</dd>
            </>
          )}

          {node.ifc_type && (
            <>
              <dt className="font-medium">IFC Type</dt>
              <dd className="mb-2">{node.ifc_type}</dd>
            </>
          )}
        </dl>
      </div>

      <div className="mt-4">
        <h4 className="text-sm font-semibold">Property Sets</h4>
        <div className="mt-2 max-h-64 overflow-auto text-xs">
          {Object.keys(psets).length === 0 && <div className="text-gray-400">Aucun Pset trouvé</div>}
          {Object.entries(psets).map(([k, v]) => (
            <div key={k} className="mb-2">
              <div className="text-gray-300 font-medium">{k}</div>
              <div className="text-gray-400">{JSON.stringify(v)}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4">
        <h4 className="text-sm font-semibold">Équipements rattachés</h4>
        <div className="mt-2">
          {equipment.length === 0 && <div className="text-gray-400">Aucun équipement lié</div>}
          {equipment.length > 0 && (
            <div className="flex flex-col gap-2">
              {equipment.map((e) => (
                <div key={e.id} className="flex items-center gap-3 p-2 bg-gray-800 rounded">
                  <img src={`/icons/${e.category}.svg`} alt={e.category} style={{ width: 28, height: 28 }} />
                  <div className="text-xs">
                    <div className="font-medium">{e.name}</div>
                    <div className="text-gray-400 truncate" style={{ maxWidth: 220 }}>{e.desc}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
