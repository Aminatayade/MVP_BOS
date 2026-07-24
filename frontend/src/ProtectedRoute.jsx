/**
 * FILE: src/ProtectedRoute.jsx
 * DESCRIPTION: Composant d'enveloppement (Wrapper) React assurant le verrouillage strict 
 *              des accès applicatifs selon le modèle RBAC de Keycloak.
 *
 * INPUTS:
 *   - Instance Keycloak active (passée en prop `keycloak`)
 *   - Composants enfants React (`children`) à afficher en cas d'accès autorisé
 *
 * OUTPUTS:
 *   - Si l'utilisateur possède le rôle 'Administrateur' ou 'Configurateur' : Affiche le contenu protégé.
 *   - Si l'utilisateur possède uniquement le rôle 'Client' ou un rôle non autorisé : Affiche un écran "403 Accès Refusé".
 *
 * DEPENDENCIES:
 *   - react
 *
 * NOTES / PRÉREQUIS:
 *   - La session Keycloak doit être initialisée avant l'évaluation des rôles.
 */

import React from 'react';

// Liste stricte des rôles Keycloak autorisés à accéder au Configurateur Web
const ALLOWED_ROLES = ['Administrateur', 'Configurateur'];

export const ProtectedRoute = ({ keycloak, children }) => {
  // Extraction des rôles rattachés à la session utilisateur du Realm Keycloak
  const userRoles = keycloak.realmAccess?.roles || [];

  // Vérification de la présence d'au moins un rôle valide
  const hasAccess = ALLOWED_ROLES.some((role) => userRoles.includes(role));

  // Écran d'interdiction d'accès (Cas du rôle Client)
  if (!hasAccess) {
    return (
      <div 
        style={{ 
          padding: '30px', 
          marginTop: '20px', 
          textAlign: 'center', 
          backgroundColor: '#fff5f5', 
          border: '1px solid #feb2b2', 
          borderRadius: '8px' 
        }}
      >
        <h2 style={{ color: '#c53030', margin: '0 0 10px 0' }}>🚫 Accès Refusé</h2>
        <p style={{ color: '#4a5568', fontSize: '15px' }}>
          Vous êtes connecté en tant que <strong>{keycloak.tokenParsed?.preferred_username}</strong>.
        </p>
        <p style={{ color: '#718096', fontSize: '14px' }}>
          Votre rôle actuel ne vous permet pas d'accéder à cette application de configuration.<br />
          Seuls les utilisateurs disposant des rôles <strong>Administrateur</strong> ou <strong>Configurateur</strong> sont autorisés.
        </p>
        <button 
          onClick={() => keycloak.logout()} 
          style={{ 
            marginTop: '15px', 
            padding: '8px 16px', 
            backgroundColor: '#e53e3e', 
            color: 'white', 
            border: 'none', 
            borderRadius: '5px', 
            cursor: 'pointer' 
          }}
        >
          Se déconnecter
        </button>
      </div>
    );
  }

  // Si le rôle est validé, affichage normal de l'application
  return children;
};