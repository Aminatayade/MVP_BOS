# 🚀 web-app-configuration — BOS Light

**BOS Light MVP** est composé de trois applications distinctes : **web-app-configuration**
(ce dépôt), **web-app-execution**, et **userapp**. Ce nom de dossier (et non
« bos-light-mvp », ambigu — c'est le nom du produit global, pas de cette application
précise) identifie sans confusion laquelle des trois est ici.

Ce dépôt est le **produit fusionné** de l'application de configuration avec authentification
& RBAC (le MVP d'origine) **et** le moteur BIM — validation/ingestion de maquettes IFC,
visualisation du graphe spatial, et visualisation 3D du jumeau numérique (ThatOpenEngine).
Un seul `git clone` + `docker compose up` suffit pour faire tourner l'ensemble : plus besoin
d'un second dossier ni d'un transfert de fichiers séparé.

---

## 📐 Architecture globale

L'application repose sur **10 conteneurs** interconnectés :

| Service | Rôle | Port hôte |
| :--- | :--- | :--- |
| **frontend** (React / Vite) | Interface web — configurateur, explorateur BIM (graphe + 3D) | `5173` |
| **api-gateway** (FastAPI) | Point d'entrée public unique côté backend, RBAC Keycloak, proxy vers les services BIM | `8000` |
| **keycloak** (24) | IAM / identité, realm `bos-realm` | `8080` |
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

- **Connexion & RBAC** — authentification OIDC via Keycloak, accès contrôlé par rôle.
- **Workflow du projet** — cartouches et étapes métier, servies depuis la BDD applicative.
- **Import & validation IFC** — dépôt d'un ou plusieurs fichiers `.ifc` (une discipline à la
  fois ou en lot), validation contre la charte BIM, ingestion du graphe spatial dans
  ArcadeDB, archivage du fichier source dans SeaweedFS.
- **Visualisation du graphe** — exploration interactive (Cytoscape) du bâtiment, des
  étages, pièces et équipements ingérés.
- **Visualisation 3D** — rendu du jumeau numérique (ThatOpenEngine/three.js), fusion
  visuelle de plusieurs disciplines (Archi, Élec, CVC...) dans une même scène.

---

## 🔐 Modèle de sécurité (RBAC)

L'accès à l'application et aux endpoints API est contrôlé par les rôles définis dans
Keycloak (`bos-realm`) :

| Rôle Keycloak | Accès web frontend | Accès API Gateway |
| :--- | :--- | :--- |
| **Administrateur** | ✅ Autorisé | ✅ HTTP 200 OK |
| **Configurateur** | ✅ Autorisé | ✅ HTTP 200 OK |
| **Client** | ❌ Refusé (écran 403) | ❌ HTTP 403 Forbidden |

Principales routes exposées par l'API Gateway :

- `GET /api/config-access`, `GET /api/v1/phases` — accès applicatif et workflow (RBAC)
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
git clone <URL_DU_DEPOT_GIT> web-app-configuration
cd web-app-configuration

# 1) Configurer les secrets (aucune valeur par défaut n'est fournie pour les
#    mots de passe : le démarrage échoue explicitement sans .env rempli)
cp .env.example .env
# éditer .env

# 2) Démarrer l'ensemble de la pile (10 services)
docker compose -f docker-compose.fusion.yml up -d --build

# 3) Vérifier l'état des conteneurs
docker compose -f docker-compose.fusion.yml ps
```

Accès dans le navigateur :
- Interface web : http://localhost:5173
- API Gateway (Swagger) : http://localhost:8000/docs
- Console d'administration Keycloak : http://localhost:8080

Pour tout arrêter (les données restent dans les volumes nommés) :
```bash
docker compose -f docker-compose.fusion.yml down
```

> **`docker-compose.yml`** (sans le suffixe `.fusion`) reste disponible séparément pour du
> développement isolé de la partie connexion/RBAC seule, sans les services BIM — utile pour
> travailler sur le frontend/API Gateway sans démarrer ArcadeDB/NATS/SeaweedFS.

Voir [`ROLLBACK.md`](./ROLLBACK.md) pour la procédure de sauvegarde des volumes et de
retour en arrière sur une version antérieure des images.

---

## 🌳 Arborescence du projet

```
web-app-configuration
├─ .github/workflows/build-and-push.yml   # Publication des images vers GHCR
├─ ROLLBACK.md                            # Procédure de sauvegarde / retour en arrière
├─ scripts/backup-volumes.ps1             # Sauvegarde des volumes Postgres/ArcadeDB
├─ keycloak/bos-realm-export.json         # Realm importé automatiquement au démarrage
├─ docker-compose.fusion.yml              # Pile complète (10 services) — usage principal
├─ docker-compose.yml                     # Pile BOS Light seule — dev isolé sans BIM
├─ .env.example
├─ api-gateway/
│  ├─ main.py            # Routes RBAC / workflow
│  ├─ bim_proxy.py       # Proxy RBAC vers bim_api / timeseries_api
│  ├─ database.py, models.py, security.py
│  └─ Dockerfile
├─ bim_validator/
│  ├─ api_server.py            # Entrée bim_api (validation, ingestion, graphe)
│  ├─ timeseries_api.py        # Entrée timeseries_api
│  ├─ main.py                  # CLI de validation one-shot
│  ├─ domain/                  # Entités + ports (interfaces)
│  ├─ application/             # Services métier (validation, ingestion graphe)
│  ├─ adapters/, infrastructure/adapters/   # Implémentations (ifcopenshell, ArcadeDB, SeaweedFS, NATS)
│  ├─ scripts/                 # Utilitaires ponctuels (préparation de maquettes de test)
│  ├─ tests/
│  └─ maquettes/                # Fichiers IFC de test
└─ frontend/
   ├─ src/
   │  ├─ App.jsx, main.jsx, ProtectedRoute.jsx
   │  ├─ services/keycloak.js, apiClient.js
   │  └─ features/bim/
   │     ├─ BimExplorer.tsx                     # Onglets Graphe / Maquette 3D
   │     ├─ components/BimGraphViewer.tsx        # Visualisation du graphe (Cytoscape)
   │     ├─ components/BimModelViewer.tsx        # Visualisation 3D (ThatOpenEngine)
   │     ├─ components/NodeInspector.tsx
   │     └─ services/arcadeApi.ts
   ├─ public/wasm/web-ifc.wasm    # Binaire WASM auto-hébergé (parsing IFC côté navigateur)
   └─ Dockerfile
```
