/**
 * Client HTTP Axios Sécurisé (API Gateway)
 * ----------------------------------------
 * Rôle : Intercepte toutes les requêtes HTTP envoyées vers l'API Gateway Python (FastAPI).
 *        Il vérifie la validité du token JWT Keycloak, le rafraîchit si besoin,
 *        et l'injecte automatiquement dans l'en-tête 'Authorization: Bearer <token>'.
 * Auteur : Équipe Développement Frontend
 */

import axios from 'axios';
import keycloak from './keycloak';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_GATEWAY_URL,
});

apiClient.interceptors.request.use(
  async (config) => {
    if (keycloak.token) {
      try {
        // Rafraîchit le token s'il expire dans moins de 30 secondes
        await keycloak.updateToken(30);
      } catch (error) {
        console.error('Erreur lors du rafraîchissement du token JWT', error);
        keycloak.login();
      }
      // Injection du badge JWT dans la requête
      config.headers.Authorization = `Bearer ${keycloak.token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

export default apiClient;
