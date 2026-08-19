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

  if (!node) {
    return (
      <div className="inspector">
        <p className="muted" style={{ fontSize: 13 }}>Cliquez sur un nœud pour voir les détails.</p>
      </div>
    )
  }

  const psets = node.psets || node.properties || {}

  return (
    <div className="inspector">
      <div className="inspector__head">
        <div>
          <h3 className="inspector__title">{node.label || node.name || node.id}</h3>
          <p className="inspector__subtitle">{node.type || node.ifc_type}</p>
        </div>
        <div className="inspector__actions">
          {onExpand && (
            <button className="btn btn-outline btn-sm" onClick={() => onExpand(node.id)}>Déplier</button>
          )}
          {onCollapse && (
            <button className="btn btn-ghost btn-sm" onClick={() => onCollapse(node.id)}>Replier</button>
          )}
          {onClose && (
            <button className="inspector__close" onClick={onClose}>✕</button>
          )}
        </div>
      </div>

      <div className="inspector__section">
        <dl style={{ margin: 0 }}>
          <div className="inspector__row">
            <dt>Global ID</dt>
            <dd>{node.id}</dd>
          </div>
          {node.name && (
            <div className="inspector__row">
              <dt>Name</dt>
              <dd>{node.name}</dd>
            </div>
          )}
          {node.ifc_type && (
            <div className="inspector__row">
              <dt>IFC Type</dt>
              <dd>{node.ifc_type}</dd>
            </div>
          )}
        </dl>
      </div>

      <div className="inspector__section">
        <h4>Property sets</h4>
        {Object.keys(psets).length === 0 && <p className="muted" style={{ fontSize: 12.5 }}>Aucun Pset trouvé</p>}
        {Object.entries(psets).map(([k, v]) => (
          <div key={k} className="inspector__row" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
            <dt style={{ fontWeight: 600, color: 'var(--gray-700)' }}>{k}</dt>
            <dd style={{ textAlign: 'left', color: 'var(--gray-500)' }}>{JSON.stringify(v)}</dd>
          </div>
        ))}
      </div>

      <div className="inspector__section">
        <h4>Équipements rattachés</h4>
        {equipment.length === 0 && <p className="muted" style={{ fontSize: 12.5 }}>Aucun équipement lié</p>}
        {equipment.length > 0 && (
          <div>
            {equipment.map((e) => (
              <div key={e.id} className="equipment-row">
                <img src={`/icons/${e.category}.svg`} alt={e.category} style={{ width: 26, height: 26, flexShrink: 0 }} />
                <div style={{ overflow: 'hidden' }}>
                  <div className="equipment-row__name">{e.name}</div>
                  <div className="equipment-row__desc">{e.desc}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
