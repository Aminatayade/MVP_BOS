/**
 * FILE: features/occupancy/components/SpaceListLinkButton.tsx
 * DESCRIPTION: Point d'entrée seulement (notes client) -- la liste détaillée
 *              des espaces en dépassement de seuil n'est PAS implémentée
 *              (hors périmètre de ce cockpit). Le dashboard calcule déjà
 *              `spaces[]` par salle (voir OccupancyDashboard), prêt à
 *              alimenter cet écran quand il sera construit.
 */
export default function SpaceListLinkButton() {
  return (
    <div className="panel">
      <strong style={{ fontSize: 13, color: 'var(--text-h)' }}>Liste des espaces</strong>
      <p className="panel-hint" style={{ marginTop: 6 }}>Bientôt disponible.</p>
      <button type="button" className="btn btn-secondary" disabled style={{ marginTop: 8 }}>
        Voir les espaces au-delà du seuil
      </button>
    </div>
  )
}
