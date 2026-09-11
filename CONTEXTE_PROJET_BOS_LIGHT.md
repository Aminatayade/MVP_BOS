# Contexte du projet BOS Light MVP — document de reprise

Document rédigé pour donner du contexte technique complet à toute IA (ou développeur) qui
reprend ce dépôt sans avoir suivi les sessions précédentes. Il couvre uniquement ce qui a été
concrètement implémenté dans le dossier `bos-light-mvp` : l'architecture, les grandes étapes,
les bugs rencontrés (avec leur diagnostic et leur cause réelle, pour éviter de refaire les
mêmes erreurs), et l'état fonctionnel actuel. Il ne couvre pas les documents PDF annexes
(état de l'art hébergeurs, architecture générale) ni les échanges de simples questions/réponses
— seulement le code et l'infrastructure réellement livrés.

Dernière mise à jour de ce document : 2026-08-28. État du dépôt à cette date : branche
`bos-light-mvp`, commit `486cbf9` (fusion de `417035e` et `d9d8c8d`).

---

## 1. Qu'est-ce que BOS Light MVP

BOS Light MVP est composé de **trois applications distinctes** :
- **BOS Configurateur** — l'app où on importe/valide les maquettes IFC, on visualise le
  graphe spatial et le jumeau numérique 3D. Rôles autorisés : Administrateur, Configurateur.
- **BOS Exécution** — l'app de consultation pour les exploitants/propriétaires du bâtiment
  (jumeau numérique en lecture seule, cockpits de pilotage). Rôle supplémentaire autorisé :
  Client. Contrainte stricte : **BOS Exécution ne peut jamais ingérer/modifier**, seulement
  lire ce que le Configurateur a déjà ingéré.
- **userapp** — pas encore démarrée, hors périmètre de ce dépôt à ce jour.

Ce dépôt (`bos-light-mvp`) héberge les deux premières applications (deux frontends React/Vite
séparés) et **un seul backend partagé** (Keycloak, PostgreSQL ×2, API Gateway, ArcadeDB, NATS,
SeaweedFS, Grafana). Un seul `git clone` + `docker compose -f docker-compose.fusion.yml up
--build` suffit pour tout faire tourner.

---

## 2. Architecture technique actuelle

### 2.1 Services (13 définis, 12 démarrent par défaut)

| Service | Rôle | Port hôte |
| :--- | :--- | :--- |
| `frontend` | BOS Configurateur (React/Vite) | `5173` |
| `frontend-client` | BOS Exécution (React/Vite) | `5174` |
| `api-gateway` | FastAPI, point d'entrée public unique, RBAC Keycloak, proxy vers bim_api/timeseries_api | `8000` |
| `keycloak` (v24) | IAM, realm `bos-realm`, importé automatiquement au démarrage depuis `keycloak/bos-realm-export.json` | `8080` |
| `postgres` | BDD Identity (Keycloak) | `5432` |
| `postgres-app` | BDD métier (phases, cartouches du workflow Configurateur) | `5433` |
| `bim_api` | FastAPI (`bim_validator/api_server.py`) — validation IFC, ingestion graphe, téléchargement maquettes | interne uniquement, via gateway |
| `timeseries_api` | FastAPI (`bim_validator/timeseries_api.py`) — lecture des relevés temps réel | interne uniquement, via gateway |
| `arcadedb` | Graphe BIM (bâtiments/étages/pièces/équipements) **et** moteur time-series (même base) | `127.0.0.1:2480/2424/8582` (debug) |
| `nats` | JetStream, canal d'ingestion des flux temps réel simulés/réels | `4222`, monitoring `8222` |
| `seaweedfs` | Stockage objet des fichiers IFC validés | `127.0.0.1:9333` (debug) |
| `grafana` (grafana-oss + plugin Infinity) | Cockpits de graphiques, embarqués en iframe côté frontend (mode kiosk : le client ne voit jamais "Grafana", juste des graphiques) | `3000` |
| `bim_validator` (profil `tools`) | CLI one-shot de validation IFC, ne tourne pas en continu (`docker compose run --rm bim_validator ...`) | — |

`bim_api` et `timeseries_api` n'ont **aucun port publié sur l'hôte** : uniquement joignables
via le réseau Docker interne, donc uniquement via les routes `/api/bim/*` de l'API Gateway.

Deux fichiers compose : `docker-compose.fusion.yml` (pile complète, usage principal) et
`docker-compose.yml` (juste connexion/RBAC des deux frontends, sans les services BIM — pour
travailler sur ces parties sans démarrer ArcadeDB/NATS/SeaweedFS/Grafana).

### 2.2 RBAC — rôles Keycloak et accès

Realm `bos-realm`, deux clients OIDC distincts (un par frontend) : `web-app-frontend`
(redirect `:5173/*`) et `web-app-client` (redirect `:5174/*`).

| Rôle | BOS Configurateur | BOS Exécution | API config/BIM (écriture + lecture complète) | API exécution (lecture 3D seule) |
| :--- | :--- | :--- | :--- | :--- |
| Administrateur ||
| Configurateur ||
| Client |  403 |  |  403 | |

Le RBAC est appliqué **route par route** dans `api-gateway/bim_proxy.py` (pas une seule
dépendance globale sur le routeur) : chaque route déclare explicitement
`Depends(verify_allowed_roles)` (Admin/Configurateur uniquement) ou
`Depends(verify_execution_allowed_roles)` (Admin/Configurateur/Client). Seules deux routes
sont ouvertes au rôle Client : `GET /graph/sources` et `GET /models/download` (les deux
routes nécessaires pour que BOS Exécution affiche la 3D sans jamais pouvoir ingérer). Toute
route d'écriture (`/validate`, `/ingest-batch`) reste strictement Admin/Configurateur — la
contrainte "Exécution ne peut jamais ingérer" est donc appliquée **au niveau du backend**, pas
seulement par absence de bouton dans l'UI.

### 2.3 Stockage et flux de données

- **Fichier IFC source** → validé (règles de charte BIM : unités, `Pset_SpaceCommon` requis
  sur `IfcSpace`, etc.) → archivé tel quel dans **SeaweedFS** → son URL de stockage
  (`storage_url`) est écrite comme propriété sur chaque nœud du graphe qu'il a produit.
- **Graphe spatial** (Building/Floor/Room/Equipment + relations) → **ArcadeDB**. Seule la
  discipline "backbone" (généralement Archi) crée les nœuds Building/Floor/Room ; les autres
  disciplines (Élec, CVC...) n'ajoutent que des nœuds Equipment, rattachés par correspondance
  de nom aux Room/Floor déjà créés.
- **Données temps réel** (température, CO2, occupation...) → publiées sur **NATS**
  (JetStream) → consommées par `timeseries_api`/`bim_api` → écrites dans **ArcadeDB** (moteur
  time-series intégré, même base que le graphe). Deux sources : un flux simulé
  (`publish_demo_stream.py`, `simulate_live_stream.py`) et, depuis l'ajout du catalogue de
  simulation (§4, dernière étape), une génération automatique d'historique à l'ingestion pour
  chaque équipement nouvellement créé (`equipment_simulation_service.py` +
  `config/equipment_simulation_catalog.json`), pour qu'un équipement ait tout de suite des
  valeurs plausibles sans dépendre d'un capteur physique réel connecté.
- **Modèle 3D** → chaque discipline chargée séparément dans le navigateur (ThatOpenEngine /
  web-ifc / three.js) depuis son `storage_url` (téléchargé via `GET /models/download`), fusion
  visuelle faite côté client (plusieurs modèles dans la même scène three.js), pas de fusion
  serveur.

---

## 3. Grandes étapes chronologiques (avec le "pourquoi")

Historique complet du dépôt (29 commits avant la fusion du 2026-08-28, premier commit
`3a72b1f` du 2026-07-24 — c'est un import initial déjà à l'état "MVP finalisé RBAC", l'
historique antérieur n'est pas dans ce dépôt).

1. **`3a72b1f` (24/07)** — Import initial : MVP connexion/RBAC (auth Keycloak, rôles,
   frontend React + API Gateway FastAPI).
2. **`127e549` → `9baa739` (18-19/08)** — **Rapatriement du backend BIM.** Le backend
   `bim_validator` (validation/ingestion IFC) n'était versionné nulle part avant (transmis par
   zip, jamais un vrai dépôt git) : rapatrié dans `./bim_validator`. Import auto du realm
   Keycloak au démarrage (au lieu d'une config manuelle). Auto-création de la base ArcadeDB
   `bim` au démarrage + volume persistant (voir bug détaillé en §4).
3. **`43562a1` → `cda49dd` (19/08)** — **Saga de bugs sur le graphe Cytoscape** (clics ratés,
   panneau qui grossit à l'infini) — voir diagnostic détaillé en §4, exemple concret de
   plusieurs tentatives avant la bonne cause racine.
4. **`8d7d949`, `afad541` (19/08)** — Refonte visuelle (le CSS Tailwind n'était en fait jamais
   appliqué, dépendance jamais installée) + glisser-déposer pour l'import IFC.
5. **`1025a40` (21/08)** — **Ajout du visualiseur 3D IFC** (ThatOpenEngine/three.js), routes
   `/graph/sources` et `/models/download`, WASM auto-hébergé. En parallèle : premier
   verrouillage des versions d'images Docker (digests figés pour ArcadeDB/NATS/SeaweedFS,
   tag `APP_VERSION` pour les images maison), script de sauvegarde des volumes
   (`scripts/backup-volumes.ps1`), doc `ROLLBACK.md`, publication automatique vers GHCR.
6. **`5c04f31`, `c794ac6` (24/08)** — Audit du dépôt : suppression des fichiers morts/doublons
   (chaque suppression vérifiée par recherche réelle des imports avant coup), mise à jour du
   README.
7. **`562833c` → `0fe5c0b` (24/08)** — **Fusion des deux applications dans un seul dépôt.**
   Renommage `bos-light-mvp` → `web-app-configuration` (le nom `bos-light-mvp` désignait le
   produit global, source de confusion), puis fusion du frontend Exécution
   (`frontend-client/`) dans ce même dépôt unifié, re-renommé `bos-light-mvp` (nom du projet
   global cette fois, `COMPOSE_PROJECT_NAME`). Effet de bord positif : élimine une collision
   de noms de conteneurs Docker qui causait des incidents de conteneurs partagés entre les
   deux apps avant la fusion.
8. **`5aeddbf`, `d50047e` (24/08)** — Réorganisation en sous-dossiers
   `web-app-configuration/` et `web-app-execution/`, READMEs locaux par app.
9. **`b1873e7`, `4fff9a1` (24/08)** — **Cockpit Grafana embarqué** (température/CO2/occupation
   par pièce-étage), nouvelles routes `/graph/locations` et `/graph/location-readings` (agréger
   par pièce/étage plutôt que par équipement individuel, plus fiable car indépendant du
   nommage propre à chaque maquette). Fix d'un bug NATS déjà corrigé sur une autre branche mais
   jamais reçu ici (le consumer n'attrapait que `nats.errors.TimeoutError`, pas
   `asyncio.TimeoutError`, et mourait silencieusement à la première fenêtre sans message).
   Fusionné avec la branche de fusion backend (résolution manuelle de conflits sur
   `api_server.py` et `.env.example`, rien perdu des deux côtés).
10. **`eccc256` (24/08)** — **Point 6 roadmap, volet Exécution** : RBAC ouvert route par route
    (voir §2.2), 3D portée dans `web-app-execution/frontend-client/` (copie du composant
    Configurateur, `BimViewer.tsx` allégé sans aucune UI d'ingestion).
11. **`ff5a616`, `eac18ee` (26/08)** — Ajout de la config Grafana dans l'interface
    Configurateur, après l'onglet 3D (troisième onglet de l'explorateur BIM :
    Graphe / Maquette 3D / Supervision).
12. **`417035e` (28/08, cette session)** — **Fiabilisation de la popup 3D** (clic sur un
    élément → fiche IFC) : voir §4 pour le détail des 3 bugs réels corrigés (coordonnées de
    raycast, worker fragments non auto-hébergé, angle polaire inversé pour la vue de dessus),
    plus l'ajout de points d'audit `console.log/warn/error` groupés pour diagnostiquer tout
    nouvel échec directement en F12 sans deviner. Fix aussi du filtre `zone` manquant sur
    `/graph/buildings` et `/graph/floors`, et d'un cache client périmé qui empêchait de voir
    une nouvelle maquette après ré-ingestion.
13. **`d9d8c8d` (28/08, en parallèle par Aminata)** — **Popup capteurs sur le graphe** +
    intégration Grafana approfondie : `EquipmentTelemetryPopup.tsx` (popup flottante ancrée
    sur le nœud Equipment cliqué dans le graphe Cytoscape — PAS dans la vue 3D — affichant la
    valeur courante de ses métriques via `getEquipmentSummary(globalId)`), génération
    automatique d'historique simulé par catalogue d'équipement à l'ingestion
    (`equipment_simulation_service.py`, `config/equipment_simulation_catalog.json`), sync
    automatique des variables de dashboard Grafana (`sync_grafana_dashboard_variables.py`,
    `grafana_dashboard_sync_adapter.py`) plutôt qu'une config manuelle.
14. **`486cbf9` (28/08)** — Fusion des deux commits parallèles (13) et (14), aucun conflit
    réel (fichiers différents ou sections différentes du même fichier), vérifié par
    `tsc -b --noEmit` avant push.

**Point important issu de (12) et (13) en parallèle** : il existe maintenant DEUX popups
différentes selon où on clique — une popup capteurs/temps réel sur le **graphe** (nœud
Equipment, Aminata), et une popup fiche IFC statique sur la **3D** (élément cliqué, cette
session) qui n'affiche PAS encore de données temps réel. Voir §6 et §7 pour la suite logique
naturelle (relier les deux, cf. discussion dans ce chat sur "quelles fonctionnalités mettre
dans une roue au clic 3D").

---

## 4. Bugs rencontrés — diagnostic et cause réelle

Section volontairement détaillée : chaque entrée montre le symptôme observé, les fausses
pistes explorées le cas échéant, et la cause racine réellement identifiée (par lecture de
code/source, jamais par supposition non vérifiée), pour ne pas reproduire la même erreur de
diagnostic.

### 4.1 ArcadeDB : "Database bim is not available" (502 côté frontend)
**Symptôme** : fonctionnait chez l'auteur d'origine, cassé sur une machine neuve.
**Cause réelle** : la base `bim` n'était jamais créée explicitement dans le code — l'auteur
l'avait créée une fois à la main via ArcadeDB Studio. Sur une machine neuve, l'ingestion
(écriture) réussissait silencieusement sans que la base existe vraiment, et la lecture
échouait ensuite avec 502. **Fix** : `bim_api`/`timeseries_api` créent la base au démarrage
(avec ré-essais, ArcadeDB pouvant ne pas être prêt) + ajout d'un volume persistant absent
jusque-là (sans lui, chaque rebuild de conteneur perdait toutes les maquettes ingérées).

### 4.2 Graphe Cytoscape : clics sur les nœuds qui ratent leur cible (saga en 4 commits)
**Symptôme initial** : le panneau du graphe grossissait indéfiniment et écrasait
l'inspecteur de nœud.
- **Cause #1 identifiée et corrigée (`43562a1`)** : le conteneur flex du graphe (`flex: 1`)
  n'avait pas `min-width/min-height: 0`. Un flex-item vaut par défaut `min-width/min-height:
  auto`, donc refuse de rétrécir sous la taille intrinsèque de son contenu — le canvas
  Cytoscape (`width/height: 100%`) se mesurait sur son propre contenu et grossissait à chaque
  re-rendu.
- **Symptôme résiduel après ce premier fix** : les clics sur les nœuds ne dépliaient plus
  rien.
- **Fausse piste explorée** : `pixelRatio: 1` sur Cytoscape — n'affecte que la netteté du
  rendu, pas le hit-testing des clics. Écartée après vérification.
- **Cause #2 identifiée (`78d39f5`)**, par traces de diagnostic temporaires confirmant que le
  clic DOM atteignait bien le canvas mais que Cytoscape le classait en "fond" : Cytoscape ne
  se redimensionne jamais tout seul quand son conteneur change de taille après sa création
  (ex. une police Google Fonts qui finit de charger après le premier rendu et décale
  légèrement la mise en page). Son repère de coordonnées interne restait basé sur l'ancienne
  taille. **Fix** : `ResizeObserver` sur le conteneur, appelle `cy.resize()` à chaque
  changement réel de taille.
- **Tentative intermédiaire incorrecte (`fbd50e5`)** : plafonner le zoom après `fit()` à 2.5
  (hypothèse : avec peu d'éléments, `cy.fit()` visait un zoom supérieur à `maxZoom` et se
  faisait plafonner, désynchronisant le cadrage affiché de la zone cliquable). **Corrigée
  ensuite (`ed052b3`, revert)** : l'utilisateur a confirmé que ça ne résolvait rien et ajoutait
  un flash visuel disgracieux à l'ouverture — symptôme corrélé (zoom au maximum) mais pas cause
  réelle.
- **Fix final réel (`cda49dd`)** : le `ResizeObserver` (cause #2) était la bonne piste mais
  incomplète — Cytoscape mettait en cache la taille/position du conteneur à la création et ne
  la relisait jamais si un parent flexbox le redimensionnait ensuite (ex. apparition du
  panneau NodeInspector) sans événement `resize` de la fenêtre. Le `ResizeObserver` couvre
  aussi ce cas. Pin de `cytoscape` à la version exacte de référence qui fonctionnait (3.34.0).
**Leçon** : plusieurs symptômes corrélés (zoom, taille de police, redimensionnement de panneau)
pointaient vers la même cause racine (absence de resize explicite de Cytoscape) — la
diagnostiquer a demandé des traces temporaires réelles, pas de la déduction a priori.

### 4.3 Collision de noms de conteneurs entre Configurateur et Exécution
**Symptôme** : incidents de conteneurs partagés/écrasés entre les deux apps avant leur fusion
en un seul dépôt.
**Cause réelle** : `COMPOSE_PROJECT_NAME` retombait par défaut sur le même nom pour les deux
projets Docker Compose distincts (le nom de dossier). **Fix** : renommage explicite du nom de
projet Compose lors de la fusion (§3, étape 7) — effet de bord positif du renommage, pas son
but premier.

### 4.4 CSS Tailwind sans effet
**Symptôme** : `NodeInspector` s'affichait sans aucun style.
**Cause réelle** : le composant utilisait des classes Tailwind alors que la dépendance
Tailwind n'était jamais installée dans le projet — les classes ne faisaient donc strictement
rien. **Fix** : refonte avec un vrai design system (CSS simple, palette bleu foncé/bleu
clair/gris/blanc), pas de réinstallation de Tailwind.

### 4.5 Popup 3D (clic sur un élément → fiche IFC) — 3 bugs réels, cette session

**Bug 1 — coordonnées du raycast.** `FragmentsModel.raycast({camera, mouse, dom})` attend le
paramètre `mouse` en **pixels bruts** (`event.clientX/clientY`), pas en coordonnées normalisées
(NDC, -1..1) : la librairie fait elle-même la conversion NDC en interne via
`dom.getBoundingClientRect()`. Vérifié en lisant `screenToCast()` dans le code compilé de
`@thatopen/fragments` (le motif `p.x - rect.left` prouve une attente en pixels). Avant ce fix,
le rayon ne touchait jamais rien.

**Bug 2 — `model[input.function] is not a function` sur `getGuid()`/`getCategory()`/
`getAttributes()`.** Le raycast lui-même fonctionnait (via une route RPC ancienne/stable), mais
ces trois appels échouaient. **Cause réelle** : `OBC.FragmentsManager.getWorker()` va chercher
le worker fragments (le script qui tourne dans un thread séparé et parse le buffer IFC) sur
**unpkg.com** par défaut, au lieu de la version réellement installée en local — mismatch de
version entre le code appelant et le worker distant, cassant l'API "Item" plus récente (le
raycast, plus ancien/stable, passait par une route RPC différente qui restait compatible).
**Fix** : auto-héberger `node_modules/@thatopen/fragments/dist/Worker/worker.mjs` en le
copiant vers `public/fragments-worker.mjs`, et charger `'/fragments-worker.mjs'` au lieu
d'appeler `getWorker()`. Même famille de bug que le WASM `web-ifc` déjà auto-hébergé
précédemment (`1025a40`) pour la même raison.

**Bug 3 — vue "du dessous" au lieu d'une vue de dessus sur le plan 2D d'étage.**
`controls.rotateTo(0, Math.PI, false)` (librairie `camera-controls`) avait l'angle polaire
`phi` inversé. Vérifié directement dans `camera-controls.module.js` (convention standard
`THREE.Spherical` : `phi = 0` = caméra au-dessus de la cible regardant vers le bas ; `phi =
Math.PI` = caméra en dessous regardant vers le haut). **Fix** : `rotateTo(0, 0, false)`.

**Bugs annexes de la même fonctionnalité (plan 2D d'étage / téléportation)** :
- Le plan de coupe (`OBC.Clipper`) créé n'était jamais réellement activé : le code mettait à
  jour `clipper.enabled`/`clipper.visible` (contrôle l'outil interactif générique), pas les
  propriétés du plan lui-même (`clipper.list.get(planeId).enabled/.visible`) — donc le même
  plan (ou aucune coupe) s'affichait quel que soit l'étage sélectionné.
- Hauteur de coupe à `unionBox.max.y + 0.3` (près du plafond, incohérent selon la hauteur sous
  plafond de chaque pièce) → changée à `unionBox.min.y + 1.2` (hauteur d'appui, convention
  architecturale standard : les murs vont du sol au plafond quelle que soit la hauteur de la
  pièce, contrairement au mobilier).
- Téléportation au clic sur le plan : atterrissait exactement au point cliqué, ce qui pouvait
  finir dans un mur si on cliquait près d'un bord de pièce → changé pour atterrir au **centre**
  de la pièce détectée (le clic ne sert qu'à désigner QUELLE pièce, la marche libre ZQSD prend
  le relais ensuite).
- Contrôles FPS : `OBC.FirstPersonMode` de ThatOpenEngine ne fait que resserrer l'orbite
  (distance figée à 1) et accélérer le zoom-molette — elle ne branche AUCUNE touche clavier.
  Le déplacement WASD/ZQSD a dû être construit à la main (boucle `requestAnimationFrame`,
  lecture de `pressedKeysRef`). Une première version utilisait la Pointer Lock API (mouse-look
  façon FPS classique, toujours actif) — **explicitement remplacée** sur demande utilisateur
  par un schéma plus simple : la souris ne dirige la marche QUE pendant qu'on avance (touche
  avant maintenue), sinon elle se comporte normalement. Ne pas réintroduire la Pointer Lock
  sans qu'on le redemande explicitement.

### 4.6 Backend : `/graph/buildings` et `/graph/floors` sans filtre de zone
**Symptôme** : après ingestion d'une nouvelle maquette dans une zone différente, l'app semblait
"garder en cache" l'ancienne maquette.
**Cause réelle, vérifiée en base** : ces deux routes n'avaient tout simplement AUCUN filtre de
zone — elles renvoyaient tous les bâtiments/étages jamais ingérés, toutes zones confondues.
**Fix** : paramètre `zone` optionnel ajouté (rétrocompatible), filtrage Cypher côté ArcadeDB
quand fourni.

### 4.7 Frontend : cache client périmé sur les sources de maquette
**Symptôme** : même après le fix 4.6, une nouvelle ingestion ne se reflétait pas dans l'onglet
3D tant qu'on n'avait pas rechargé la page entière.
**Cause réelle** : `openModelTab()` avait un garde `if (modelSourcesLoaded) return` qui
empêchait tout refetch de `getModelSources(zone)` après la première visite de l'onglet dans la
session, même après une nouvelle ingestion. **Fix** : garde supprimée entièrement, refetch
systématique à chaque visite de l'onglet.

### 4.8 Maquette avec "0 pièces" affichées sur le plan 2D
**Symptôme** : rapporté comme un bug d'affichage.
**Diagnostic** : vérifié directement avec `ifcopenshell` dans le conteneur `bim_api`
(`docker exec bos_bim_api python3 ...`) que le fichier IFC concerné contenait réellement
**0 entité `IfcSpace`** (contre 547 dans un autre fichier de test) — c'est-à-dire que
l'exportateur IFC d'origine (Revit) n'avait jamais défini de pièces dans cette maquette
précise. **Conclusion** : caractéristique réelle du fichier source, pas un bug du code. Un fix
d'affichage a quand même été fait côté état vide (message explicite + nettoyage de l'état 3D
résiduel), pour que ce cas ne laisse pas un plan visuellement incohérent affiché.

---

## 5. Conventions et pièges techniques établis (à connaître avant de retoucher le code)

- **Toujours auto-héberger les assets ThatOpenEngine/web-ifc** (WASM `web-ifc.wasm` ET le
  worker `fragments-worker.mjs`) plutôt que de laisser les résolutions par défaut vers un CDN
  (unpkg) — cause vérifiée de deux bugs réels de mismatch de version (§4.5, bug 2, et le fix
  WASM antérieur `1025a40`).
- **`FragmentsModel.raycast()` attend des pixels bruts**, pas des coordonnées NDC
  normalisées — la conversion se fait en interne.
- **`camera-controls` : `phi` (angle polaire) suit la convention `THREE.Spherical` standard**
  (0 = au-dessus regardant vers le bas). Vérifier dans le code source compilé avant de
  deviner, pas l'inverse.
- **`OBC.Clipper` : activer le plan lui-même** (`clipper.list.get(id).enabled/.visible`), pas
  le composant `clipper.enabled/.visible` (contrôle seulement l'outil interactif générique).
- **Coupe de plan d'étage à hauteur d'appui (~1.2 m au-dessus du sol)**, pas près du plafond —
  seuls les murs traversent fiablement toute la hauteur de la pièce.
- **`OBC.FirstPersonMode` ne branche aucune touche clavier** — tout le WASD/ZQSD est fait à la
  main dans `BimModelViewer.tsx`.
- **`event.code` (position physique de la touche) plutôt que `event.key`** : rend WASD et
  ZQSD utilisables simultanément sans sélecteur de disposition clavier, gratuitement.
- **Chrome masque `console.debug` par défaut** (filtre "Verbose" désactivé par défaut) —
  toujours utiliser `console.log`/`warn`/`error` pour un diagnostic destiné à l'utilisateur
  final en F12, jamais `console.debug`.
- **Le `GlobalId` IFC est généré côté logiciel auteur** (Revit...) à la création de l'élément,
  pas par ThatOpenEngine/web-ifc qui ne fait que le lire. Il survit à toute modification de
  l'élément, mais est régénéré si l'élément est supprimé puis recréé (au lieu d'édité) — point
  d'attention pour tout lien futur GUID ↔ capteur/ticket.
- **Toujours vérifier zone-scoping sur les nouvelles routes de lecture graphe** — au moins deux
  routes (`/graph/buildings`, `/graph/floors`) ont eu ce bug d'oubli de filtre zone ; c'est un
  piège récurrent, à vérifier systématiquement sur toute nouvelle route de lecture.
- **`bim_api`/`timeseries_api` créent leur base ArcadeDB au démarrage** (avec ré-essais) — ne
  pas supposer qu'une base ArcadeDB existe déjà sans vérifier ce code.
- **`bim_api` parle à Grafana via `http://grafana:3000` (réseau Docker interne)**, alors que le
  frontend le contacte depuis le navigateur via `localhost:3000` — deux URLs différentes pour
  le même service, ne pas les confondre en cas de bug de connexion Grafana.
- **Pattern de rebuild frontend établi cette session** : `npx tsc -b --noEmit` (depuis le
  dossier frontend concerné) → `docker compose -f docker-compose.fusion.yml up -d --build
  <service>` → `docker logs <conteneur> --tail 15` + `curl -s -o /dev/null -w "status:
  %{http_code}\n" http://localhost:<port>` pour vérifier avant de considérer un changement
  terminé.

---

## 6. État fonctionnel actuel par application

### BOS Configurateur (`web-app-configuration/frontend/`, port 5173)
- Connexion/RBAC opérationnels (Administrateur/Configurateur).
- Import IFC (glisser-déposer, une discipline ou en lot), validation contre la charte BIM,
  ingestion du graphe spatial + archivage SeaweedFS.
- Explorateur BIM à 3 onglets : **Graphe** (Cytoscape, avec la popup capteurs temps réel
  d'Aminata sur clic d'un nœud Equipment), **Maquette 3D** (ThatOpenEngine, popup fiche IFC
  statique sur clic d'un élément — voir nuance §3 point 14 — plus navigation immersion
  Orbite/Première personne avec sélecteur d'étage → plan 2D cliquable → téléportation), et
  **Supervision** (cockpits Grafana embarqués par pièce/étage).

### BOS Exécution (`web-app-execution/frontend-client/`, port 5174)
- Connexion/RBAC opérationnels (Administrateur/Configurateur/Client).
- Visualisation 3D (`BimViewer.tsx` + `BimModelViewer.tsx`, copie du composant Configurateur)
  en lecture seule stricte : aucune UI d'ingestion, uniquement les zones déjà ingérées côté
  Configurateur (backend refuse explicitement l'écriture à ce rôle, voir §2.2).
- Pas encore de graphe Cytoscape ni de cockpits Grafana côté Exécution à ce jour (périmètre 3D
  uniquement décidé pour cette itération) — extension possible plus tard si demandé.

### userapp
Non démarrée, aucun code dans ce dépôt.

---

## 7. Ce qui reste ouvert / dette connue

- **Popup 3D vs popup graphe non reliées** : la popup au clic sur un élément 3D
  (`BimModelViewer.tsx`) n'affiche que des métadonnées IFC statiques (Nom, Type IFC, GlobalId)
  ; la popup temps réel (`EquipmentTelemetryPopup.tsx`) n'existe que côté graphe. Discussion en
  cours (dans le chat, pas encore implémentée) : ajouter au clic 3D une mise en surbrillance de
  l'élément + un menu radial ("roue") avec plusieurs actions (fiche IFC, données temps réel,
  aller au nœud correspondant dans le graphe) — fonctionnalités pas encore arbitrées/codées.
- **`getCategory()` revient parfois vide** sur certains éléments cliqués en 3D (observé sur
  plusieurs captures utilisateur, ex. une porte qui devrait renvoyer `IfcDoor`) — cause non
  encore investiguée, points d'audit déjà en place (§4.5) pour la diagnostiquer via les logs
  F12 au prochain cas reproduit.
- **Psets (property sets) et relations spatiales pas affichés dans la popup 3D** — seuls les
  attributs directs IFC (`Name`, `Category`, `GlobalId`) sont extraits aujourd'hui ; les Psets
  (ex. `FireRating`, `IsExternal`, matériau) et la structure spatiale contenante
  (`ContainedInStructure`) sont accessibles via `item.getRelations()`/`model.getItemsData()`
  mais pas encore branchés.
- **Pas de système de ticketing/documentation par équipement** — contrairement à des éditeurs
  comparables (ex. SpinalCore, mentionné en référence dans le chat), rien dans BOS Light ne
  permet aujourd'hui d'attacher un ticket de maintenance ou un document à un équipement/GUID.
  Serait un vrai nouveau module, pas un simple ajout de popup.
- **`web-app-execution` (ancien dépôt historique)** reste mentionné dans certains README comme
  "historique non synchronisé" — `frontend-client/` dans ce dépôt fusionné est la version de
  référence actuelle, ne pas repartir de l'ancien dépôt séparé.
- **README.md à la racine légèrement daté** : ne mentionne pas encore Grafana comme 12e/13e
  service ni l'onglet Supervision — à rafraîchir si quelqu'un s'y fie pour l'architecture
  exacte (préférer ce document + une lecture directe de `docker-compose.fusion.yml` en cas de
  doute).
