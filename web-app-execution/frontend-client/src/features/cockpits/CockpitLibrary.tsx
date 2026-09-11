/**
 * FILE: features/cockpits/CockpitLibrary.tsx
 * DESCRIPTION: Écran 1 -- bibliothèque "Vos cockpits". Recherche, filtre par
 *              catégorie (dérivée des cockpits réellement présents, jamais
 *              une liste figée), "Mes cockpits"/"Partagés par l'équipe",
 *              favoris en localStorage. La carte "Consommation électrique"
 *              (ancien panneau fixe) est épinglée en haut de "Mes cockpits"
 *              -- ce n'est pas une vraie ligne Postgres, elle ne devient un
 *              cockpit possédé qu'au premier "Enregistrer" (fork-on-save).
 */
import { useEffect, useMemo, useState, type MouseEvent } from 'react'
import { deleteCockpit, listCockpits, type CockpitRecord } from './services/cockpitApi'
import { legacySeed, type CockpitComposition, type CockpitMeta } from './types'

const FAVORITES_KEY = 'cockpit-favorites'

function loadFavorites(): Set<string> {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY)
    return new Set(raw ? (JSON.parse(raw) as string[]) : [])
  } catch {
    return new Set()
  }
}

function saveFavorites(favs: Set<string>) {
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify([...favs]))
  } catch {
    // localStorage indisponible (navigation privée...) -- favoris non
    // persistés, pas une erreur bloquante pour le reste de la page.
  }
}

type Props = {
  buildingId: string
  buildingLabel: string
  currentUsername?: string
  onOpenCockpit: (composition: CockpitComposition, meta: CockpitMeta) => void
  onCreateNew: () => void
}

export default function CockpitLibrary({ buildingId, buildingLabel, currentUsername, onOpenCockpit, onCreateNew }: Props) {
  const [cockpits, setCockpits] = useState<CockpitRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('Toutes')
  const [favorites, setFavorites] = useState<Set<string>>(() => loadFavorites())

  const refresh = () => {
    listCockpits()
      .then(setCockpits)
      .catch((err) => setError(err instanceof Error ? err.message : 'Erreur inattendue'))
  }

  useEffect(refresh, [])

  const toggleFavorite = (id: string) => {
    setFavorites((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      saveFavorites(next)
      return next
    })
  }

  const categories = useMemo(() => {
    const set = new Set<string>()
    for (const c of cockpits ?? []) if (c.category) set.add(c.category)
    return ['Toutes', ...Array.from(set).sort()]
  }, [cockpits])

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return (cockpits ?? []).filter((c) => {
      if (category !== 'Toutes' && c.category !== category) return false
      if (!term) return true
      return (
        c.name.toLowerCase().includes(term) ||
        (c.description ?? '').toLowerCase().includes(term) ||
        c.tags.some((t) => t.toLowerCase().includes(term))
      )
    })
  }, [cockpits, search, category])

  const mine = filtered.filter((c) => c.owner_username === currentUsername)
  const shared = filtered.filter((c) => c.owner_username !== currentUsername)

  const sortByFavorite = (list: CockpitRecord[]) =>
    [...list].sort((a, b) => Number(favorites.has(b.id)) - Number(favorites.has(a.id)))

  const openRecord = (record: CockpitRecord) => {
    onOpenCockpit(record.composition, {
      id: record.id, name: record.name, description: record.description ?? '',
      category: record.category ?? '', tags: record.tags, owner_username: record.owner_username,
    })
  }

  const handleDelete = async (id: string, event: MouseEvent) => {
    event.stopPropagation()
    if (!window.confirm('Supprimer définitivement ce cockpit ?')) return
    try {
      await deleteCockpit(id)
      refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur lors de la suppression')
    }
  }

  return (
    <div style={{ marginTop: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <h3 style={{ margin: '0 0 4px' }}>Vos cockpits</h3>
          <p className="panel-hint" style={{ margin: 0 }}>Composez, réutilisez et partagez vos cockpits de suivi.</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={onCreateNew}>+ Nouveau cockpit</button>
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <input
          type="text" placeholder="Rechercher un cockpit" value={search} onChange={(e) => setSearch(e.target.value)}
          className="input" style={{ flex: '1 1 260px' }}
        />
        <select className="input" style={{ width: 'auto' }} value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {error && <p className="panel-hint" style={{ color: '#e53e3e' }}>{error}</p>}
      {!error && cockpits === null && <p className="panel-hint">Chargement...</p>}

      {cockpits !== null && (
        <>
          <h4 style={{ fontSize: 12, letterSpacing: 0.5, textTransform: 'uppercase', color: 'var(--text)' }}>Mes cockpits</h4>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14, marginBottom: 24 }}>
            {/* Carte épinglée : ancien panneau "Consommation électrique" */}
            <div
              className="panel" style={{ cursor: 'pointer', border: '1px solid var(--accent-border, var(--border))' }}
              onClick={() => onOpenCockpit(legacySeed(buildingId, buildingLabel), { name: 'Consommation électrique', description: 'Indicateur + répartition par étage + tendance.', category: 'Énergie', tags: ['exemple'] })}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <strong style={{ fontSize: 13 }}>⚡ Consommation électrique</strong>
                <span style={{ fontSize: 10, color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 4, padding: '1px 5px' }}>Exemple</span>
              </div>
              <p className="panel-hint" style={{ fontSize: 12, margin: '6px 0' }}>Indicateur, barres, camembert, tendance.</p>
              <span style={{ fontSize: 11, color: 'var(--text)' }}>énergie</span>
            </div>

            {sortByFavorite(mine).map((c) => (
              <div key={c.id} className="panel" style={{ cursor: 'pointer' }} onClick={() => openRecord(c)}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <strong style={{ fontSize: 13 }}>{c.name}</strong>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button type="button" onClick={(e) => { e.stopPropagation(); toggleFavorite(c.id) }} style={starBtnStyle} title="Favori">
                      {favorites.has(c.id) ? '★' : '☆'}
                    </button>
                    <button type="button" onClick={(e) => handleDelete(c.id, e)} style={starBtnStyle} title="Supprimer">🗑</button>
                  </div>
                </div>
                {c.description && <p className="panel-hint" style={{ fontSize: 12, margin: '6px 0' }}>{c.description}</p>}
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {c.tags.map((t) => <span key={t} style={tagStyle}>{t}</span>)}
                </div>
              </div>
            ))}
            {mine.length === 0 && <p className="panel-hint">Aucun autre cockpit -- cliquez "+ Nouveau cockpit" ou la carte exemple ci-dessus.</p>}
          </div>

          <h4 style={{ fontSize: 12, letterSpacing: 0.5, textTransform: 'uppercase', color: 'var(--text)' }}>Partagés par l'équipe</h4>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14 }}>
            {sortByFavorite(shared).map((c) => (
              <div key={c.id} className="panel" style={{ cursor: 'pointer' }} onClick={() => openRecord(c)}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <strong style={{ fontSize: 13 }}>{c.name}</strong>
                  <button type="button" onClick={(e) => { e.stopPropagation(); toggleFavorite(c.id) }} style={starBtnStyle} title="Favori">
                    {favorites.has(c.id) ? '★' : '☆'}
                  </button>
                </div>
                {c.description && <p className="panel-hint" style={{ fontSize: 12, margin: '6px 0' }}>{c.description}</p>}
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
                  {c.tags.map((t) => <span key={t} style={tagStyle}>{t}</span>)}
                </div>
                <span style={{ fontSize: 11, color: 'var(--text)' }}>👤 {c.owner_username}</span>
              </div>
            ))}
            {shared.length === 0 && <p className="panel-hint">Aucun cockpit partagé par l'équipe pour l'instant.</p>}
          </div>
        </>
      )}
    </div>
  )
}

const starBtnStyle = { background: 'none', border: 'none', cursor: 'pointer', fontSize: 14, padding: 0 } as const
const tagStyle = {
  fontSize: 10, background: 'var(--accent-bg, rgba(99,102,241,0.12))', color: 'var(--accent, #6366f1)',
  borderRadius: 4, padding: '2px 6px',
} as const
