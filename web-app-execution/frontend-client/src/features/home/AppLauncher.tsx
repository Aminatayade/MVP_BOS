/**
 * FILE: features/home/AppLauncher.tsx
 * DESCRIPTION: Écran d'accueil de BOS Exécution -- catalogue des apps
 *              disponibles (inspiré de la page "Toutes les applications" de
 *              SpinalCore SmartEvolution : cartes avec titre, description et
 *              tags), présenté AVANT d'entrer dans une app en particulier
 *              (Jumeau numérique, Cockpit Énergie, Maintenance, Suivi des
 *              usages).
 */

export type AppId = 'jumeau' | 'cockpit' | 'maintenance' | 'occupancy'

type AppCard = {
  id: AppId
  icon: string
  title: string
  description: string
  tags: string[]
  disabled?: boolean
}

const APPS: AppCard[] = [
  {
    id: 'jumeau',
    icon: '🏗️',
    title: 'Jumeau numérique',
    description: "Visualisez la maquette 3D d'une zone déjà ingérée depuis le Configurateur.",
    tags: ['3D', 'MAQUETTE'],
  },
  {
    id: 'cockpit',
    icon: '⚡',
    title: 'Cockpit Énergie',
    description: 'Pilotez et analysez la consommation énergétique par bâtiment, étage ou pièce.',
    tags: ['ÉNERGIE', 'COCKPIT'],
  },
  {
    id: 'maintenance',
    icon: '🛠️',
    title: 'Maintenance',
    description: "Statistiques d'interventions par métier, priorité et zone.",
    tags: ['MAINTENANCE', 'COCKPIT'],
  },
  {
    id: 'occupancy',
    icon: '👥',
    title: 'Suivi des usages',
    description: "Compréhension du taux d'usage des espaces (occupation, réservation).",
    tags: ['OCCUPATION', 'COCKPIT'],
  },
]

type Props = {
  onSelect: (app: AppId) => void
}

export default function AppLauncher({ onSelect }: Props) {
  return (
    <div className="panel" style={{ marginTop: 24 }}>
      <h3>Applications</h3>
      <p className="panel-hint">Choisissez une application pour commencer.</p>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
          gap: 14,
          marginTop: 14,
        }}
      >
        {APPS.map((app) => (
          <button
            key={app.id}
            type="button"
            disabled={app.disabled}
            onClick={() => !app.disabled && onSelect(app.id as AppId)}
            style={{
              textAlign: 'left',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              padding: 16,
              borderRadius: 10,
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              cursor: app.disabled ? 'not-allowed' : 'pointer',
              opacity: app.disabled ? 0.55 : 1,
              fontFamily: 'var(--sans)',
            }}
          >
            <span style={{ fontSize: 26 }}>{app.icon}</span>
            <span style={{ fontWeight: 600, fontSize: 15, color: 'var(--text-h)' }}>{app.title}</span>
            <span style={{ fontSize: 12.5, color: 'var(--text-m, #6b7280)', lineHeight: 1.4 }}>
              {app.description}
            </span>
            <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
              {app.tags.map((tag) => (
                <span
                  key={tag}
                  style={{
                    fontSize: 10.5,
                    fontWeight: 600,
                    letterSpacing: 0.3,
                    color: '#1d4ed8',
                    background: '#dbeafe',
                    borderRadius: 4,
                    padding: '2px 6px',
                  }}
                >
                  {tag}
                </span>
              ))}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
