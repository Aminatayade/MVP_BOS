# Mise à jour des images & retour à une version antérieure

Procédure concrète associée au rapport `Rapport_MAJ_Rollback_BOS_Light_2026-08-20.pdf`. Deux
catégories d'images, deux procédures distinctes — voir `docker-compose.fusion.yml` pour le détail de
ce qui est épinglé aujourd'hui.

## Avant toute mise à jour : sauvegarder les données

Un retour en arrière logiciel (rollback d'image) **ne restaure pas** l'état antérieur des données si
la nouvelle version a modifié un schéma ou un format de stockage entre-temps. Systématiquement, avant
de mettre à jour une image tierce ou un microservice maison :

```powershell
./scripts/backup-volumes.ps1
```

Sauvegarde `postgres_data`, `postgres_app_data` et `arcadedb_data` dans `backups/<horodatage>/`
(dossier gitignored — sauvegardes locales, pas versionnées).

## Images tierces (PostgreSQL, Keycloak, ArcadeDB, NATS, SeaweedFS)

Toutes sont épinglées sur une version exacte dans `docker-compose.fusion.yml` (tag pour
PostgreSQL/Keycloak, digest pour ArcadeDB/NATS/SeaweedFS — voir les commentaires en tête de chaque
service pour la justification).

**Monter de version :**
1. Sauvegarder les données (étape ci-dessus).
2. Récupérer le digest de la nouvelle version :
   ```
   docker pull <image>:<nouveau-tag>
   docker image inspect <image>:<nouveau-tag> --format "{{index .RepoDigests 0}}"
   ```
3. Remplacer la ligne `image:` du service concerné dans `docker-compose.fusion.yml` par ce digest
   (ou ce tag, si l'éditeur maintient des tags numérotés stables).
4. `docker compose -f docker-compose.fusion.yml up -d <service>`
5. Vérifier que le service redémarre sainement (`docker compose logs -f <service>`) avant de
   considérer la mise à jour terminée.

**Revenir en arrière :** remettre l'ancien digest/tag (conservé dans l'historique Git du fichier —
`git log -p docker-compose.fusion.yml`) à la place du nouveau, puis répéter les étapes 4 et 5.
Aucune reconstruction n'est nécessaire : l'image précédente est simplement re-tirée du registre.

## Microservices maison (frontend, api-gateway, bim_api, timeseries_api, bim_validator)

Chaque service a désormais un nom d'image explicite, tagué via la variable `APP_VERSION` (voir
`.env.example`). Par défaut (`APP_VERSION` vide ou absente), tout retombe sur le tag `dev` : l'usage
local quotidien (`docker compose up --build`) est inchangé.

**Publier une version identifiable :**
1. Positionner `APP_VERSION` dans `.env` sur une référence unique — le hash court du commit Git
   (`git rev-parse --short HEAD`) est recommandé, pour toujours pouvoir relier une image publiée au
   code exact qui l'a produite.
2. `docker compose -f docker-compose.fusion.yml build`
3. Une fois le workflow GitHub Actions (`.github/workflows/build-and-push.yml`) en place, cette même
   étape se fait automatiquement à chaque push sur `testifc`/`main`, et l'image est en plus poussée
   vers `ghcr.io/devoptim-bos/bos-light-<service>` — donc récupérable sans reconstruction locale.

**Revenir en arrière :**
1. Sauvegarder les données (étape en tête de document).
2. Dans `.env`, positionner `APP_VERSION` sur le tag de la version précédente, connue et
   fonctionnelle (le hash de commit correspondant).
3. Récupérer cette version déjà publiée plutôt que de la reconstruire :
   ```
   docker compose -f docker-compose.fusion.yml pull <service>
   docker compose -f docker-compose.fusion.yml up -d --no-build <service>
   ```
   `--no-build` est important : sans lui, Compose reconstruirait l'image depuis le code source actuel
   au lieu de redéployer la version taguée récupérée du registre.

## Restaurer un volume depuis une sauvegarde

À utiliser si le rollback logiciel seul ne suffit pas (schéma de données incompatible avec la version
restaurée). Remplace intégralement le contenu du volume par celui de la sauvegarde — arrêter le
service concerné avant de l'exécuter :

```
docker compose -f docker-compose.fusion.yml stop <service>

docker run --rm `
  -v <projet>_<volume>:/data `
  -v <chemin-vers-le-dossier-de-sauvegarde>:/backup `
  alpine sh -c "rm -rf /data/* && tar xzf /backup/<volume>.tar.gz -C /data"

docker compose -f docker-compose.fusion.yml up -d <service>
```

`<projet>` est la valeur de `COMPOSE_PROJECT_NAME` (`.env`), `<volume>` l'un de `postgres_data`,
`postgres_app_data` ou `arcadedb_data`.
