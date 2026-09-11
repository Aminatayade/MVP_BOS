/**
 * FILE: features/cockpits/CockpitBuilder.tsx
 * DESCRIPTION: Écran 2 -- constructeur composable de cockpit. Barre du haut
 *              (Période/Espace/Comparer), colonne gauche (Données
 *              disponibles), canevas central (widgets), colonne droite
 *              (ajouter un widget), panneau de réglages par widget.
 */
import { useEffect, useState } from 'react'
import {
  getEquipmentScope,
  getFloors,
  getRoomsByFloor,
  getRoomsByTypology,
  getTypologies,
  type Floor,
  type Typology,
  type TypologyRoom,
} from '../bim/services/arcadeApi'
import WidgetBlock from './components/WidgetBlock'
import WidgetSettingsPanel from './components/WidgetSettingsPanel'
import { createCockpit, updateCockpit, type CockpitInput } from './services/cockpitApi'
import {
  categoryLabel,
  defaultWidget,
  type CockpitComposition,
  type CockpitMeta,
  type PeriodPreset,
  type SpaceLevel,
  type WidgetType,
} from './types'

type Props = {
  zone: string
  buildingId: string
  buildingLabel: string
  seed: CockpitComposition
  seedMeta?: CockpitMeta
  currentUsername?: string
  onBack: () => void
  onSaved: () => void
}

const PERIOD_OPTIONS: { value: PeriodPreset; label: string }[] = [
  { value: 'jour', label: 'Jour' },
  { value: 'semaine', label: 'Semaine' },
  { value: 'mois', label: 'Mois' },
  { value: 'annee', label: 'Année' },
]

const SPACE_LEVEL_OPTIONS: { value: SpaceLevel; label: string }[] = [
  { value: 'batiment', label: 'Bâtiment' },
  { value: 'etage', label: 'Étage' },
  { value: 'typologie', label: 'Type de pièce' },
  { value: 'piece', label: 'Pièce spécifique' },
]

const WIDGET_TYPE_OPTIONS: { value: WidgetType; label: string }[] = [
  { value: 'barres', label: 'Barres' },
  { value: 'courbe', label: 'Courbe' },
  { value: 'camembert', label: 'Camembert' },
  { value: 'semaine_type', label: 'Semaine type' },
  { value: 'indicateur', label: 'Indicateur' },
]

export default function CockpitBuilder({
  zone, buildingId, buildingLabel, seed, seedMeta, currentUsername, onBack, onSaved,
}: Props) {
  const [composition, setComposition] = useState<CockpitComposition>(seed)
  const [editingWidgetId, setEditingWidgetId] = useState<string | null>(null)
  const [availableCategories, setAvailableCategories] = useState<string[]>([])
  const [floors, setFloors] = useState<Floor[]>([])
  const [typologies, setTypologies] = useState<Typology[]>([])
  const [floorRooms, setFloorRooms] = useState<TypologyRoom[]>([])
  const [selectedFloorForRooms, setSelectedFloorForRooms] = useState('')
  const [saveOpen, setSaveOpen] = useState(false)
  const [saveMeta, setSaveMeta] = useState<{ name: string; description: string; category: string; tags: string }>({
    name: seedMeta?.name ?? '', description: seedMeta?.description ?? '',
    category: seedMeta?.category ?? '', tags: (seedMeta?.tags ?? []).join(', '),
  })
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const canEditExisting = !!seedMeta?.id && seedMeta.owner_username === currentUsername

  // Colonne gauche + multi-select des widgets : dérivés de l'espace courant,
  // jamais une liste statique.
  useEffect(() => {
    let cancelled = false
    getEquipmentScope(composition.space.ids)
      .then((cats) => {
        if (!cancelled) setAvailableCategories(cats.map((c) => c.category).filter((c) => c !== '_unknown'))
      })
      .catch(() => {
        if (!cancelled) setAvailableCategories([])
      })
    return () => {
      cancelled = true
    }
  }, [composition.space.ids.join(',')])

  useEffect(() => {
    getFloors(zone).then(setFloors).catch(() => setFloors([]))
    getTypologies(buildingId).then(setTypologies).catch(() => setTypologies([]))
  }, [zone, buildingId])

  useEffect(() => {
    if (!selectedFloorForRooms) {
      setFloorRooms([])
      return
    }
    getRoomsByFloor(selectedFloorForRooms).then(setFloorRooms).catch(() => setFloorRooms([]))
  }, [selectedFloorForRooms])

  const updateSpaceLevel = (level: SpaceLevel) => {
    if (level === 'batiment') {
      setComposition((c) => ({ ...c, space: { level, ids: [buildingId], label: buildingLabel, typologyName: null } }))
    } else {
      // Les niveaux Étage/Type de pièce/Pièce nécessitent une sélection
      // supplémentaire -- l'espace reste vide (aucun widget ne peut charger
      // de données) tant que l'utilisateur n'a pas choisi une valeur.
      setComposition((c) => ({ ...c, space: { level, ids: [], label: '', typologyName: null } }))
    }
  }

  const selectFloor = (floorId: string) => {
    const floor = floors.find((f) => f.global_id === floorId)
    setComposition((c) => ({ ...c, space: { level: 'etage', ids: floorId ? [floorId] : [], label: floor?.name ?? '', typologyName: null } }))
  }

  const selectTypology = async (name: string) => {
    if (!name) {
      setComposition((c) => ({ ...c, space: { level: 'typologie', ids: [], label: '', typologyName: null } }))
      return
    }
    try {
      const rooms = await getRoomsByTypology(buildingId, name)
      setComposition((c) => ({
        ...c,
        space: { level: 'typologie', ids: rooms.map((r) => r.room_id), label: name, typologyName: name },
      }))
    } catch {
      setComposition((c) => ({ ...c, space: { level: 'typologie', ids: [], label: name, typologyName: name } }))
    }
  }

  const selectRoom = (roomId: string) => {
    const room = floorRooms.find((r) => r.room_id === roomId)
    setComposition((c) => ({ ...c, space: { level: 'piece', ids: roomId ? [roomId] : [], label: room?.room_name ?? '', typologyName: null } }))
  }

  const addWidget = (type: WidgetType) => {
    setComposition((c) => ({ ...c, widgets: [...c.widgets, defaultWidget(type)] }))
  }

  const removeWidget = (id: string) => {
    setComposition((c) => ({ ...c, widgets: c.widgets.filter((w) => w.id !== id) }))
    if (editingWidgetId === id) setEditingWidgetId(null)
  }

  const updateWidget = (id: string, next: typeof composition.widgets[number]) => {
    setComposition((c) => ({ ...c, widgets: c.widgets.map((w) => (w.id === id ? next : w)) }))
  }

  const handleSave = async () => {
    if (!saveMeta.name.trim()) {
      setSaveError('Le nom est obligatoire.')
      return
    }
    setSaving(true)
    setSaveError(null)
    try {
      const body: CockpitInput = {
        name: saveMeta.name.trim(),
        description: saveMeta.description.trim(),
        category: saveMeta.category.trim(),
        tags: saveMeta.tags.split(',').map((t) => t.trim()).filter(Boolean),
        composition,
      }
      if (canEditExisting && seedMeta?.id) {
        await updateCockpit(seedMeta.id, body)
      } else {
        await createCockpit(body)
      }
      onSaved()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Erreur inattendue')
    } finally {
      setSaving(false)
    }
  }

  const editingWidget = composition.widgets.find((w) => w.id === editingWidgetId) ?? null

  return (
    <div style={{ marginTop: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <button type="button" className="btn btn-secondary" onClick={onBack}>← Bibliothèque</button>
        <h3 style={{ margin: 0 }}>Constructeur de cockpit</h3>
        <button type="button" className="btn btn-primary" style={{ marginLeft: 'auto' }} onClick={() => setSaveOpen(true)}>
          Enregistrer
        </button>
      </div>

      {/* Barre du haut : Période / Espace / Comparer */}
      <div className="panel" style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 16 }}>
        <div>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>Période</label>
          <div style={{ display: 'flex', gap: 4 }}>
            {PERIOD_OPTIONS.map((opt) => (
              <button
                key={opt.value} type="button"
                className={composition.period.preset === opt.value ? 'btn btn-primary' : 'btn btn-secondary'}
                style={{ padding: '5px 10px', fontSize: 12 }}
                onClick={() => setComposition((c) => ({ ...c, period: { preset: opt.value } }))}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text)', marginBottom: 4 }}>Espace</label>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <select className="input" style={{ fontSize: 12, padding: '5px 8px' }} value={composition.space.level} onChange={(e) => updateSpaceLevel(e.target.value as SpaceLevel)}>
              {SPACE_LEVEL_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
            </select>

            {composition.space.level === 'etage' && (
              <select className="input" style={{ fontSize: 12, padding: '5px 8px' }} value={composition.space.ids[0] ?? ''} onChange={(e) => selectFloor(e.target.value)}>
                <option value="">Choisir un étage...</option>
                {floors.map((f) => <option key={f.global_id} value={f.global_id}>{f.name}</option>)}
              </select>
            )}

            {composition.space.level === 'typologie' && (
              <select className="input" style={{ fontSize: 12, padding: '5px 8px' }} value={composition.space.typologyName ?? ''} onChange={(e) => selectTypology(e.target.value)}>
                <option value="">Choisir un type de pièce...</option>
                {typologies.map((t) => <option key={t.name} value={t.name}>{t.name} ({t.room_count})</option>)}
              </select>
            )}

            {composition.space.level === 'piece' && (
              <>
                <select className="input" style={{ fontSize: 12, padding: '5px 8px' }} value={selectedFloorForRooms} onChange={(e) => setSelectedFloorForRooms(e.target.value)}>
                  <option value="">Choisir un étage...</option>
                  {floors.map((f) => <option key={f.global_id} value={f.global_id}>{f.name}</option>)}
                </select>
                <select className="input" style={{ fontSize: 12, padding: '5px 8px' }} value={composition.space.ids[0] ?? ''} onChange={(e) => selectRoom(e.target.value)} disabled={!selectedFloorForRooms}>
                  <option value="">Choisir une pièce...</option>
                  {floorRooms.map((r) => <option key={r.room_id} value={r.room_id}>{r.room_name}</option>)}
                </select>
              </>
            )}
          </div>
        </div>

        <div style={{ marginLeft: 'auto', alignSelf: 'flex-end' }}>
          <button type="button" className="btn btn-secondary" disabled title="Bientôt disponible">+ Comparer une période</button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr 180px', gap: 16, alignItems: 'start' }}>
        {/* Colonne gauche */}
        <div className="panel">
          <h3 style={{ fontSize: 12 }}>Données disponibles</h3>
          {availableCategories.length === 0 && <p className="panel-hint" style={{ fontSize: 11 }}>Aucune donnée pour cet espace.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {availableCategories.map((cat) => (
              <div key={cat} style={{ fontSize: 12, color: 'var(--text)' }}>• {categoryLabel(cat)}</div>
            ))}
          </div>
        </div>

        {/* Canevas central */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
          {composition.widgets.length === 0 && (
            <p className="panel-hint">Ajoutez un widget avec les boutons à droite.</p>
          )}
          {composition.widgets.map((widget) => (
            <WidgetBlock
              key={widget.id}
              widget={widget}
              space={composition.space}
              period={composition.period.preset}
              onEdit={() => setEditingWidgetId(widget.id)}
              onRemove={() => removeWidget(widget.id)}
            />
          ))}
        </div>

        {/* Colonne droite */}
        <div className="panel">
          <h3 style={{ fontSize: 12 }}>Ajouter un widget</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {WIDGET_TYPE_OPTIONS.map((opt) => (
              <button key={opt.value} type="button" className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => addWidget(opt.value)}>
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {editingWidget && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 20 }} onClick={() => setEditingWidgetId(null)}>
          {/* `height` explicite (pas seulement `maxHeight`) -- WidgetSettingsPanel
              se positionne en `inset:0` sur cette boîte : sans hauteur définie ici,
              cette contrainte ne se résolvait à (quasi) rien, rendant le panneau
              invisible/minuscule (bug remonté par l'utilisateur). */}
          <div
            style={{ position: 'absolute', top: '15%', left: '50%', transform: 'translateX(-50%)', width: 320, height: '70vh' }}
            onClick={(e) => e.stopPropagation()}
          >
            <WidgetSettingsPanel
              widget={editingWidget}
              availableCategories={availableCategories}
              spaceIds={composition.space.ids}
              onChange={(next) => updateWidget(editingWidget.id, next)}
              onClose={() => setEditingWidgetId(null)}
            />
          </div>
        </div>
      )}

      {saveOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 30 }} onClick={() => setSaveOpen(false)}>
          <div className="panel" style={{ width: 360, background: 'var(--bg)' }} onClick={(e) => e.stopPropagation()}>
            <h3>Enregistrer le cockpit</h3>
            {saveError && <p className="panel-hint" style={{ color: '#e53e3e' }}>{saveError}</p>}
            <label style={{ display: 'block', fontSize: 11, marginBottom: 4 }}>Nom</label>
            <input className="input" style={{ width: '100%', marginBottom: 10 }} value={saveMeta.name} onChange={(e) => setSaveMeta((m) => ({ ...m, name: e.target.value }))} />
            <label style={{ display: 'block', fontSize: 11, marginBottom: 4 }}>Description</label>
            <input className="input" style={{ width: '100%', marginBottom: 10 }} value={saveMeta.description} onChange={(e) => setSaveMeta((m) => ({ ...m, description: e.target.value }))} />
            <label style={{ display: 'block', fontSize: 11, marginBottom: 4 }}>Catégorie</label>
            <input className="input" style={{ width: '100%', marginBottom: 10 }} placeholder="Énergie, Occupation, Confort..." value={saveMeta.category} onChange={(e) => setSaveMeta((m) => ({ ...m, category: e.target.value }))} />
            <label style={{ display: 'block', fontSize: 11, marginBottom: 4 }}>Tags (séparés par des virgules)</label>
            <input className="input" style={{ width: '100%', marginBottom: 14 }} value={saveMeta.tags} onChange={(e) => setSaveMeta((m) => ({ ...m, tags: e.target.value }))} />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" className="btn btn-secondary" onClick={() => setSaveOpen(false)}>Annuler</button>
              <button type="button" className="btn btn-primary" disabled={saving} onClick={handleSave}>
                {saving ? 'Enregistrement...' : 'Enregistrer'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
