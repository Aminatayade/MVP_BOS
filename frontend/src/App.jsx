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
    <div style={{ padding: '40px', fontFamily: 'Arial, sans-serif', maxWidth: '1000px', margin: '0 auto' }}>
      <h1>🚀 BOS Light - Application Web</h1>
      <hr style={{ margin: '20px 0' }} />

      {!authenticated ? (
        <div>
          <p>Vous n'êtes pas connecté.</p>
          <button 
            onClick={() => keycloak.login()}
            style={{ padding: '10px 20px', fontSize: '16px', backgroundColor: '#007bff', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
          >
            Se connecter avec Keycloak
          </button>
        </div>
      ) : (
        <ProtectedRoute keycloak={keycloak}>
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <p>🟢 Connecté en tant que : <strong>{keycloak.tokenParsed?.preferred_username}</strong></p>
              <div>
                <button 
                  onClick={testApiGateway} 
                  style={{ padding: '8px 15px', marginRight: '10px', fontSize: '14px', cursor: 'pointer' }}
                >
                  Tester l'accès API
                </button>
                
                <button 
                  onClick={() => keycloak.logout()} 
                  style={{ padding: '8px 15px', fontSize: '14px', backgroundColor: '#dc3545', color: 'white', border: 'none', borderRadius: '5px', cursor: 'pointer' }}
                >
                  Se déconnecter
                </button>
              </div>
            </div>

            {/* Affichage de la réponse de test API Gateway */}
            {apiResponse && (
              <div style={{ marginTop: '20px' }}>
                <h3>Réponse de l'API Gateway :</h3>
                <pre style={{ background: '#222', color: '#00ff00', padding: '15px', borderRadius: '5px', overflowX: 'auto' }}>
                  {apiResponse}
                </pre>
              </div>
            )}

            <hr style={{ margin: '30px 0' }} />

            {/* Section d'affichage dynamique des cartouches BDD */}
            <h2>📌 Cartouches & Workflow du Projet</h2>
            
            {loadingPhases ? (
              <p>Chargement des phases métier depuis la BDD...</p>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px', marginTop: '20px' }}>
                {phases.map((phase) => (
                  <div 
                    key={phase.id} 
                    style={{ 
                      border: '1px solid #ccc', 
                      borderRadius: '8px', 
                      padding: '20px', 
                      backgroundColor: '#f9f9f9',
                      boxShadow: '0 2px 5px rgba(0,0,0,0.05)'
                    }}
                  >
                    <span 
                      style={{ 
                        backgroundColor: phase.badge_color || '#007bff', 
                        color: 'white', 
                        padding: '4px 10px', 
                        borderRadius: '12px', 
                        fontSize: '12px', 
                        fontWeight: 'bold' 
                      }}
                    >
                      {phase.nom}
                    </span>
                    
                    <h3 style={{ marginTop: '15px', marginBottom: '10px' }}>{phase.titre}</h3>
                    <p style={{ fontSize: '14px', color: '#555' }}>{phase.description}</p>
                    
                    <h4 style={{ fontSize: '13px', marginTop: '15px', marginBottom: '5px' }}>Points clés :</h4>
                    <ul style={{ paddingLeft: '20px', fontSize: '13px' }}>
                      {phase.cartouche_points?.map((point, idx) => (
                        <li key={idx}>{point}</li>
                      ))}
                    </ul>

                    <div style={{ marginTop: '20px', padding: '10px', backgroundColor: '#eef2ff', borderRadius: '5px', borderLeft: `4px solid ${phase.badge_color}` }}>
                      <strong style={{ fontSize: '13px', color: '#333' }}>{phase.etape_nom}</strong>
                      <p style={{ fontSize: '12px', color: '#666', margin: '5px 0 0 0' }}>{phase.livrables}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <BimExplorer />
          </div>
        </ProtectedRoute>
      )}
    </div>
  );
}

export default App;