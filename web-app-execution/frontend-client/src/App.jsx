/**
 * FILE: src/App.jsx
 * DESCRIPTION: Composant principal de l'application Web BOS Exécution (Web App Client).
 *              Gère l'authentification OIDC via Keycloak et le contrôle d'accès (RBAC)
 *              ouvert aux rôles Administrateur, Configurateur et Client. Une fois
 *              connecté, présente un écran d'accueil (AppLauncher) listant les apps
 *              disponibles avant d'entrer dans l'une d'elles.
 *
 * DEPENDENCIES:
 *   - react (useEffect, useState, useRef)
 *   - ./services/keycloak (Instance Keycloak-js)
 *   - ./ProtectedRoute (Composant de contrôle des accès applicatifs)
 *
 * NOTES / PRÉREQUIS:
 *   - L'API Gateway (FastAPI) et Keycloak doivent être opérationnels.
 */

import { useEffect, useState, useRef } from 'react';
import keycloak from './services/keycloak';
import { ProtectedRoute } from './ProtectedRoute';
import AppLauncher from './features/home/AppLauncher';
import JumeauNumeriqueApp from './features/bim/JumeauNumeriqueApp';
import CockpitEnergieApp from './features/cockpits/CockpitEnergieApp';
import MaintenanceApp from './features/maintenance/MaintenanceApp';
import OccupancyApp from './features/occupancy/OccupancyApp';
import EnergyConsumptionApp from './features/energy-consumption/EnergyConsumptionApp';
import './App.css';

const DISPLAYED_ROLES = ['Administrateur', 'Configurateur', 'Client'];

function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [activeApp, setActiveApp] = useState(null);
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
                {activeApp && (
                  <div className="actions">
                    <button className="btn btn-secondary" onClick={() => setActiveApp(null)}>
                      ← Applications
                    </button>
                  </div>
                )}
              </div>

              {!activeApp && <AppLauncher onSelect={setActiveApp} />}
              {activeApp === 'jumeau' && <JumeauNumeriqueApp />}
              {activeApp === 'cockpit' && <CockpitEnergieApp />}
              {activeApp === 'maintenance' && <MaintenanceApp />}
              {activeApp === 'occupancy' && <OccupancyApp />}
              {activeApp === 'energy-consumption' && <EnergyConsumptionApp />}
            </div>
          </ProtectedRoute>
        )}
      </main>

      <footer className="footer">BOS Exécution · Propulsé par BOS Light</footer>
    </div>
  );
}

export default App;
