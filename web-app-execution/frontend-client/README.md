# Frontend Client — BOS Exécution

Interface web de **BOS Exécution** (React / Vite) — voir le [README racine](../../README.md)
pour l'architecture complète (backend partagé, les deux frontends, RBAC).

## Ce que fait cette app

Consultation des données du bâtiment par les personas propriétaires/exploitants : jumeau
numérique et cockpits de pilotage. **État actuel : squelette** — connexion Keycloak (client
OIDC `web-app-client`) et un bouton de test de l'accès API (`/api/execution-access`) ; le
contenu métier (jumeau numérique, cockpits) reste à construire.

## Port

`5174` — servi par le conteneur `bos_frontend_client`.

## Lancer en local (hors Docker, hot-reload)

```bash
cd web-app-execution/frontend-client
npm install
npm run dev -- --port 5174
```

Nécessite l'API Gateway (`bos_api_gateway`, port 8000) et Keycloak (`bos_keycloak`, port
8080) déjà démarrés — voir le README racine pour la commande `docker compose`. C'est le
**même** backend que celui utilisé par `frontend/` (BOS Configurateur).

## Rôles Keycloak autorisés

Administrateur, Configurateur, **Client**.

## Historique

Ce dossier a été copié depuis le dépôt `web-app-execution` (branche `client`), qui reste
disponible séparément comme référence historique mais n'est plus synchronisé avec ce
dépôt — tout développement actif se fait ici.
