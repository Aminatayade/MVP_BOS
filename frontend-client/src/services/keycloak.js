/**
 * Service d'authentification Keycloak
 * ------------------------------------
 * Rôle : Initialise l'instance du client Keycloak-JS en utilisant les variables
 *        d'environnement (.env). Permet de gérer la connexion, la déconnexion,
 *        la récupération des informations utilisateur et la gestion des tokens.
 * Auteur : Équipe Développement Frontend
 */

import Keycloak from 'keycloak-js';

const keycloak = new Keycloak({
  url: import.meta.env.VITE_KEYCLOAK_URL,
  realm: import.meta.env.VITE_KEYCLOAK_REALM,
  clientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID,
});

export default keycloak;
