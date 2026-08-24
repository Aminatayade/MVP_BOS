/**
 * FILE: src/ProtectedRoute.jsx
 * DESCRIPTION: Composant d'enveloppement (Wrapper) React assurant le verrouillage strict
 *              des accès applicatifs selon le modèle RBAC de Keycloak pour l'application
 *              BOS Exécution (Web App Client).
 *
 * INPUTS:
 *   - Instance Keycloak active (passée en prop `keycloak`)
 *   - Composants enfants React (`children`) à afficher en cas d'accès autorisé
 *
 * OUTPUTS:
 *   - Si l'utilisateur possède le rôle 'Administrateur', 'Configurateur' ou 'Client' : Affiche le contenu protégé.
 *   - Si l'utilisateur ne possède aucun rôle autorisé : Affiche un écran "403 Accès Refusé".
 *
 * DEPENDENCIES:
 *   - react
 *
 * NOTES / PRÉREQUIS:
 *   - La session Keycloak doit être initialisée avant l'évaluation des rôles.
 */

// Liste stricte des rôles Keycloak autorisés à accéder au BOS Exécution
const ALLOWED_ROLES = ['Administrateur', 'Configurateur', 'Client'];

export const ProtectedRoute = ({ keycloak, children }) => {
  // Extraction des rôles rattachés à la session utilisateur du Realm Keycloak
  const userRoles = keycloak.realmAccess?.roles || [];

  // Vérification de la présence d'au moins un rôle valide
  const hasAccess = ALLOWED_ROLES.some((role) => userRoles.includes(role));

  // Écran d'interdiction d'accès
  if (!hasAccess) {
    return (
      <div className="access-denied">
        <h2>🚫 Accès Refusé</h2>
        <p>
          Vous êtes connecté en tant que <strong>{keycloak.tokenParsed?.preferred_username}</strong>.
        </p>
        <p>
          Votre rôle actuel ne vous permet pas d'accéder à cette application.<br />
          Seuls les utilisateurs disposant des rôles <strong>Administrateur</strong>, <strong>Configurateur</strong> ou <strong>Client</strong> sont autorisés.
        </p>
        <button className="btn btn-danger" style={{ marginTop: '15px' }} onClick={() => keycloak.logout()}>
          Se déconnecter
        </button>
      </div>
    );
  }

  // Si le rôle est validé, affichage normal de l'application
  return children;
};
