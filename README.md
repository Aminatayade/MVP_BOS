# 🚀 BOS Light MVP

**BOS Light MVP** est composé de trois applications distinctes : **BOS Configurateur**,
**BOS Exécution**, et **userapp** (pas encore démarrée). Ce dépôt unique héberge les deux
premières — deux frontends séparés (`frontend/` et `frontend-client/`) qui partagent un seul
et même backend (Keycloak, PostgreSQL ×2, API Gateway, ArcadeDB, NATS, SeaweedFS). C'est
l'architecture cible décrite dans la roadmap : Configurateur et Exécution ne sont **pas** deux
backends séparés qui se connectent l'un à l'autre, mais deux frontends sur un backend commun.

Ce dépôt regroupe aussi la fusion de l'application de configuration avec authentification &
RBAC (le MVP d'origine) **et** le moteur BIM — validation/ingestion de maquettes IFC,
visualisation du graphe spatial, et visualisation 3D du jumeau numérique (ThatOpenEngine). Un
seul `git clone` + `docker compose up` suffit pour faire tourner l'ensemble.

---

## 📐 Architecture globale

L'application repose sur **11 conteneurs** interconnectés :

| Service | Rôle | Port hôte |
| :--- | :--- | :--- |
| **frontend** (React / Vite) | Interface web BOS Configurateur — workflow, explorateur BIM (graphe + 3D) | `5173` |
| **frontend-client** (React / Vite) | Interface web BOS Exécution — jumeau numérique, cockpits de pilotage | `5174` |
| **api-gateway** (FastAPI) | Point d'entrée public unique côté backend, **partagé par les deux frontends**, RBAC Keycloak, proxy vers les services BIM | `8000` |
| **keycloak** (24) | IAM / identité, realm `bos-realm` (rôles Administrateur/Configurateur/Client) | `8080` |
| **postgres** | BDD Identity (Keycloak) | `5432` |
| **postgres-app** | BDD métier applicative (phases, cartouches) | `5433` |
| **bim_api** (`bim_validator`) | Validation IFC, ingestion du graphe spatial, téléchargement des maquettes | interne uniquement (via gateway) |
| **timeseries_api** (`bim_validator`) | Lecture des relevés temps réel liés aux équipements | interne uniquement (via gateway) |
| **arcadedb** | Graphe BIM (bâtiments/étages/pièces/équipements) + moteur time-series | `127.0.0.1:2480`/`2424`/`8582` (admin/debug) |
| **nats** | Canal d'ingestion JetStream (flux temps réel) | `4222`, monitoring `127.0.0.1:8222` |
| **seaweedfs** | Stockage objet des maquettes IFC validées | `127.0.0.1:9333` (admin/debug) |

`bim_api` et `timeseries_api` n'ont **aucun port publié sur l'hôte** : ils ne sont
atteignables que via le réseau Docker interne, donc uniquement via les routes
`/api/bim/*` de l'API Gateway (`bim_proxy.py`), qui applique le même RBAC Keycloak que le
reste de l'API.

### Fonctionnalités

- **Connexion & RBAC** — authentification OIDC via Keycloak, accès contrôlé par rôle, sur
  les deux frontends.
- **Workflow du projet** (BOS Configurateur) — cartouches et étapes métier, servies depuis
  la BDD applicative.
- **Import & validation IFC** (BOS Configurateur) — dépôt d'un ou plusieurs fichiers `.ifc`
  (une discipline à la fois ou en lot), validation contre la charte BIM, ingestion du graphe
  spatial dans ArcadeDB, archivage du fichier source dans SeaweedFS.
- **Visualisation du graphe** (BOS Configurateur) — exploration interactive (Cytoscape) du
  bâtiment, des étages, pièces et équipements ingérés.
- **Visualisation 3D** (BOS Configurateur) — rendu du jumeau numérique
  (ThatOpenEngine/three.js), fusion visuelle de plusieurs disciplines (Archi, Élec, CVC...)
  dans une même scène.
- **Consultation du bâtiment** (BOS Exécution) — jumeau numérique et cockpits de pilotage
  pour les personas propriétaires/exploitants (en construction — `frontend-client/` est
  aujourd'hui un squelette login + test RBAC).

---

## 🔐 Modèle de sécurité (RBAC)

L'accès à l'application et aux endpoints API est contrôlé par les rôles définis dans
Keycloak (`bos-realm`) :

| Rôle Keycloak | Accès BOS Configurateur | Accès BOS Exécution | Accès API config/BIM | Accès API exécution |
| :--- | :--- | :--- | :--- | :--- |
| **Administrateur** | ✅ | ✅ | ✅ HTTP 200 OK | ✅ HTTP 200 OK |
| **Configurateur** | ✅ | ✅ | ✅ HTTP 200 OK | ✅ HTTP 200 OK |
| **Client** | ❌ Refusé (écran 403) | ✅ | ❌ HTTP 403 Forbidden | ✅ HTTP 200 OK |

Deux clients Keycloak distincts, un par frontend : `web-app-frontend` (redirect URIs
`:5173/*`) et `web-app-client` (redirect URIs `:5174/*`).

Principales routes exposées par l'API Gateway :

- `GET /api/config-access`, `GET /api/v1/phases` — accès BOS Configurateur (RBAC : Admin/Configurateur)
- `GET /api/execution-access` — accès BOS Exécution (RBAC : Admin/Configurateur/Client)
- `POST /api/bim/validate`, `POST /api/bim/ingest-batch` — validation et ingestion IFC
- `GET /api/bim/graph/buildings`, `/floors`, `/equipment`, `/neighbors`, `/room-equipment`, `/sources` — lecture du graphe spatial
- `GET /api/bim/models/download` — téléchargement d'une maquette source (pour le rendu 3D)
- `GET /api/bim/timeseries/readings` — relevés temps réel

---

## ⚡ Lancement rapide

### Prérequis
* [Docker Desktop](https://www.docker.com/products/docker-desktop/) installé et démarré
* [Git](https://git-scm.com/)

### Procédure de déploiement

```bash
git clone <URL_DU_DEPOT_GIT> bos-light-mvp
cd bos-light-mvp

# 1) Configurer les secrets (aucune valeur par défaut n'est fournie pour les
#    mots de passe : le démarrage échoue explicitement sans .env rempli)
cp .env.example .env
# éditer .env

# 2) Démarrer l'ensemble de la pile (11 services)
docker compose -f docker-compose.fusion.yml up -d --build

# 3) Vérifier l'état des conteneurs
docker compose -f docker-compose.fusion.yml ps
```

Accès dans le navigateur :

| App | URL | Conteneur |
| :--- | :--- | :--- |
| **BOS Configurateur** | http://localhost:5173 | `bos_frontend` |
| **BOS Exécution** | http://localhost:5174 | `bos_frontend_client` |
| API Gateway (Swagger) | http://localhost:8000/docs | `bos_api_gateway` |
| Console d'administration Keycloak | http://localhost:8080 | `bos_keycloak` |

Pour tout arrêter (les données restent dans les volumes nommés) :
```bash
docker compose -f docker-compose.fusion.yml down
```

> **`docker-compose.yml`** (sans le suffixe `.fusion`) reste disponible séparément pour du
> développement isolé de la partie connexion/RBAC des deux frontends, sans les services BIM
> — utile pour travailler sur `frontend`/`frontend-client`/API Gateway sans démarrer
> ArcadeDB/NATS/SeaweedFS.

Voir [`ROLLBACK.md`](./ROLLBACK.md) pour la procédure de sauvegarde des volumes et de
retour en arrière sur une version antérieure des images.

---

## 🌳 Arborescence du projet

```
bos-light-mvp
├─ .github/workflows/build-and-push.yml   # Publication des images vers GHCR
├─ ROLLBACK.md                            # Procédure de sauvegarde / retour en arrière
├─ scripts/backup-volumes.ps1             # Sauvegarde des volumes Postgres/ArcadeDB
├─ keycloak/bos-realm-export.json         # Realm importé automatiquement au démarrage
│                                         # (rôles Admin/Configurateur/Client, 2 clients
│                                         # OIDC : web-app-frontend et web-app-client)
├─ docker-compose.fusion.yml              # Pile complète (11 services) — usage principal
├─ docker-compose.yml                     # Pile BOS Light seule — dev isolé sans BIM
├─ .env.example
├─ api-gateway/                           # Backend PARTAGÉ par les deux frontends
│  ├─ main.py            # Routes RBAC / workflow / /api/execution-access
│  ├─ security.py        # Dépendances RBAC (verify_allowed_roles, verify_execution_allowed_roles)
│  ├─ bim_proxy.py       # Proxy RBAC vers bim_api / timeseries_api
│  ├─ database.py, models.py
│  └─ Dockerfile
├─ bim_validator/                         # Backend BIM, PARTAGÉ (utilisé par BOS Configurateur)
│  ├─ api_server.py            # Entrée bim_api (validation, ingestion, graphe)
│  ├─ timeseries_api.py        # Entrée timeseries_api
│  ├─ main.py                  # CLI de validation one-shot
│  ├─ domain/                  # Entités + ports (interfaces)
│  ├─ application/             # Services métier (validation, ingestion graphe)
│  ├─ adapters/, infrastructure/adapters/   # Implémentations (ifcopenshell, ArcadeDB, SeaweedFS, NATS)
│  ├─ scripts/                 # Utilitaires ponctuels (préparation de maquettes de test)
│  ├─ tests/
│  └─ maquettes/                # Fichiers IFC de test
├─ frontend/                              # BOS Configurateur (:5173)
│  ├─ src/
│  │  ├─ App.jsx, main.jsx, ProtectedRoute.jsx
│  │  ├─ services/keycloak.js, apiClient.js
│  │  └─ features/bim/
│  │     ├─ BimExplorer.tsx                     # Onglets Graphe / Maquette 3D
│  │     ├─ components/BimGraphViewer.tsx        # Visualisation du graphe (Cytoscape)
│  │     ├─ components/BimModelViewer.tsx        # Visualisation 3D (ThatOpenEngine)
│  │     ├─ components/NodeInspector.tsx
│  │     └─ services/arcadeApi.ts
│  ├─ public/wasm/web-ifc.wasm    # Binaire WASM auto-hébergé (parsing IFC côté navigateur)
│  └─ Dockerfile
└─ frontend-client/                       # BOS Exécution (:5174)
   ├─ src/
   │  ├─ App.jsx, main.jsx, ProtectedRoute.jsx
   │  └─ services/keycloak.js, apiClient.js
   └─ Dockerfile
```
