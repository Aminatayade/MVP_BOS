/**
 * FILE: src/App.jsx
 * DESCRIPTION: Composant principal de l'application Web BOS Exécution (Web App Client).
 *              Gère l'authentification OIDC via Keycloak et le contrôle d'accès (RBAC)
 *              ouvert aux rôles Administrateur, Configurateur et Client.
 *
 * INPUTS:
 *   - Jetons de session Keycloak (keycloak.token)
 *   - Route API Gateway : GET /api/execution-access (Vérification des rôles)
 *
 * OUTPUTS:
 *   - Interface utilisateur React affichant l'état de connexion et la réponse de test API.
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
import BimViewer from './features/bim/BimViewer';
import './App.css';

const DISPLAYED_ROLES = ['Administrateur', 'Configurateur', 'Client'];

function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [apiResponse, setApiResponse] = useState('');
  const isRun = useRef(false);

  useEffect(() => {
    // Empêche le double appel au démarrage lié au Strict Mode de React
    if (isRun.current) return;
    isRun.current = true;

    // Initialise la session Keycloak
    keycloak.init({ onLoad: 'check-sso' }).then((auth) => {
      setAuthenticated(auth);
    }).catch((err) => {
      console.error('Erreur lors de la connexion Keycloak', err);
    });
  }, []);

  /**
   * Fonction de contrôle pour tester l'accès RBAC à la route sécurisée de l'API Gateway.
   */
  const testApiGateway = async () => {
    try {
      const response = await apiClient.get('/api/execution-access');
      setApiResponse(JSON.stringify(response.data, null, 2));
    } catch (error) {
      setApiResponse(`Erreur (${error.response?.status || 'Inconnue'}) : ${JSON.stringify(error.response?.data || error.message)}`);
    }
  };

  const username = keycloak.tokenParsed?.preferred_username || '';
  const initial = username.charAt(0).toUpperCase();
  const userRoles = (keycloak.realmAccess?.roles || []).filter((role) => DISPLAYED_ROLES.includes(role));

  return (
    <div>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">🏢</span>
          BOS Exécution
          <span className="brand-tag">Client</span>
        </div>

        {authenticated && (
          <div className="actions">
            <button className="btn btn-danger" onClick={() => keycloak.logout()}>
              Se déconnecter
            </button>
          </div>
        )}
      </header>

      <main className="content">
        {!authenticated ? (
          <div className="login-card">
            <div className="login-icon">🔐</div>
            <h2>Bienvenue sur BOS Exécution</h2>
            <p>
              Connectez-vous pour accéder aux données du bâtiment : jumeau numérique,
              cockpits de pilotage et suivi d'exploitation.
            </p>
            <button className="btn btn-primary" onClick={() => keycloak.login()}>
              Se connecter avec Keycloak
            </button>
          </div>
        ) : (
          <ProtectedRoute keycloak={keycloak}>
            <div>
              <div className="user-bar">
                <div className="user-info">
                  <div className="avatar">{initial}</div>
                  <div className="user-meta">
                    <span className="username">{username}</span>
                    <div className="role-badges">
                      {userRoles.map((role) => (
                        <span key={role} className="role-badge">{role}</span>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="actions">
                  <button className="btn btn-secondary" onClick={testApiGateway}>
                    Tester l'accès API
                  </button>
                </div>
              </div>

              <div className="panel">
                <h3>📡 Réponse de l'API Gateway</h3>
                <p className="panel-hint">
                  Vérifie que le rôle courant est bien accepté par la route
                  <code> /api/execution-access</code> de l'API Gateway.
                </p>
                {apiResponse ? (
                  <pre className="api-response">{apiResponse}</pre>
                ) : (
                  <p className="panel-hint">Aucun test lancé pour le moment.</p>
                )}
              </div>

              <BimViewer />
            </div>
          </ProtectedRoute>
        )}
      </main>

      <footer className="footer">BOS Exécution · Propulsé par BOS Light</footer>
    </div>
  );
}

export default App;
