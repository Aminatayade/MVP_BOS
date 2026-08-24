/**
 * FILE: src/App.jsx
 * DESCRIPTION: Composant principal de l'application Web BOS Light.
 *              Gère l'authentification OIDC via Keycloak, le contrôle d'accès strict (RBAC),
 *              ainsi que le chargement et l'affichage dynamique des cartouches et étapes du workflow.
 *
 * INPUTS:
 *   - Jetons de session Keycloak (keycloak.token)
 *   - Route API Gateway : GET /api/config-access (Vérification des rôles)
 *   - Route API Gateway : GET /api/v1/phases (Récupération des cartouches & étapes métier)
 *
 * OUTPUTS:
 *   - Interface utilisateur React affichant l'état de connexion, la réponse de test API,
 *     les cartes dynamiques des phases A, B, C ou l'écran d'accès refusé pour le rôle Client.
 *
 * DEPENDENCIES:
 *   - react (useEffect, useState, useRef)
 *   - ./services/keycloak (Instance Keycloak-js)
 *   - ./services/apiClient (Instance Axios configurée avec intercepteur JWT)
 *   - ./ProtectedRoute (Composant de contrôle des accès applicatifs)
 *
 * NOTES / PRÉREQUIS:
 *   - L'API Gateway (FastAPI) et Keycloak doivent être opérationnels.
 */

import { useEffect, useState, useRef } from 'react';
import keycloak from './services/keycloak';
import apiClient from './services/apiClient';
import { ProtectedRoute } from './ProtectedRoute';
import BimExplorer from './features/bim/BimExplorer';

function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [apiResponse, setApiResponse] = useState('');
  const [phases, setPhases] = useState([]);
  const [loadingPhases, setLoadingPhases] = useState(false);
  const isRun = useRef(false);

  useEffect(() => {
    // Empêche le double appel au démarrage lié au Strict Mode de React
    if (isRun.current) return;
    isRun.current = true;

    // Initialise la session Keycloak
    keycloak.init({ onLoad: 'check-sso' }).then((auth) => {
      setAuthenticated(auth);
      if (auth) {
        // Charge automatiquement les phases BDD si l'utilisateur est authentifié et autorisé
        fetchPhases();
      }
    }).catch((err) => {
      console.error('Erreur lors de la connexion Keycloak', err);
    });
  }, []);

  /**
   * Effectue un appel sécurisé à l'API Gateway pour récupérer la liste
   * des phases, cartouches et livrables depuis la base de données métier PostgreSQL.
   */
  const fetchPhases = async () => {
    setLoadingPhases(true);
    try {
      const response = await apiClient.get('/api/v1/phases');
      setPhases(response.data);
    } catch (error) {
      console.error('Erreur lors du chargement des phases métier :', error);
    } finally {
      setLoadingPhases(false);
    }
  };

  /**
   * Fonction de contrôle pour tester l'accès RBAC à la route sécurisée de l'API Gateway.
   */
  const testApiGateway = async () => {
    try {
      const response = await apiClient.get('/api/config-access');
      setApiResponse(JSON.stringify(response.data, null, 2));
    } catch (error) {
      setApiResponse(`Erreur (${error.response?.status || 'Inconnue'}) : ${JSON.stringify(error.response?.data || error.message)}`);
    }
  };

  return (
    <div className="page">
      <header className="navbar">
        <div className="navbar__brand">
          <span className="navbar__brand-mark">B</span>
          BOS Light
        </div>
        {authenticated && (
          <div className="navbar__user">
            <span>
              Connecté en tant que <strong>{keycloak.tokenParsed?.preferred_username}</strong>
            </span>
            <button className="btn btn-ghost btn-sm" style={{ color: '#cbd8e8' }} onClick={testApiGateway}>
              Tester l'accès API
            </button>
            <button className="btn btn-outline btn-sm" style={{ background: 'transparent', color: '#cbd8e8', borderColor: '#2c4c6c' }} onClick={() => keycloak.logout()}>
              Se déconnecter
            </button>
          </div>
        )}
      </header>

      <main className="content">
        {!authenticated ? (
          <div className="card card--pad login-panel">
            <h2 style={{ marginBottom: 8 }}>Bienvenue</h2>
            <p className="muted" style={{ marginBottom: 20 }}>
              Connectez-vous avec votre compte Keycloak pour accéder au configurateur.
            </p>
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => keycloak.login()}>
              Se connecter avec Keycloak
            </button>
          </div>
        ) : (
          <ProtectedRoute keycloak={keycloak}>
            <div>
              {apiResponse && (
                <div className="section card card--pad">
                  <h3 style={{ fontSize: 14, marginBottom: 10 }}>Réponse de l'API Gateway</h3>
                  <pre style={{
                    background: 'var(--navy-900)', color: '#8fe3b0', padding: '14px',
                    borderRadius: 'var(--radius-sm)', overflowX: 'auto', fontSize: 12, margin: 0,
                  }}>
                    {apiResponse}
                  </pre>
                </div>
              )}

              <section className="section">
                <div className="section-header">
                  <div>
                    <div className="eyebrow">Workflow du projet</div>
                    <h2>Cartouches &amp; étapes</h2>
                  </div>
                </div>

                {loadingPhases ? (
                  <p className="muted">Chargement des phases métier…</p>
                ) : (
                  <div className="phase-grid">
                    {phases.map((phase) => (
                      <div key={phase.id} className="phase-card">
                        <div className="phase-card__bar" style={{ background: phase.badge_color || 'var(--blue-500)' }} />
                        <div className="phase-card__body">
                          <span className="badge" style={{ background: phase.badge_color || 'var(--blue-500)', alignSelf: 'flex-start' }}>
                            {phase.nom}
                          </span>
                          <h3 className="phase-card__title">{phase.titre}</h3>
                          <p className="phase-card__desc">{phase.description}</p>

                          <ul className="phase-card__points">
                            {phase.cartouche_points?.map((point, idx) => (
                              <li key={idx}>{point}</li>
                            ))}
                          </ul>

                          <div className="phase-card__footer">
                            <strong>{phase.etape_nom}</strong>
                            <p>{phase.livrables}</p>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="section">
                <BimExplorer />
              </section>
            </div>
          </ProtectedRoute>
        )}
      </main>
    </div>
  );
}

export default App;
