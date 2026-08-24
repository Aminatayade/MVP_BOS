# Frontend — BOS Configurateur

Interface web du **Configurateur BOS Light** (React / Vite) — voir le [README racine](../../README.md)
pour l'architecture complète (backend partagé, les deux frontends, RBAC).

## Ce que fait cette app

- Connexion Keycloak (client OIDC `web-app-frontend`)
- Workflow du projet — cartouches et étapes métier
- Import & validation de maquettes IFC (une ou plusieurs disciplines)
- Explorateur BIM : visualisation du graphe spatial (Cytoscape) et visualisation 3D du
  jumeau numérique (ThatOpenEngine)

## Port

`5173` — servi par le conteneur `bos_frontend`.

## Lancer en local (hors Docker, hot-reload)

```bash
cd web-app-configuration/frontend
npm install
npm run dev
```

Nécessite l'API Gateway (`bos_api_gateway`, port 8000) et Keycloak (`bos_keycloak`, port
8080) déjà démarrés — voir le README racine pour la commande `docker compose`.

## Rôles Keycloak autorisés

Administrateur, Configurateur. Le rôle Client reçoit un écran 403 (c'est BOS Exécution,
`frontend-client/`, qui lui est destiné).
