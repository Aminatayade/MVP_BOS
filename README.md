# 🚀 BOS Light MVP - Application de Configuration avec Authentification & RBAC

Ce projet constitue le MVP (Minimum Viable Product) de l'application **BOS Light**. Il s'appuie sur une architecture microservices entièrement conteneurisée sous **Docker Compose**, intégrant la gestion de l'identité (IAM), une API Gateway sécurisée et une interface web interactive.

---

## 📐 Architecture Globale

L'application repose sur 5 briques applicatives interconnectées :

1. **Frontend Web** (React / Vite) — Port `5173`
2. **API Gateway** (FastAPI) — Port `8000`
3. **IAM / Identity** (Keycloak 24) — Port `8080`
4. **BDD Identity** (PostgreSQL Keycloak) — Port `5432`
5. **BDD Métier** (PostgreSQL Application) — Port `5433`

---

## 🔐 Modèle de Sécurité (RBAC)

L'accès à l'application et aux endpoints API est contrôlé par les rôles définis dans Keycloak (`bos-realm`) :

| Rôle Keycloak | Accès Web Frontend | Accès API Gateway (`/api/v1/phases`) |
| :--- | :--- | :--- |
| **Administrateur** | ✅ Autorisé | ✅ HTTP 200 OK |
| **Configurateur** | ✅ Autorisé | ✅ HTTP 200 OK |
| **Client** | ❌ Refusé (Écran 403) | ❌ HTTP 403 Forbidden |

---

## ⚡ Lancement Rapide (Quick Start)

### Prérequis
* [Docker Desktop](https://www.docker.com/products/docker-desktop/) installé et démarré.
* [Git](https://git-scm.com/).

### Procédure de déploiement

1. **Cloner le dépôt :**
   ```bash
   git clone <URL_DU_DEPOT_GIT>
   cd bos-light-mvp


   Démarrer l'ensemble de la stack Docker :
Bash

docker compose up -d --build

Vérifier l'état des conteneurs :
Bash

docker compose ps

Arrêter les services :
Bash

docker compose down

Réinitialiser complètement la base de données et les volumes :
Bash

docker compose down -v

Consulter les logs de l'API Gateway :
Bash

docker compose logs -f api-gateway


1. Démarrer toute la stack Docker

C:\stageBIM\bos-light-mvp
PowerShell

docker compose up -d --build


2. Vérifier que tout tourne correctement


docker compose ps

3. Accéder aux services dans navigateur

    Interface Web (React) : http://localhost:5173

    API Gateway (Documentation Swagger) : http://localhost:8000/docs

    Console d'administration Keycloak : http://localhost:8080

Pour tout arrêter :

PowerShell

docker compose down

Project tree: 
```
bos-light-mvp
├─ api-gateway
│  ├─ database.py
│  ├─ Dockerfile
│  ├─ main.py
│  ├─ models.py
│  └─ requirements.txt
├─ docker-compose.yml
├─ frontend
│  ├─ Dockerfile
│  ├─ eslint.config.js
│  ├─ index.html
│  ├─ package-lock.json
│  ├─ package.json
│  ├─ public
│  │  ├─ favicon.svg
│  │  └─ icons.svg
│  ├─ README.md
│  ├─ src
│  │  ├─ App.css
│  │  ├─ App.jsx
│  │  ├─ assets
│  │  │  ├─ hero.png
│  │  │  ├─ react.svg
│  │  │  └─ vite.svg
│  │  ├─ components
│  │  │  └─ ProtectedRoute.jsx
│  │  ├─ index.css
│  │  ├─ main.jsx
│  │  ├─ pages
│  │  │  ├─ Dashboard.jsx
│  │  │  └─ Home.jsx
│  │  ├─ ProtectedRoute.jsx
│  │  └─ services
│  │     ├─ apiClient.js
│  │     └─ keycloak.js
│  └─ vite.config.js
├─ package-lock.json
├─ package.json
└─ README.md

```