/**
 * FILE: features/occupancy/components/HeatmapLinkButton.tsx
 * DESCRIPTION: Widget 4 -- point d'entrée seulement, la carte de chaleur par
 *              étage n'est PAS implémentée (hors périmètre de ce cockpit).
 */
export default function HeatmapLinkButton() {
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Carte de chaleur par étage</strong>
      <p className="panel-hint" style={{ marginTop: 6 }}>Bientôt disponible.</p>
      <button type="button" className="btn btn-secondary" disabled style={{ marginTop: 8 }}>
        Ouvrir la carte de chaleur
      </button>
    </div>
  )
}
