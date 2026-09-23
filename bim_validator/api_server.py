import json
import os
import sys
import tempfile
import time
import base64
import requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))

try:
    from adapters.ifcopenshell_adapter import IfcOpenShellAdapter
except Exception:
    IfcOpenShellAdapter = None
from adapters.json_rules_adapter import JsonRulesRepositoryAdapter
from adapters.json_equipment_catalog_adapter import JsonEquipmentCatalogAdapter
from adapters.seaweedfs_adapter import SeaweedFsStorageAdapter
from adapters.arcadedb_ts_adapter import ArcadeDbTimeseriesAdapter
from adapters.grafana_dashboard_sync_adapter import GrafanaDashboardSyncAdapter
from application.services.bim_graph_ingestion_service import BimGraphIngestionResult, BimGraphIngestionService
from application.bim_validator_service import BimValidatorService
from application.bim_ingestion_service import BimIngestionService
from application.equipment_simulation_service import EquipmentSimulationService
from application.services.occupancy_provisioning_service import OccupancyProvisioningService
from application.occupancy_aggregation import (
    bucket_daily_occupancy_pct,
    classify_threshold_counts,
    compute_donut,
    compute_reservation_rate,
    filter_by_plage,
    per_sensor_occupancy_pct,
)
# Alias : energy_aggregation.filter_by_plage a une sémantique de plage
# différente (toute_journee/ouvrable/hors_ouvrable, pas de coupure le
# week-end) de occupancy_aggregation.filter_by_plage -- même nom, deux
# fonctions distinctes, jamais interchangeables.
from application.energy_aggregation import (
    bucket_daily_by_subcategory,
    densify_daily_subcategories,
    filter_by_plage as energy_filter_by_plage,
    month_total_by_subcategory,
)
from domain.entities import ValidationReport
from infrastructure.adapters.arcadedb_repository_adapter import ArcadeDbRepositoryAdapter
from infrastructure.secrets import read_secret
from infrastructure.adapters.seaweed_storage_adapter import SeaweedStorageAdapter
from infrastructure.zone_database import (
    LEGACY_DATABASE,
    invalidate_zone_cache,
    resolve_database_for_zone,
)
from datetime import datetime, timezone

# Singleton process : le pool de threads du backfill de simulation doit être
# PARTAGÉ entre tous les fichiers d'un même batch d'ingestion (chacun
# reconstruit son propre BimGraphIngestionService), pas recréé par fichier --
# sinon un batch de N fichiers ouvrirait N pools indépendants et le
# plafonnement de concurrence (max_workers) perdrait tout son sens.
_equipment_simulation_service_singleton: EquipmentSimulationService | None = None


_AGGREGATORS = {
    "sum": sum,
    "avg": lambda values: sum(values) / len(values),
    "max": max,
    "min": min,
}


def _bucket_and_aggregate(rows: list[dict], *, bucket_seconds: int, aggregation: str = "avg") -> list[dict]:
    """Regroupe des lectures brutes multi-équipements/multi-pièces par fenêtre
    temporelle et les réduit à UNE seule série exploitable par un panel
    Grafana. `aggregation="sum"` pour un compteur cumulatif (énergie, eau :
    la grandeur qui a du sens au niveau site/étage est le TOTAL, pas la
    moyenne des compteurs individuels) ; `aggregation="avg"` (par défaut)
    pour une grandeur physique comme la température ; `"max"`/`"min"`
    disponibles pour un choix explicite (variable Grafana "Opération"),
    voir le paramètre `aggregation` de /graph/location-readings."""
    aggregator = _AGGREGATORS.get(aggregation, _AGGREGATORS["avg"])
    buckets: dict[int, list[float]] = {}
    for row in rows:
        ts_raw = row.get("ts")
        value = row.get("value")
        if ts_raw is None or value is None:
            continue
        try:
            dt = datetime.strptime(str(ts_raw), "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            continue
        bucket_epoch = int(dt.timestamp() // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket_epoch, []).append(float(value))

    readings = []
    for epoch in sorted(buckets):
        values = buckets[epoch]
        aggregated = aggregator(values)
        readings.append({
            "ts": datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S.000"),
            "value": round(aggregated, 2),
            "sample_count": len(values),
        })
    return readings


# Métriques du catalogue dont le nom porte "cumulative" (compteurs kWh/m³) :
# leur agrégation multi-équipements/multi-pièces doit être une SOMME (le
# total consommé), pas une moyenne. Toute autre métrique reste moyennée.
def _aggregation_for_metric(metric: str) -> str:
    return "sum" if "cumulative" in metric else "avg"


def _cypher_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class ApiHandler(BaseHTTPRequestHandler):
    END_TO_END_BATCH_ROUTE = "/ingest-batch"

    def _send_json(self, payload, status=200):
        # Pas d'en-tête CORS : ce service n'est plus exposé sur l'hôte (voir
        # fusion/docker-compose.yml) et n'est appelé que server-to-server par
        # l'API Gateway BOS Light (bim_proxy.py), jamais directement par un
        # navigateur -- CORS n'a donc plus lieu d'être ici.
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def _arcadedb_repo_factory(self):
        """Ferme sur les identifiants de connexion ArcadeDB (lus une seule
        fois) et renvoie une fonction `database -> ArcadeDbRepositoryAdapter`
        -- remplace la répétition, à chaque route, du bloc "lire les 4 env
        vars/secrets puis construire l'adapter". Nécessaire pour
        `resolve_database_for_zone` (isolation par zone, Phase 1) qui doit
        pouvoir sonder PLUSIEURS bases candidates (celle de la zone, puis la
        base legacy partagée) sans connaître elle-même ces identifiants.
        """
        arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
        arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
        arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")

        def factory(database: str) -> ArcadeDbRepositoryAdapter:
            return ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=database,
                username=arcadedb_username,
                password=arcadedb_password,
            )

        return factory

    def do_DELETE(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/graph/zone":
                # Supprime intégralement une zone : équipements, pièces,
                # étages, bâtiment, site, typologies -- ET l'historique
                # time-series associé. Permet de nettoyer une maquette de
                # test sans laisser de résidus qui polluent le graphe
                # (plusieurs Building affichés côte à côte) ou la variable
                # Grafana "location" (entrées mortes -> "No data").
                #
                # MATCH (n) non typé ne fonctionne PAS ici (vérifié :
                # renvoie toujours 0, cohérent avec l'absence de supertype
                # générique V déjà documentée pour SQL -- Cypher a la même
                # limite) : chaque type concret est énuméré séparément,
                # même motif que la déduplication post-ingestion.
                # DETACH DELETE (pas juste DELETE) retire aussi les arêtes
                # (HAS_CHILD/HAS_GROUP/HAS_MEMBER/LOCATED_IN) du nœud.
                params = parse_qs(parsed.query)
                zone = (params.get("zone", [None])[0] or "").strip()
                if not zone:
                    self._send_json({"error": "zone query param is required"}, 400)
                    return

                repo_factory = self._arcadedb_repo_factory()
                database = resolve_database_for_zone(repo_factory, zone, for_write=False)
                if database is None:
                    self._send_json({"error": "unknown zone", "zone": zone}, 404)
                    return
                repo = repo_factory(database)
                # EquipmentReading (magasin timeseries) reste TOUJOURS dans la
                # base partagée historique, quelle que soit la zone (voir
                # /graph/location-readings) -- une zone avec sa propre base
                # graphe (toute zone créée depuis l'isolation par zone,
                # Phase 1) n'a JAMAIS ce type défini chez elle : y lancer un
                # DELETE FROM EquipmentReading échoue avec une
                # SchemaException ArcadeDB ("Type ... was not found"), pas
                # juste "0 lignes supprimées". Pour une zone legacy (ex.
                # Batipompe), `timeseries_repo` pointe vers la même base que
                # `repo` -- aucun cas particulier à gérer.
                # timeout_seconds relevé (défaut 30s) : marge de sécurité en
                # plus du passage au paramètre lié ci-dessous -- une DELETE
                # sur une base de plusieurs millions de lignes n'a pas
                # forcément le même profil de coût qu'une simple lecture.
                timeseries_repo = ArcadeDbRepositoryAdapter(
                    base_url=os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480"),
                    database=LEGACY_DATABASE,
                    username=os.environ.get("ARCADEDB_USERNAME", "root"),
                    password=read_secret("ARCADEDB_PASSWORD", "testBIM123"),
                    timeout_seconds=120,
                )
                try:
                    escaped_zone = _cypher_escape(zone)

                    # 1) Equipment d'abord : il faut leurs global_id pour
                    # purger EquipmentReading (magasin séparé, pas une
                    # relation ArcadeDB) AVANT de supprimer les nœuds.
                    equip_rows = repo.query_cypher(
                        f"MATCH (e:Equipment) WHERE e.zone = '{escaped_zone}' RETURN e.global_id AS gid"
                    ).get("result", [])
                    equip_ids = [r["gid"] for r in equip_rows if r.get("gid")]

                    # 2) Chaque type de nœud spatial/métier, un par un --
                    # AVANT la purge d'EquipmentReading (ordre inversé par
                    # rapport à avant) : c'est cette étape qui fait vraiment
                    # "disparaître" la zone (plus dans /graph/zones, plus
                    # dans l'UI), et elle est rapide (quelques secondes) même
                    # pour une maquette volumineuse. La purge des lectures,
                    # elle, peut dépasser largement 2 minutes sur une base
                    # partagée de plusieurs millions de lignes (ex: clinic
                    # multi-disciplines, 500 équipements) -- elle ne doit
                    # jamais empêcher la zone de disparaître pour autant.
                    deleted_nodes = {}
                    for vertex_type in ("Equipment", "Room", "Floor", "Building", "Site", "Typology"):
                        count_before = repo.query_cypher(
                            f"MATCH (n:{vertex_type}) WHERE n.zone = '{escaped_zone}' RETURN count(n) AS n"
                        ).get("result", [])
                        n_before = count_before[0].get("n", 0) if count_before else 0
                        if n_before:
                            repo.query_cypher(
                                f"MATCH (n:{vertex_type}) WHERE n.zone = '{escaped_zone}' DETACH DELETE n"
                            )
                        deleted_nodes[vertex_type] = n_before

                    # Sans ça, une zone supprimée puis ré-ingérée sous le
                    # même nom resterait à tort résolue vers son ancienne
                    # base (le cache de résolution est sinon permanent).
                    invalidate_zone_cache(zone)

                    # 3) Purge best-effort des lectures -- paramètre lié
                    # (`:ids`), pas une liste de littéraux interpolés (même
                    # constat déjà documenté ailleurs dans ce fichier pour
                    # `global_id IN` : 13-52s avec des littéraux contre 5-6s
                    # en paramètre lié). Un échec/timeout ici est loggé mais
                    # NE FAIT PAS échouer la suppression de zone -- les
                    # lectures orphelines restent inertes (plus aucun nœud
                    # Equipment ne les référence) et sans effet fonctionnel
                    # jusqu'à un nettoyage ultérieur.
                    deleted_readings: int | None = 0
                    if equip_ids:
                        try:
                            resp = timeseries_repo.query_sql(
                                "DELETE FROM EquipmentReading WHERE global_id IN :ids",
                                params={"ids": equip_ids},
                            )
                            rows = resp.get("result") or []
                            deleted_readings = rows[0].get("count", 0) if rows else 0
                        except Exception as exc:
                            print(
                                f"Warning: purge EquipmentReading après suppression de la zone '{zone}' "
                                f"a échoué (zone déjà supprimée du graphe, sans effet fonctionnel) : {exc}",
                                file=sys.stderr,
                            )
                            deleted_readings = None

                    self._send_json({
                        "ok": True,
                        "zone": zone,
                        "deleted_readings": deleted_readings,
                        "deleted_nodes": deleted_nodes,
                    })
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 500)
                return
            self._send_json({"error": "Not found"}, 404)
        except Exception as e:
            self._send_json({"error": f"Server error: {str(e)}"}, 500)

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return
        # Simple graph view: /graph/view?floor_id=<global_id>
        parsed = urlparse(self.path)
        if parsed.path == "/graph/view":
            params = parse_qs(parsed.query)
            floor_id = params.get("floor_id", [None])[0]
            zone = params.get("zone", [None])[0]
            if not floor_id:
                self._send_json({"error": "floor_id query param is required"}, 400)
                return

            # Build a simple cypher to fetch the floor, its rooms and equipment
            repo_factory = self._arcadedb_repo_factory()
            if zone:
                database = resolve_database_for_zone(repo_factory, zone, for_write=False)
                if database is None:
                    self._send_json({"error": "unknown zone", "zone": zone}, 404)
                    return
            else:
                # zone facultatif ici (compat ascendante) : sans elle, aucun
                # moyen de choisir une base par zone -- repli direct sur la
                # base legacy partagée, comme avant ce changement.
                database = LEGACY_DATABASE
            repo = repo_factory(database)

            # Note: ArcadeDB may return nodes under a wrapper; frontend will adapt.
            #
            # MATCH (f)-[:HAS_CHILD]->(r:Room) SEUL (ancienne version) exigeait
            # au moins une Room pour retourner ne serait-ce que la ligne du
            # floor : un étage industriel sans AUCUN IfcSpace (équipement
            # rattaché DIRECTEMENT au Floor, pas via une Room) ne retournait
            # alors JAMAIS son équipement, même s'il en avait -- la requête ne
            # matchait tout simplement rien. OPTIONAL MATCH + WHERE loc = f OR
            # loc IN rooms couvre les deux cas de rattachement (Room ou Floor
            # direct) dans une seule réponse.
            #
            # "zone" optionnel mais fortement recommandé côté appelant : un
            # GlobalId IFC n'est unique qu'au sein d'un même fichier/projet --
            # une même maquette réimportée dans PLUSIEURS zones (courant en
            # test) produit plusieurs nœuds Floor distincts partageant le
            # MÊME global_id. Sans filtre de zone, le MATCH les trouve TOUS,
            # et la réponse mélange l'équipement de zones différentes dans
            # une seule liste (constaté : 3 zones, 3× le même comptage
            # d'équipement, agrégées côté frontend en une seule liste géante).
            escaped_floor_id = _cypher_escape(floor_id)
            floor_match = (
                f"Floor {{global_id: '{escaped_floor_id}', zone: '{_cypher_escape(zone)}'}}"
                if zone
                else f"Floor {{global_id: '{escaped_floor_id}'}}"
            )
            cypher = f"""
MATCH (f:{floor_match})
OPTIONAL MATCH (f)-[:HAS_CHILD]->(r:Room)
WITH f, collect(distinct r) AS rooms
OPTIONAL MATCH (e:Equipment)-[:LOCATED_IN]->(loc)
WHERE loc = f OR loc IN rooms
RETURN f AS floor, rooms AS rooms, collect(distinct e) AS equipment
"""
            try:
                resp = repo.query_cypher(cypher)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/floors":
            # return a simple list of floor nodes (global_id + name + le
            # global_id du Building parent). "zone" est optionnel (compat.
            # avec les appelants existants qui ne le passent pas) -- sans
            # lui, retombe sur le comportement global d'origine.
            #
            # building_id inclus via HAS_CHILD (Building -> Floor) : une
            # zone peut légitimement contenir PLUSIEURS bâtiments (une
            # maquette par discipline, chacune avec son propre GlobalId IFC
            # -- ex: AO+AP dans la même zone) ; sans cette info, le
            # sélecteur d'étage de l'Immersion mélangeait les étages de
            # bâtiments différents dans une seule liste plate (bug remonté
            # par l'utilisateur : "il y a des niveaux supplémentaires qui ne
            # se trouvent pas dans le graphe").
            params = parse_qs(parsed.query)
            zone = params.get("zone", [None])[0]
            repo_factory = self._arcadedb_repo_factory()
            if zone:
                database = resolve_database_for_zone(repo_factory, zone, for_write=False)
                if database is None:
                    self._send_json({"error": "unknown zone", "zone": zone}, 404)
                    return
            else:
                database = LEGACY_DATABASE
            repo = repo_factory(database)
            try:
                if zone:
                    cy = (
                        f"MATCH (b:Building)-[:HAS_CHILD]->(f:Floor {{zone: '{_cypher_escape(zone)}'}}) "
                        f"RETURN f, b.global_id AS building_id LIMIT 500"
                    )
                else:
                    cy = (
                        "MATCH (b:Building)-[:HAS_CHILD]->(f:Floor) "
                        "RETURN f, b.global_id AS building_id LIMIT 500"
                    )
                resp = repo.query_cypher(cy)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/buildings":
            # "zone" optionnel, même raisonnement que /graph/floors ci-dessus
            # -- corrige le bug où "Visualiser graphe" affichait TOUS les
            # bâtiments jamais ingérés (toutes zones confondues) au lieu de
            # ceux de la zone courante, donnant l'impression qu'une ancienne
            # maquette restait "en cache".
            params = parse_qs(parsed.query)
            zone = params.get("zone", [None])[0]
            repo_factory = self._arcadedb_repo_factory()
            if zone:
                database = resolve_database_for_zone(repo_factory, zone, for_write=False)
                if database is None:
                    self._send_json({"error": "unknown zone", "zone": zone}, 404)
                    return
            else:
                database = LEGACY_DATABASE
            repo = repo_factory(database)
            try:
                if zone:
                    cy = f"MATCH (b:Building {{zone: '{_cypher_escape(zone)}'}}) RETURN b LIMIT 500"
                else:
                    cy = "MATCH (b:Building) RETURN b LIMIT 500"
                resp = repo.query_cypher(cy)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/equipment":
            # Vue admin/inspecteur peu utilisée -- zone désormais requise
            # (avant : tout l'Equipment, toutes zones confondues, sur la
            # seule base par défaut ; incohérent avec l'isolation par zone).
            params = parse_qs(parsed.query)
            zone = (params.get("zone", [None])[0] or "").strip()
            if not zone:
                self._send_json({"error": "zone query param is required"}, 400)
                return
            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)
            try:
                cy = f"MATCH (e:Equipment {{zone: '{_cypher_escape(zone)}'}}) RETURN e LIMIT 500"
                resp = repo.query_cypher(cy)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/neighbors":
            params = parse_qs(parsed.query)
            gid = params.get("global_id", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not gid or not zone:
                self._send_json({"error": "global_id and zone query params are required"}, 400)
                return

            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)

            # Les relations LOCATED_IN vers un Equipment sont exclues des "voisins
            # structurels" : une pièce peut porter des dizaines d'équipements, ce
            # qui noyait le graphe Cytoscape en un clic. Le compte est renvoyé à
            # part ; le frontend affiche un nœud groupé "N équipements" et ne va
            # chercher la liste réelle (/graph/room-equipment) qu'à la demande.
            cypher = f"""
MATCH (n {{global_id: '{gid}'}})
OPTIONAL MATCH (n)-[r]-(m)
  WHERE NOT (type(r) = 'LOCATED_IN' AND 'Equipment' IN labels(m))
WITH n, collect(distinct m) AS structural_neighbors
OPTIONAL MATCH (eq:Equipment)-[:LOCATED_IN]->(n)
RETURN n AS node, structural_neighbors AS neighbors, count(distinct eq) AS equipment_count
"""
            try:
                resp = repo.query_cypher(cypher)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/room-equipment":
            params = parse_qs(parsed.query)
            gid = params.get("global_id", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not gid or not zone:
                self._send_json({"error": "global_id and zone query params are required"}, 400)
                return

            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)

            cypher = f"""
MATCH (e:Equipment)-[:LOCATED_IN]->(r {{global_id: '{gid}'}})
RETURN e AS node
"""
            try:
                resp = repo.query_cypher(cypher)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/sources":
            # Point 6 de la roadmap (jumeau numerique) : liste les fichiers IFC
            # sources deja ingeres pour une zone, un par discipline, pour que le
            # frontend puisse les recharger et les superposer dans le visualiseur
            # 3D. Seule la discipline "backbone" (ex: ARCHI) cree des noeuds
            # Building/Floor/Room ; les autres (ELEC, CVC...) n'ajoutent que des
            # noeuds Equipment -- d'ou une requete SANS label explicite pour
            # couvrir tous les types de noeuds porteurs de storage_url.
            params = parse_qs(parsed.query)
            zone = (params.get("zone", [None])[0] or "").strip()
            if not zone:
                self._send_json({"error": "zone query param is required"}, 400)
                return

            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)

            cypher = f"""
MATCH (n {{zone: '{_cypher_escape(zone)}'}}) WHERE n.storage_url IS NOT NULL
RETURN DISTINCT n.discipline AS discipline, n.storage_url AS storage_url
"""
            try:
                resp = repo.query_cypher(cypher)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/models/download":
            # Retelecharge un IFC deja valide depuis SeaweedFS pour le visualiseur
            # 3D (ThatOpenEngine). Reutilise SeaweedStorageAdapter.download_ifc,
            # deja utilise cote ingestion -- seule difference : ici la source est
            # relayee telle quelle au client au lieu d'etre parsee.
            params = parse_qs(parsed.query)
            url = params.get("url", [None])[0]
            if not url:
                self._send_json({"error": "url query param is required"}, 400)
                return

            # Controle anti-SSRF : n'accepter que des URLs pointant vers l'hote
            # SeaweedFS interne configure -- sans ce controle, cette route
            # deviendrait un relais ouvert vers n'importe quelle adresse
            # joignable depuis le conteneur bim_api.
            master_url = os.environ.get("SEAWEEDFS_MASTER_URL", "http://localhost:9333")
            expected_host = urlparse(master_url).hostname
            requested_host = urlparse(url).hostname
            if not requested_host or requested_host != expected_host:
                self._send_json({"error": "url host is not an authorized storage host"}, 400)
                return

            seaweed_reader = SeaweedStorageAdapter()
            local_path = None
            try:
                local_path = seaweed_reader.download_ifc(url)
                with open(local_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 502)
            finally:
                if local_path and os.path.exists(local_path):
                    try:
                        os.unlink(local_path)
                    except OSError:
                        pass
            return
        if parsed.path == "/graph/locations":
            # Rooms ET Floors dans une seule liste : certaines maquettes (ex:
            # celles sans IfcSpace) rattachent les équipements directement au
            # Floor plutôt qu'à un Room -- le sélecteur Grafana doit couvrir
            # les deux niveaux, pas seulement les Rooms. zone désormais
            # requise (isolation par zone, Phase 1) : avant, cette route
            # renvoyait Room/Floor de TOUTES les zones sans distinction.
            params = parse_qs(parsed.query)
            zone = (params.get("zone", [None])[0] or "").strip()
            if not zone:
                self._send_json({"error": "zone query param is required"}, 400)
                return
            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)
            try:
                locations = []
                for label in ("Room", "Floor"):
                    resp = repo.query_cypher(
                        f"MATCH (n:{label} {{zone: $zone}}) RETURN n LIMIT 1000",
                        params={"zone": zone},
                    )
                    rows = resp.get("result") or []
                    for row in rows:
                        node = row.get("n", row)
                        gid = node.get("global_id")
                        if not gid:
                            continue
                        locations.append({
                            "global_id": gid,
                            "name": node.get("name") or gid,
                            "type": label,
                            "zone": node.get("zone"),
                        })
                self._send_json({"ok": True, "locations": locations})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/location-readings":
            # Agrégation d'une métrique pour TOUS les équipements rattachés
            # (directement) à UN OU PLUSIEURS Room/Floor donnés -- le niveau
            # métier le plus pertinent pour un cockpit client (le schéma
            # spatial Building->Floor->Room est fiable, contrairement au
            # rattachement par équipement individuel qui dépend de la qualité
            # de nommage de chaque maquette). `global_id` accepte une liste
            # séparée par des virgules pour comparer/cumuler plusieurs
            # pièces/étages en une seule requête (ex: "les deux premiers
            # étages" -- voir variable Grafana "location", multi-sélection).
            params = parse_qs(parsed.query)
            gid_param = params.get("global_id", [None])[0]
            metric = params.get("metric", [None])[0]
            bucket_seconds = int(params.get("bucket_seconds", ["10"])[0])
            raw_limit = int(params.get("raw_limit", ["5000"])[0])
            # Facultatif : "sum"/"avg"/"max"/"min" (variable Grafana
            # "Opération") -- sans ce paramètre, on retombe sur le choix
            # automatique _aggregation_for_metric (sum pour un compteur
            # cumulatif, avg sinon), comme avant.
            aggregation_override = params.get("aggregation", [None])[0]
            # Facultatif : limite aux équipements d'UNE catégorie (une des
            # clés EQUIPMENT_CATEGORIES : cvc/electricity/security/sanitary/
            # furniture/controls) -- pour un widget du constructeur de
            # cockpits qui veut isoler "juste l'éclairage" plutôt que tout
            # l'équipement du périmètre. Absent = comportement inchangé
            # (tous les équipements du périmètre, comme avant).
            category = params.get("category", [None])[0]
            # Facultatif : bornes epoch millis (widget "Barres dans le temps"
            # avec pagination "Mois précédent/suivant" -- voir period.ts côté
            # frontend). Absents = comportement inchangé (les `raw_limit`
            # points les plus récents, comme avant), pour ne rien casser côté
            # Grafana/widgets existants qui n'envoient jamais ces paramètres.
            start_param = params.get("start", [None])[0]
            end_param = params.get("end", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not gid_param or not metric or not zone:
                self._send_json({"error": "global_id, metric and zone query params are required"}, 400)
                return
            location_ids = sorted({g.strip() for g in gid_param.split(",") if g.strip()})
            if not location_ids:
                self._send_json({"error": "global_id and metric query params are required"}, 400)
                return

            # Deux bases : la traversée du graphe (résoudre quels équipements
            # sont dans le périmètre) doit passer par la base DE LA ZONE
            # (isolation par zone, Phase 1) ; la lecture d'EquipmentReading
            # reste sur le magasin timeseries partagé, INCHANGÉ quelle que
            # soit la zone -- ce dernier est hors périmètre de cette phase
            # (voir le plan "Isolation des données par zone"). Pour une zone
            # legacy (ex: Batipompe), les deux pointent vers la même base
            # "bim" -- aucun cas particulier à gérer, le code est identique.
            repo_factory = self._arcadedb_repo_factory()
            graph_database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if graph_database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            graph_repo = repo_factory(graph_database)
            timeseries_repo = repo_factory(LEGACY_DATABASE)
            try:
                escaped_location_ids = [lid.replace("'", "\\'") for lid in location_ids]
                location_list = ", ".join(f"'{lid}'" for lid in escaped_location_ids)
                # Descend la hiérarchie spatiale (HAS_CHILD*0..) avant de
                # chercher les équipements : un Equipment n'est jamais
                # LOCATED_IN un Building directement (toujours Room ou
                # Floor). Sans ce traversal, passer le global_id d'un
                # Building ici (ex: popup Grafana du widget "Consommation
                # électrique" de BOS Exécution) renvoyait toujours 0
                # équipement. *0.. inclut le nœud lui-même (longueur 0), donc
                # le comportement pour un Room/Floor déjà utilisé ailleurs
                # est inchangé (testé : mêmes résultats qu'avant ce fix).
                category_clause = (
                    f" AND e.category = '{_cypher_escape(category)}'" if category else ""
                )
                neighbors_cy = f"""
MATCH (loc)
WHERE loc.global_id IN [{location_list}]
OPTIONAL MATCH (loc)-[:HAS_CHILD*0..]->(descendant)
WITH collect(DISTINCT loc) + collect(DISTINCT descendant) AS all_locs
UNWIND all_locs AS l
MATCH (e:Equipment)-[:LOCATED_IN]->(l)
WHERE true{category_clause}
RETURN DISTINCT e.global_id AS gid
"""
                resp = graph_repo.query_cypher(neighbors_cy)
                rows = resp.get("result") or []
                equipment_ids = sorted({row.get("gid") for row in rows if row.get("gid")})

                if not equipment_ids:
                    self._send_json({"ok": True, "equipment_count": 0, "readings": []})
                    return

                range_clause = ""
                sql_params: dict = {"metric": metric, "ids": equipment_ids}
                if start_param:
                    range_clause += " AND ts >= :start_ts"
                    sql_params["start_ts"] = int(start_param)
                if end_param:
                    range_clause += " AND ts <= :end_ts"
                    sql_params["end_ts"] = int(end_param)
                # ORDER BY ts DESC : avec un lieu qui a beaucoup d'équipements
                # (chacun jusqu'à 7 jours d'historique horaire), le nombre
                # total de lignes dépasse vite `raw_limit` -- trier ASC
                # tronquait alors sur les lignes les plus ANCIENNES et
                # perdait silencieusement les points récents (jusqu'à faire
                # disparaître les dernières heures dans un panel Grafana
                # "Last 1 hour", ou empêcher toute détection de surchauffe en
                # temps réel côté graphe). _bucket_and_aggregate retrie de
                # toute façon par epoch croissant à la fin, donc DESC ici ne
                # change pas l'ordre du résultat, seulement QUELLES lignes
                # sont gardées avant troncature. Avec `end` explicite (mois
                # précédent, pas le mois courant), DESC part bien de la borne
                # `end` -- pas de "now" -- donc les points gardés restent les
                # plus récents DE la période demandée, pas les plus récents
                # tout court.
                # `global_id IN :ids` en paramètre lié, PAS une liste de
                # littéraux interpolés dans le texte SQL : mesuré 13-52s (et
                # même une connexion perdue côté serveur pour ~4800 IDs) avec
                # des littéraux, contre ~5-6s de façon fiable en paramètre lié
                # -- ArcadeDB n'a alors plus à reparser un texte de plusieurs
                # centaines de Ko à chaque appel avec un contenu différent.
                sql = (
                    "SELECT ts, value FROM EquipmentReading WHERE "
                    f"metric = :metric AND global_id IN :ids{range_clause} "
                    f"ORDER BY ts DESC LIMIT {raw_limit}"
                )
                ts_resp = timeseries_repo.query_sql(sql, params=sql_params)
                ts_rows = ts_resp.get("result") or []
                aggregation = (
                    aggregation_override if aggregation_override in _AGGREGATORS else _aggregation_for_metric(metric)
                )
                readings = _bucket_and_aggregate(ts_rows, bucket_seconds=bucket_seconds, aggregation=aggregation)
                self._send_json({
                    "ok": True,
                    "location_count": len(location_ids),
                    "equipment_count": len(equipment_ids),
                    "aggregation": aggregation,
                    "raw_points": len(ts_rows),
                    "readings": readings,
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/location-readings-summary":
            # Équivalent de /graph/location-readings mais pour un panel
            # Grafana "Stat"/"Gauge" qui n'a besoin que d'UNE valeur sur la
            # période (total conso, température moyenne...), pas d'une
            # courbe. Contrairement à l'endpoint ci-dessus, aucune ligne
            # brute n'est rapatriée : SUM/AVG/MAX/MIN sont calculés
            # NATIVEMENT par ArcadeDB (confirmé par test direct -- ArcadeDB
            # sait agréger sur EquipmentReading, seul le regroupement par
            # fenêtre temporelle lui est impossible avec cette version, d'où
            # le maintien de /graph/location-readings pour les courbes).
            params = parse_qs(parsed.query)
            gid_param = params.get("global_id", [None])[0]
            metric = params.get("metric", [None])[0]
            # epoch millis, même convention que query_readings (ArcadeDB
            # refuse les littéraux de date en chaîne sur le champ ts).
            start_param = params.get("start", [None])[0]
            end_param = params.get("end", [None])[0]
            # Facultatif : catégorie d'équipement (voir /graph/location-readings)
            # et "sum"/"avg"/"max"/"min" explicite (widget Indicateur du
            # constructeur de cockpits) -- sans ce dernier, repli sur le
            # choix automatique _aggregation_for_metric comme avant.
            category = params.get("category", [None])[0]
            aggregation_override = params.get("aggregation", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not gid_param or not metric or not zone:
                self._send_json({"error": "global_id, metric and zone query params are required"}, 400)
                return
            location_ids = sorted({g.strip() for g in gid_param.split(",") if g.strip()})
            if not location_ids:
                self._send_json({"error": "global_id and metric query params are required"}, 400)
                return

            # Voir le commentaire détaillé sur /graph/location-readings :
            # traversée du graphe sur la base DE LA ZONE, lecture
            # d'EquipmentReading sur le magasin timeseries partagé inchangé.
            repo_factory = self._arcadedb_repo_factory()
            graph_database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if graph_database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            graph_repo = repo_factory(graph_database)
            timeseries_repo = repo_factory(LEGACY_DATABASE)
            try:
                escaped_location_ids = [lid.replace("'", "\\'") for lid in location_ids]
                location_list = ", ".join(f"'{lid}'" for lid in escaped_location_ids)
                # Descend la hiérarchie spatiale (HAS_CHILD*0..) avant de
                # chercher les équipements : un Equipment n'est jamais
                # LOCATED_IN un Building directement (toujours Room ou
                # Floor). Sans ce traversal, passer le global_id d'un
                # Building ici (ex: popup Grafana du widget "Consommation
                # électrique" de BOS Exécution) renvoyait toujours 0
                # équipement. *0.. inclut le nœud lui-même (longueur 0), donc
                # le comportement pour un Room/Floor déjà utilisé ailleurs
                # est inchangé (testé : mêmes résultats qu'avant ce fix).
                category_clause = (
                    f" AND e.category = '{_cypher_escape(category)}'" if category else ""
                )
                neighbors_cy = f"""
MATCH (loc)
WHERE loc.global_id IN [{location_list}]
OPTIONAL MATCH (loc)-[:HAS_CHILD*0..]->(descendant)
WITH collect(DISTINCT loc) + collect(DISTINCT descendant) AS all_locs
UNWIND all_locs AS l
MATCH (e:Equipment)-[:LOCATED_IN]->(l)
WHERE true{category_clause}
RETURN DISTINCT e.global_id AS gid
"""
                resp = graph_repo.query_cypher(neighbors_cy)
                rows = resp.get("result") or []
                equipment_ids = sorted({row.get("gid") for row in rows if row.get("gid")})

                if not equipment_ids:
                    self._send_json({"ok": True, "equipment_count": 0, "value": None})
                    return

                sql_params: dict = {"metric": metric, "ids": equipment_ids}
                conditions = ["metric = :metric", "global_id IN :ids"]
                if start_param:
                    conditions.append("ts >= :start_ts")
                    sql_params["start_ts"] = int(start_param)
                if end_param:
                    conditions.append("ts <= :end_ts")
                    sql_params["end_ts"] = int(end_param)

                # Alias testés directement contre ArcadeDB : "bucket"/"avg"
                # sont refusés comme alias (mots réservés du parseur SQL),
                # "total"/"moy"/"maxi"/"mini"/"n" fonctionnent.
                # `global_id IN :ids` en paramètre lié -- voir le commentaire
                # détaillé sur /graph/location-readings ci-dessus (même gain
                # de latence, même requête auparavant construite en littéraux).
                sql = (
                    "SELECT SUM(value) as total, AVG(value) as moy, MAX(value) as maxi, "
                    "MIN(value) as mini, MIN(unit) as u, COUNT(*) as n FROM EquipmentReading WHERE "
                    f"{' AND '.join(conditions)}"
                )
                agg_resp = timeseries_repo.query_sql(sql, params=sql_params)
                agg_rows = agg_resp.get("result") or []
                row = agg_rows[0] if agg_rows else {}

                aggregation = (
                    aggregation_override if aggregation_override in _AGGREGATORS else _aggregation_for_metric(metric)
                )
                value = {
                    "sum": row.get("total"), "avg": row.get("moy"),
                    "max": row.get("maxi"), "min": row.get("mini"),
                }.get(aggregation)

                self._send_json({
                    "ok": True,
                    "location_count": len(location_ids),
                    "equipment_count": len(equipment_ids),
                    "aggregation": aggregation,
                    "value": value,
                    "unit": row.get("u") or "",
                    "sum": row.get("total"),
                    "avg": row.get("moy"),
                    "max": row.get("maxi"),
                    "min": row.get("mini"),
                    "sample_count": row.get("n", 0),
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/building-consumption":
            # Widget "Consommation électrique" (BOS Exécution) : vue agrégée
            # de TOUT le bâtiment + répartition par étage. Un Equipment n'est
            # jamais LOCATED_IN un Building directement (toujours Room ou
            # Floor) -- il faut donc descendre la hiérarchie spatiale
            # (HAS_CHILD Building->Floor->Room, testé directement contre
            # ArcadeDB : le chemin de longueur variable Cypher fonctionne)
            # avant de chercher les équipements, plutôt que de se contenter
            # d'un match direct sur le global_id du bâtiment qui renverrait
            # toujours 0.
            #
            # "Total" = somme de la DERNIÈRE valeur connue de chaque
            # équipement, pas un SUM/AVG sur tout l'historique brut de la
            # période : un SUM sur des dizaines de milliers d'échantillons
            # de puissance instantanée n'a aucun sens physique ("la
            # consommation du bâtiment maintenant" n'est pas la somme de
            # tous les relevés passés), et une moyenne pas plus. La dernière
            # valeur par équipement (ORDER BY ts DESC LIMIT 1, même motif que
            # query_latest côté adapter TS) est correcte à la fois pour une
            # puissance instantanée (kW, additive à un instant donné) ET
            # pour un compteur cumulatif (kWh, déjà un total glissant par
            # équipement -- sommer les dernières valeurs de chaque compteur
            # donne le total réel consommé à ce jour). Chaque requête reste
            # un ORDER BY+LIMIT natif ArcadeDB, jamais un calcul sur des
            # lignes rapatriées.
            params = parse_qs(parsed.query)
            building_gid = params.get("global_id", [None])[0]
            metric = params.get("metric", ["power_instant_kw"])[0]
            # Facultatif : limite aux équipements d'une catégorie (voir
            # /graph/location-readings) -- widget du constructeur de
            # cockpits scopé à "juste l'éclairage" par exemple.
            category = params.get("category", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not building_gid or not zone:
                self._send_json({"error": "global_id and zone query params are required"}, 400)
                return

            # Voir le commentaire détaillé sur /graph/location-readings :
            # traversée du graphe sur la base DE LA ZONE, lecture
            # d'EquipmentReading sur le magasin timeseries partagé inchangé.
            repo_factory = self._arcadedb_repo_factory()
            graph_database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if graph_database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            graph_repo = repo_factory(graph_database)
            timeseries_repo = repo_factory(LEGACY_DATABASE)
            try:
                escaped_gid = _cypher_escape(building_gid)
                category_clause = (
                    f" AND e.category = '{_cypher_escape(category)}'" if category else ""
                )
                grouping_cy = f"""
MATCH (root {{global_id: '{escaped_gid}'}})-[:HAS_CHILD]->(f:Floor)
OPTIONAL MATCH (f)-[:HAS_CHILD]->(r:Room)
WITH f, collect(DISTINCT f.global_id) + collect(DISTINCT r.global_id) AS loc_ids
MATCH (e:Equipment)-[:LOCATED_IN]->(l)
WHERE l.global_id IN loc_ids{category_clause}
RETURN f.global_id AS floor_id, f.name AS floor_name, collect(DISTINCT e.global_id) AS equip_ids
"""
                grouping_resp = graph_repo.query_cypher(grouping_cy)
                floor_rows = grouping_resp.get("result") or []

                # Une requête PAR équipement (une par étage, N appels) était
                # correcte mais beaucoup trop lente (~17s mesurés sur 54
                # équipements dont 52 sans donnée pour ce métrique) -- ArcadeDB
                # n'a pas de fonction fenêtrée pour un "dernier par groupe"
                # natif, donc à la place : UNE SEULE requête pour tout le
                # bâtiment (les lignes les plus récentes, tous équipements
                # confondus), réduite en Python à "la première occurrence par
                # global_id" (le tri ORDER BY ts DESC garantit que la première
                # occurrence rencontrée EST la plus récente). La marge
                # (equipment_count * 50, plafonnée) fait qu'un équipement qui
                # publie nettement moins souvent que les autres a quand même
                # de très bonnes chances d'apparaître dans la fenêtre -- ce
                # n'est plus un calcul ArcadeDB pur, mais c'est une sélection
                # triviale (pas une ré-agrégation), et le volume transféré
                # reste borné, contrairement à un rapatriement de l'historique
                # complet.
                all_equip_ids = sorted({eid for row in floor_rows for eid in (row.get("equip_ids") or [])})
                latest_by_equipment: dict[str, dict] = {}
                if all_equip_ids:
                    fetch_limit = min(5000, max(200, len(all_equip_ids) * 50))
                    # `global_id IN :ids` en paramètre lié -- voir le
                    # commentaire détaillé sur /graph/location-readings : un
                    # bâtiment de plusieurs centaines d'équipements avec cette
                    # liste interpolée en littéraux dépassait régulièrement le
                    # timeout de la gateway (10s), constaté sur ce widget
                    # précis ("Nouvelles barres" à l'échelle Bâtiment).
                    # Fenêtre bornée : sur la table partagée (plusieurs millions
                    # de lignes), `metric + IN + ORDER BY ts DESC` sans borne
                    # de temps dépassait le timeout gateway (504 sur
                    # state/temperature). Le dernier ts de la métrique est
                    # obtenu instantanément (ORDER BY ts DESC LIMIT 1) ; on
                    # ne lit ensuite que le dernier jour AVANT ce point
                    # (relatif à la donnée, pas à "maintenant" : le backfill
                    # est un instantané qui peut dater de plusieurs jours).
                    window_clause = ""
                    sql_params = {"metric": metric, "ids": all_equip_ids}
                    newest = timeseries_repo.query_sql(
                        "SELECT ts FROM EquipmentReading WHERE metric = :metric ORDER BY ts DESC LIMIT 1",
                        params={"metric": metric},
                    ).get("result") or []
                    if newest and newest[0].get("ts"):
                        try:
                            newest_dt = datetime.strptime(str(newest[0]["ts"])[:23], "%Y-%m-%d %H:%M:%S.%f")
                        except ValueError:
                            newest_dt = datetime.strptime(str(newest[0]["ts"])[:19], "%Y-%m-%d %H:%M:%S")
                        window_start_ms = int(newest_dt.replace(tzinfo=timezone.utc).timestamp() * 1000) - 24 * 3600 * 1000
                        window_clause = " AND ts >= :window_start"
                        sql_params["window_start"] = window_start_ms
                    sql = (
                        "SELECT global_id, value, unit FROM EquipmentReading WHERE metric = :metric "
                        f"AND global_id IN :ids{window_clause} ORDER BY ts DESC LIMIT {fetch_limit}"
                    )
                    recent_rows = timeseries_repo.query_sql(sql, params=sql_params).get("result") or []
                    for row in recent_rows:
                        gid = row.get("global_id")
                        if gid and gid not in latest_by_equipment:
                            latest_by_equipment[gid] = row

                by_floor = []
                total = 0.0
                unit = ""
                for floor_row in floor_rows:
                    equip_ids = floor_row.get("equip_ids") or []
                    if not equip_ids:
                        continue

                    floor_value = 0.0
                    equipment_with_data = 0
                    for eid in equip_ids:
                        latest = latest_by_equipment.get(eid)
                        if not latest:
                            continue
                        floor_value += latest.get("value") or 0.0
                        unit = unit or latest.get("unit") or ""
                        equipment_with_data += 1

                    if equipment_with_data == 0:
                        continue

                    total += floor_value
                    by_floor.append({
                        "floor_id": floor_row.get("floor_id"),
                        "floor_name": floor_row.get("floor_name"),
                        "value": round(floor_value, 2),
                        "equipment_count": len(equip_ids),
                        "equipment_with_data": equipment_with_data,
                    })

                by_floor.sort(key=lambda item: item["floor_name"] or "")

                self._send_json({
                    "ok": True,
                    "building_id": building_gid,
                    "metric": metric,
                    "unit": unit,
                    "total": round(total, 2),
                    "by_floor": by_floor,
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/equipment-scope":
            # Colonne "Données disponibles" du constructeur de cockpits
            # (BOS Exécution) : catégories d'équipement RÉELLEMENT présentes
            # sous un périmètre spatial donné (Building/Floor/Room, ou une
            # liste de Room pour une typologie) -- jamais une liste statique.
            # Même traversal HAS_CHILD*0.. que /graph/location-readings.
            params = parse_qs(parsed.query)
            gid_param = params.get("global_id", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not gid_param or not zone:
                self._send_json({"error": "global_id and zone query params are required"}, 400)
                return
            location_ids = sorted({g.strip() for g in gid_param.split(",") if g.strip()})
            if not location_ids:
                self._send_json({"error": "global_id query param is required"}, 400)
                return

            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)
            try:
                location_list = ", ".join(f"'{_cypher_escape(lid)}'" for lid in location_ids)
                # DISTINCT sur global_id, PAS un count(*) direct par
                # (catégorie, type) : HAS_CHILD*0.. inclut déjà le nœud de
                # départ lui-même (longueur de chemin 0), donc concaténer
                # `collect(DISTINCT loc) + collect(DISTINCT descendant)`
                # comptait CE nœud une deuxième fois (une fois via chaque
                # collect) -- tout équipement rattaché DIRECTEMENT au
                # périmètre demandé (pas à un descendant) se retrouvait
                # compté deux fois (confirmé : 82 au lieu de 41 sur un étage
                # réel). On agrège nous-mêmes en Python après déduplication
                # par global_id, plutôt que de faire confiance à un count(*)
                # Cypher sur des lignes potentiellement dupliquées.
                cy = f"""
MATCH (loc)
WHERE loc.global_id IN [{location_list}]
OPTIONAL MATCH (loc)-[:HAS_CHILD*0..]->(descendant)
WITH collect(DISTINCT descendant) AS all_locs
UNWIND all_locs AS l
MATCH (e:Equipment)-[:LOCATED_IN]->(l)
RETURN DISTINCT e.global_id AS global_id, e.category AS category, e.equipment_type AS equipment_type
"""
                rows = repo.query_cypher(cy).get("result") or []
                by_category: dict[str, dict] = {}
                for row in rows:
                    cat = row.get("category") or "_unknown"
                    equipment_type = row.get("equipment_type")
                    entry = by_category.setdefault(
                        cat, {"category": cat, "equipment_count": 0, "_type_counts": {}}
                    )
                    entry["equipment_count"] += 1
                    entry["_type_counts"][equipment_type] = entry["_type_counts"].get(equipment_type, 0) + 1
                # Métriques RÉELLEMENT simulées pour cette catégorie -- sans
                # ça, le constructeur de cockpits n'avait aucun moyen de
                # savoir que "power_instant_kw" (métrique par défaut d'un
                # nouveau widget) n'a aucune donnée pour un bâtiment donné
                # (ex: équipements électriques génériques sans capteur), ce
                # qui affichait silencieusement "0" partout. Union des
                # métriques du catalogue de simulation à travers tous les
                # equipment_type réellement présents dans la catégorie.
                catalog = self._build_equipment_catalog()
                categories = []
                for entry in sorted(by_category.values(), key=lambda c: c["category"]):
                    type_counts = entry.pop("_type_counts")
                    entry["equipment_types"] = [
                        {"equipment_type": t, "count": n} for t, n in sorted(type_counts.items())
                    ]
                    metrics_by_name: dict[str, str] = {}
                    for equipment_type in type_counts:
                        try:
                            spec = catalog.get_generator_spec(equipment_type)
                        except KeyError:
                            continue
                        for metric in spec.metrics:
                            metrics_by_name.setdefault(metric.name, metric.unit)
                    entry["metrics"] = [
                        {"name": name, "unit": unit} for name, unit in sorted(metrics_by_name.items())
                    ]
                    categories.append(entry)
                self._send_json({"ok": True, "categories": categories})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/equipment-list":
            # Variante "instances" de /graph/equipment-scope ci-dessus (qui
            # n'agrège que des comptes) -- alimente le sélecteur "cibler un
            # équipement précis" du widget Indicateur (constructeur de
            # cockpits, BOS Exécution). Même traversal HAS_CHILD*0..,
            # mêmes garde-fous sur global_id.
            params = parse_qs(parsed.query)
            gid_param = params.get("global_id", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not gid_param or not zone:
                self._send_json({"error": "global_id and zone query params are required"}, 400)
                return
            location_ids = sorted({g.strip() for g in gid_param.split(",") if g.strip()})
            if not location_ids:
                self._send_json({"error": "global_id query param is required"}, 400)
                return
            # Facultatif : relève le plafond par défaut (500, pensé pour le
            # sélecteur "cibler un équipement précis" de l'UI) -- utilisé en
            # interne par la websocket /ws/widget-aggregate de la gateway
            # pour résoudre TOUT l'ensemble d'équipements d'un widget scopé à
            # un bâtiment entier, où 500 tronquerait silencieusement la
            # correspondance NATS -> recalcul (voir aggregation_bridge.py).
            limit = int(params.get("limit", ["500"])[0])

            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)
            try:
                location_list = ", ".join(f"'{_cypher_escape(lid)}'" for lid in location_ids)
                # collect(DISTINCT descendant) seul suffit : HAS_CHILD*0..
                # inclut déjà le nœud de départ (longueur 0) -- voir le
                # commentaire détaillé sur /graph/equipment-scope ci-dessus
                # pour le bug que la version "+ collect(DISTINCT loc)"
                # provoquait (équipement compté deux fois). RETURN DISTINCT
                # en plus, en filet de sécurité.
                cy = f"""
MATCH (loc)
WHERE loc.global_id IN [{location_list}]
OPTIONAL MATCH (loc)-[:HAS_CHILD*0..]->(descendant)
WITH collect(DISTINCT descendant) AS all_locs
UNWIND all_locs AS l
MATCH (e:Equipment)-[:LOCATED_IN]->(l)
RETURN DISTINCT e.global_id AS global_id, e.name AS name, e.equipment_type AS equipment_type, e.category AS category
ORDER BY e.name
LIMIT {limit}
"""
                equipment = repo.query_cypher(cy).get("result") or []
                self._send_json({"ok": True, "equipment": equipment})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/typologies":
            # Niveau "Type de pièce" du sélecteur Espace (constructeur de
            # cockpits) : un nœud Typology existe PAR ÉTAGE
            # (global_id = typology::<floor_id>::<nom>), donc "les bureaux du
            # bâtiment" n'est PAS un seul nœud -- on unionne par le nom
            # (identique entre étages pour une même classification, voir
            # _resolve_room_typology) à travers tous les étages du bâtiment.
            params = parse_qs(parsed.query)
            building_gid = params.get("global_id", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not building_gid or not zone:
                self._send_json({"error": "global_id and zone query params are required"}, 400)
                return

            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)
            try:
                escaped_gid = _cypher_escape(building_gid)
                cy = f"""
MATCH (root {{global_id: '{escaped_gid}'}})-[:HAS_CHILD]->(f:Floor)-[:HAS_GROUP]->(t:Typology)-[:HAS_MEMBER]->(r:Room)
RETURN t.name AS name, count(DISTINCT r) AS room_count, count(DISTINCT f) AS floor_count
"""
                rows = repo.query_cypher(cy).get("result") or []
                typologies = sorted(rows, key=lambda r: r.get("name") or "")
                self._send_json({"ok": True, "typologies": typologies})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/rooms-by-typology":
            # Résout un nom de typologie en Room concrets à travers tout le
            # bâtiment -- le frontend passe ensuite ces room_id comme
            # space.ids (liste) aux endpoints de lecture (location-readings,
            # location-readings-summary), qui acceptent déjà une liste.
            params = parse_qs(parsed.query)
            building_gid = params.get("global_id", [None])[0]
            name = params.get("name", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not building_gid or not name or not zone:
                self._send_json({"error": "global_id, name and zone query params are required"}, 400)
                return

            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)
            try:
                escaped_gid = _cypher_escape(building_gid)
                escaped_name = _cypher_escape(name)
                cy = f"""
MATCH (root {{global_id: '{escaped_gid}'}})-[:HAS_CHILD]->(f:Floor)-[:HAS_GROUP]->(t:Typology {{name: '{escaped_name}'}})-[:HAS_MEMBER]->(r:Room)
RETURN r.global_id AS room_id, r.name AS room_name, f.global_id AS floor_id, f.name AS floor_name
"""
                rows = repo.query_cypher(cy).get("result") or []
                self._send_json({"ok": True, "rooms": rows})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/rooms-by-floor":
            # Niveau "Pièce spécifique" du sélecteur Espace (constructeur de
            # cockpits) : liste plate des pièces d'UN étage, même forme que
            # /graph/rooms-by-typology (évite de rapatrier/parser la
            # structure imbriquée plus lourde de /graph/view).
            params = parse_qs(parsed.query)
            floor_gid = params.get("global_id", [None])[0]
            zone = (params.get("zone", [None])[0] or "").strip()
            if not floor_gid or not zone:
                self._send_json({"error": "global_id and zone query params are required"}, 400)
                return

            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            repo = repo_factory(database)
            try:
                escaped_gid = _cypher_escape(floor_gid)
                cy = f"""
MATCH (f:Floor {{global_id: '{escaped_gid}'}})-[:HAS_CHILD]->(r:Room)
RETURN r.global_id AS room_id, r.name AS room_name, f.global_id AS floor_id, f.name AS floor_name
"""
                rows = repo.query_cypher(cy).get("result") or []
                self._send_json({"ok": True, "rooms": rows})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/occupancy-dashboard":
            # Cockpit "Compréhension du taux d'usage des espaces" -- un seul
            # payload pour les 7 widgets. `start`/`end` (epoch ms) bornent la
            # période principale, `compare_start`/`compare_end` (optionnels)
            # une seconde période superposée sur les barres ("Comparer 2
            # périodes"). Bornage calendaire calculé CÔTÉ FRONTEND (comme
            # /graph/location-readings), pas ici -- évite de dupliquer la
            # logique déjà en place dans features/cockpits/utils/period.ts.
            params = parse_qs(parsed.query)
            zone = (params.get("zone", [None])[0] or "").strip()
            etage = (params.get("etage", [None])[0] or "").strip()
            type_espace = (params.get("type_espace", [None])[0] or "").strip()
            plage = (params.get("plage", ["7-19"])[0] or "7-19")
            seuil_bas = float(params.get("seuil_bas", ["20"])[0])
            seuil_haut = float(params.get("seuil_haut", ["80"])[0])
            start_param = params.get("start", [None])[0]
            end_param = params.get("end", [None])[0]
            compare_start_param = params.get("compare_start", [None])[0]
            compare_end_param = params.get("compare_end", [None])[0]
            raw_limit = int(params.get("raw_limit", ["50000"])[0])
            if not zone or not start_param or not end_param:
                self._send_json({"error": "zone, start and end query params are required"}, 400)
                return

            repo_factory = self._arcadedb_repo_factory()
            # for_write=True : le premier appel sur une zone avec de vraies
            # Room mais aucun capteur simulé doit pouvoir les provisionner.
            database = resolve_database_for_zone(repo_factory, zone, for_write=True)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            if database != LEGACY_DATABASE:
                repo_factory(database).ensure_database()
            graph_repo = repo_factory(database)

            try:
                provisioning = OccupancyProvisioningService(
                    graph_repository=graph_repo,
                    simulation_service=self._build_equipment_simulation_service(),
                    zone=zone,
                )
                provision_result = provisioning.provision_zone_if_needed()
                if not provision_result["has_rooms"]:
                    self._send_json({"ok": True, "zone": zone, "has_rooms": False})
                    return

                escaped_zone = _cypher_escape(zone)
                floor_clause = ""
                if etage:
                    floor_clause = (
                        f"MATCH (f:Floor {{global_id: '{_cypher_escape(etage)}', zone: '{escaped_zone}'}})"
                        "-[:HAS_CHILD]->(r) "
                    )
                # `WHERE true{type_clause}` (pas `r.space_type IS NOT NULL`) :
                # une pièce sans type d'espace déterminé (son nom ne
                # correspond à aucun mot-clé bureau -- voir
                # occupancy_provisioning_service._assign_space_type) doit
                # rester incluse dans "Tout type d'espace", exclue
                # uniquement si l'utilisateur filtre sur un type précis.
                type_clause = f" AND r.space_type = '{_cypher_escape(type_espace)}'" if type_espace else ""
                sensors_cy = f"""
{floor_clause if floor_clause else ""}MATCH (r:Room {{zone: '{escaped_zone}'}})
WHERE true{type_clause}
MATCH (e:Equipment)-[:LOCATED_IN]->(r)
WHERE e.equipment_type = 'detecteur_presence' AND e.simulated = true
RETURN DISTINCT e.global_id AS sensor_id, r.global_id AS room_id, r.name AS room_name, r.space_type AS space_type
"""
                sensor_rows = graph_repo.query_cypher(sensors_cy).get("result") or []
                sensor_ids = sorted({row["sensor_id"] for row in sensor_rows if row.get("sensor_id")})
                room_by_sensor = {row["sensor_id"]: row["room_id"] for row in sensor_rows if row.get("sensor_id")}

                if not sensor_ids:
                    self._send_json({"ok": True, "zone": zone, "has_rooms": True, "room_count": 0})
                    return

                timeseries_repo = repo_factory(LEGACY_DATABASE)

                def _fetch(metric: str, start_ms: int, end_ms: int) -> list[dict]:
                    sql = (
                        "SELECT ts, value, global_id FROM EquipmentReading WHERE "
                        "metric = :metric AND global_id IN :ids AND ts >= :start_ts AND ts <= :end_ts "
                        f"ORDER BY ts DESC LIMIT {raw_limit}"
                    )
                    sql_params = {
                        "metric": metric, "ids": sensor_ids,
                        "start_ts": int(start_ms), "end_ts": int(end_ms),
                    }
                    return timeseries_repo.query_sql(sql, params=sql_params).get("result") or []

                start_ms, end_ms = int(start_param), int(end_param)
                occ_rows = filter_by_plage(_fetch("occupancy", start_ms, end_ms), plage)
                res_rows = filter_by_plage(_fetch("reservation", start_ms, end_ms), plage)

                bars_series_a = bucket_daily_occupancy_pct(occ_rows)
                bars_series_b = None
                if compare_start_param and compare_end_param:
                    compare_occ_rows = filter_by_plage(
                        _fetch("occupancy", int(compare_start_param), int(compare_end_param)), plage,
                    )
                    bars_series_b = bucket_daily_occupancy_pct(compare_occ_rows)

                now = datetime.now()
                ytd_start_ms = int(datetime(now.year, 1, 1).timestamp() * 1000)
                ytd_rows = filter_by_plage(_fetch("occupancy", ytd_start_ms, end_ms), plage)
                ytd_donut = compute_donut(ytd_rows)

                per_room_occ_pct = {
                    room_by_sensor[gid]: pct for gid, pct in per_sensor_occupancy_pct(occ_rows).items()
                }
                per_room_res_pct = {
                    room_by_sensor[gid]: pct for gid, pct in per_sensor_occupancy_pct(res_rows).items()
                }

                self._send_json({
                    "ok": True,
                    "zone": zone,
                    "has_rooms": True,
                    "room_count": provision_result["room_count"],
                    "bars": {"buckets": [p["label"] for p in bars_series_a], "series_a": [p["count"] for p in bars_series_a], "series_b": [p["count"] for p in bars_series_b] if bars_series_b else None},
                    "donut": compute_donut(occ_rows),
                    "reservation_rate": compute_reservation_rate(occ_rows, res_rows),
                    "monthly_avg_since_year_start": ytd_donut["occupied_pct"],
                    "threshold_counts": classify_threshold_counts(
                        per_room_occ_pct, per_room_res_pct, seuil_bas=seuil_bas, seuil_haut=seuil_haut
                    ),
                    "spaces": [
                        {
                            "room_id": row["room_id"], "room_name": row.get("room_name"),
                            "space_type": row.get("space_type"),
                            "occupancy_pct": per_room_occ_pct.get(row["room_id"]),
                            "reservation_pct": per_room_res_pct.get(row["room_id"]),
                        }
                        for row in sensor_rows
                    ],
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/energy-consumption-dashboard":
            # Cockpit FIXE "Consommation énergétique" (BOS Exécution) : un
            # seul payload pour le graphique en barres empilées par
            # sous-catégorie (Éclairage/Stores/MCR-CVC/IT/Cuisine/Ascenseurs)
            # + les 3 KPI. Contrairement à /graph/building-consumption (widget
            # du constructeur générique, scopé à un Building via
            # HAS_CHILD*0..), `zone` est une propriété DIRECTE du nœud
            # Equipment (voir _upsert_node_query) -- pas besoin de traverser
            # Building->Floor->Room pour le retrouver, ce cockpit n'a pas de
            # notion d'étage/pièce dans son périmètre (spec client).
            params = parse_qs(parsed.query)
            zone = (params.get("zone", [None])[0] or "").strip()
            start_param = params.get("start", [None])[0]
            end_param = params.get("end", [None])[0]
            compare_start_param = params.get("compare_start", [None])[0]
            compare_end_param = params.get("compare_end", [None])[0]
            plage = params.get("plage", ["toute_journee"])[0] or "toute_journee"
            type_consommation = (params.get("type_consommation", [""])[0] or "").strip()
            sous_categorie = (params.get("sous_categorie", [""])[0] or "").strip()
            raw_limit = int(params.get("raw_limit", ["50000"])[0])
            if not zone or not start_param or not end_param:
                self._send_json({"error": "zone, start and end query params are required"}, 400)
                return

            catalog = self._build_equipment_catalog()
            energy_type_filter = None if type_consommation in ("", "total") else type_consommation
            equipment_types = catalog.list_energy_equipment_types(
                subcategory=sous_categorie or None, energy_type=energy_type_filter,
            )
            if not equipment_types:
                # Combinaison de filtres valide mais qui ne matche aucun
                # equipment_type taggé (ex: sous_categorie=eclairage +
                # type_consommation=thermique, un luminaire n'a pas de
                # variante thermique) -- honnête plutôt qu'une 500.
                self._send_json({"ok": True, "zone": zone, "has_equipment": False})
                return

            repo_factory = self._arcadedb_repo_factory()
            database = resolve_database_for_zone(repo_factory, zone, for_write=False)
            if database is None:
                self._send_json({"error": "unknown zone", "zone": zone}, 404)
                return
            graph_repo = repo_factory(database)

            try:
                escaped_zone = _cypher_escape(zone)
                types_literal = ", ".join(f"'{_cypher_escape(t)}'" for t in equipment_types)
                equip_cy = f"""
MATCH (e:Equipment {{zone: '{escaped_zone}'}})
WHERE e.equipment_type IN [{types_literal}]
RETURN e.global_id AS global_id, e.equipment_type AS equipment_type
"""
                equip_rows = graph_repo.query_cypher(equip_cy).get("result") or []
                if not equip_rows:
                    # Equipment_type valides pour ce filtre, mais AUCUNE
                    # instance dans cette zone (ex: pas de chaudière fioul
                    # dans ce bâtiment) -- état vide honnête, jamais de
                    # repli fabriqué.
                    self._send_json({"ok": True, "zone": zone, "has_equipment": False})
                    return

                subcategory_by_gid: dict[str, str] = {}
                equipment_ids: list[str] = []
                for row in equip_rows:
                    gid = row.get("global_id")
                    if not gid:
                        continue
                    spec = catalog.get_generator_spec(row.get("equipment_type"))
                    if spec.energy_subcategory is None:
                        continue
                    subcategory_by_gid[gid] = spec.energy_subcategory
                    equipment_ids.append(gid)

                timeseries_repo = repo_factory(LEGACY_DATABASE)

                def _fetch(start_ms: int, end_ms: int) -> list[dict]:
                    sql = (
                        "SELECT ts, value, global_id FROM EquipmentReading WHERE "
                        "metric = :metric AND global_id IN :ids AND ts >= :start_ts AND ts <= :end_ts "
                        f"ORDER BY ts DESC LIMIT {raw_limit}"
                    )
                    sql_params = {
                        "metric": "power_instant_kw", "ids": equipment_ids,
                        "start_ts": int(start_ms), "end_ts": int(end_ms),
                    }
                    return timeseries_repo.query_sql(sql, params=sql_params).get("result") or []

                start_ms, end_ms = int(start_param), int(end_param)
                current_rows = energy_filter_by_plage(_fetch(start_ms, end_ms), plage)
                current_daily = bucket_daily_by_subcategory(current_rows, subcategory_by_gid, plage=plage)

                compare_totals = None
                compare_pct = None
                if compare_start_param and compare_end_param:
                    compare_rows = energy_filter_by_plage(
                        _fetch(int(compare_start_param), int(compare_end_param)), plage,
                    )
                    compare_daily = bucket_daily_by_subcategory(compare_rows, subcategory_by_gid, plage=plage)
                    compare_totals = month_total_by_subcategory(compare_daily)

                current_totals = month_total_by_subcategory(current_daily)
                current_total_kwh = round(sum(current_totals.values()), 2)
                if compare_totals is not None:
                    compare_total_kwh = round(sum(compare_totals.values()), 2)
                    compare_pct = (
                        round(100 * (current_total_kwh - compare_total_kwh) / compare_total_kwh, 1)
                        if compare_total_kwh else None
                    )
                else:
                    compare_total_kwh = None

                # "Valeur temps réel" : TOUJOURS le dernier relevé connu,
                # indépendant du mois navigué et de la plage horaire -- une
                # jauge "maintenant", pas une valeur du mois affiché. Même
                # technique de fenêtre bornée que /graph/building-consumption
                # (dernier ts global, puis 24h avant CE point, jamais "now"
                # littéral qui pourrait dépasser l'historique simulé).
                newest = timeseries_repo.query_sql(
                    "SELECT ts FROM EquipmentReading WHERE metric = :metric ORDER BY ts DESC LIMIT 1",
                    params={"metric": "power_instant_kw"},
                ).get("result") or []
                realtime_kw = 0.0
                if newest and newest[0].get("ts"):
                    try:
                        newest_dt = datetime.strptime(str(newest[0]["ts"])[:23], "%Y-%m-%d %H:%M:%S.%f")
                    except ValueError:
                        newest_dt = datetime.strptime(str(newest[0]["ts"])[:19], "%Y-%m-%d %H:%M:%S")
                    window_start_ms = int(newest_dt.replace(tzinfo=timezone.utc).timestamp() * 1000) - 24 * 3600 * 1000
                    recent_sql = (
                        "SELECT global_id, value FROM EquipmentReading WHERE metric = :metric "
                        f"AND global_id IN :ids AND ts >= :window_start ORDER BY ts DESC LIMIT {min(5000, max(200, len(equipment_ids) * 50))}"
                    )
                    recent_rows = timeseries_repo.query_sql(recent_sql, params={
                        "metric": "power_instant_kw", "ids": equipment_ids,
                        "window_start": window_start_ms,
                    }).get("result") or []
                    latest_by_equipment: dict[str, float] = {}
                    for row in recent_rows:
                        gid = row.get("global_id")
                        if gid and gid not in latest_by_equipment:
                            latest_by_equipment[gid] = row.get("value") or 0.0
                    realtime_kw = round(sum(latest_by_equipment.values()), 2)

                day_labels = sorted(current_daily.keys())
                subcategories = sorted(set(subcategory_by_gid.values()))
                dense = densify_daily_subcategories(current_daily, subcategories, day_labels)

                self._send_json({
                    "ok": True,
                    "zone": zone,
                    "has_equipment": True,
                    "bars": {
                        "days": day_labels,
                        "subcategories": subcategories,
                        "series": {subcat: [dense[d][subcat] for d in day_labels] for subcat in subcategories},
                    },
                    "kpi_realtime_kw": realtime_kw,
                    "kpi_current_month_kwh": current_total_kwh,
                    "kpi_previous_month_kwh": compare_total_kwh,
                    "kpi_variation_pct": compare_pct,
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/zones":
            # Liste les zones connues -- remplace le champ texte libre de
            # zone côté frontend (BimExplorer.tsx/BimViewer.tsx) par un vrai
            # menu déroulant, pour éviter les zones fantômes créées par une
            # faute de frappe (cause directe du bug de fuite cross-zone
            # traité plus tôt cette session). Union de deux sources : les
            # zones encore dans la base legacy partagée (ingérées avant
            # l'isolation par zone), et celles qui ont leur propre base
            # `bim_<slug>_<hash>`. Le nom de base n'étant PAS réversible vers
            # le nom de zone d'origine (slug + hash), on relit la vraie
            # valeur stockée sur le nœud Building de chaque base plutôt que
            # de tenter de la reconstruire.
            repo_factory = self._arcadedb_repo_factory()
            legacy_repo = repo_factory(LEGACY_DATABASE)
            zones: set[str] = set()
            try:
                rows = legacy_repo.query_sql("SELECT DISTINCT zone FROM Building").get("result") or []
                zones.update(r["zone"] for r in rows if r.get("zone"))
            except Exception:
                pass  # base legacy vide/injoignable : pas fatal pour la liste
            try:
                for db_name in legacy_repo.list_databases():
                    if db_name == LEGACY_DATABASE or not db_name.startswith("bim_"):
                        continue
                    try:
                        zone_repo = repo_factory(db_name)
                        rows = zone_repo.query_sql("SELECT DISTINCT zone FROM Building LIMIT 1").get("result") or []
                        zones.update(r["zone"] for r in rows if r.get("zone"))
                    except Exception:
                        continue
            except Exception:
                pass
            self._send_json({"ok": True, "zones": sorted(zones)})
            return
        self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/graph/grafana-sync":
                # Recale le dashboard Grafana (variables Pièce/Étage + Type
                # de donnée + paramètre `zone` des requêtes) sur UNE zone à
                # la demande -- appelé par l'onglet Supervision du
                # Configurateur quand on change de zone. Avant, cette synchro
                # n'avait lieu qu'à l'INGESTION, donc Grafana restait figé sur
                # la dernière zone ingérée quelle que soit la zone affichée.
                params = parse_qs(parsed.query)
                zone = (params.get("zone", [None])[0] or "").strip()
                if not zone:
                    self._send_json({"error": "zone query param is required"}, 400)
                    return
                repo_factory = self._arcadedb_repo_factory()
                database = resolve_database_for_zone(repo_factory, zone, for_write=False)
                if database is None:
                    self._send_json({"error": "unknown zone", "zone": zone}, 404)
                    return
                try:
                    dashboard_sync = self._build_dashboard_sync(database)
                    if dashboard_sync is None:
                        self._send_json({"ok": True, "zone": zone, "synced": False})
                        return
                    dashboard_sync.sync_variables(zone=zone)
                    self._send_json({"ok": True, "zone": zone, "synced": True})
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 500)
                return

            if parsed.path == "/validate":
                self._handle_validate_batch()
                return

            if parsed.path == self.END_TO_END_BATCH_ROUTE:
                self._handle_ingest_batch()
                return

            self._send_json({"error": "Not found"}, 404)
        except Exception as e:
            self._send_json({"error": f"Server error: {str(e)}"}, 500)

    def _parse_json_request(self) -> tuple[dict | None, str | None]:
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)

        if not content_type.startswith("application/json"):
            return None, "Content-Type must be application/json"

        try:
            return json.loads(body.decode("utf-8")), None
        except Exception as exc:
            return None, f"Invalid JSON: {exc}"

    def _handle_validate_batch(self) -> None:
        payload, error = self._parse_json_request()
        if error:
            self._send_json({"error": error}, 400)
            return

        assert payload is not None
        discipline = (payload.get("discipline") or "").strip().upper()
        files = payload.get("files", [])

        if not discipline or not files:
            self._send_json({"error": "discipline and files are required"}, 400)
            return

        if not isinstance(files, list):
            self._send_json({"error": "files must be an array"}, 400)
            return

        results = []
        for file_info in files:
            if not isinstance(file_info, dict) or "name" not in file_info or "content" not in file_info:
                results.append({
                    "filename": file_info.get("name", "unknown"),
                    "result": {"error": "File must have name and content fields (content should be base64)"},
                })
                continue

            result = self._validate_single_file([discipline], file_info)
            results.append({
                "filename": file_info["name"],
                "result": result,
            })

        self._send_json({
            "route": "/validate",
            "batch": len(files),
            "results": results,
        })

    def _handle_ingest_batch(self) -> None:
        """Un seul appel peut mélanger plusieurs disciplines (ARCHI + CVC + ELEC...)
        pour le même bâtiment : chaque fichier porte SA PROPRE discipline, zone
        reste commun à tout le lot (même bâtiment). C'est le scénario réel client :
        upload en une fois de toutes les maquettes disponibles pour peupler le
        graphe et donner du contexte, sans savoir/imposer un ordre précis."""
        payload, error = self._parse_json_request()
        if error:
            self._send_json({"error": error}, 400)
            return

        assert payload is not None
        zone = (payload.get("zone") or "").strip()
        files = payload.get("files", [])

        if not zone or not files:
            self._send_json({"error": "zone and files are required"}, 400)
            return

        if not isinstance(files, list):
            self._send_json({"error": "files must be an array"}, 400)
            return

        # Résolue/créée UNE FOIS pour tout le batch (isolation par zone,
        # Phase 1) -- pas par fichier : tous les fichiers d'un même appel
        # partagent une seule `zone` (voir docstring), donc une seule base.
        # Une zone déjà connue (créée par un ingest précédent, ou héritée de
        # la base legacy partagée) réutilise sa base existante ; une zone
        # neuve obtient sa propre base, créée ici avant le premier fichier.
        repo_factory = self._arcadedb_repo_factory()
        database = resolve_database_for_zone(repo_factory, zone, for_write=True)
        if database != LEGACY_DATABASE:
            repo_factory(database).ensure_database()

        valid_entries: list[tuple[list[str], dict]] = []
        invalid_results = []
        for file_info in files:
            raw_disciplines = file_info.get("disciplines") if isinstance(file_info, dict) else None
            disciplines = (
                [str(d).strip().upper() for d in raw_disciplines if str(d).strip()]
                if isinstance(raw_disciplines, list)
                else []
            )
            if not isinstance(file_info, dict) or "name" not in file_info or "content" not in file_info or not disciplines:
                invalid_results.append({
                    "filename": file_info.get("name", "unknown") if isinstance(file_info, dict) else "unknown",
                    "result": {"error": "File must have name, content (base64) and a non-empty disciplines array"},
                })
                continue
            valid_entries.append((disciplines, file_info))

        # Les maquettes backbone (ARCHI...) doivent être ingérées AVANT les
        # maquettes équipement (CVC, ELEC...), sinon le rattachement LOCATED_IN
        # ne trouve encore aucun Room/Floor pour cette zone. On trie ici plutôt
        # que d'imposer un ordre d'upload au client. Une maquette avec
        # PLUSIEURS disciplines (ex: CVC+STRUCTURE) est traitée comme backbone
        # dès qu'UNE SEULE de ses disciplines assignées l'est.
        rules_repo = self._build_rules_repository()

        def backbone_first(entry: tuple[list[str], dict]) -> int:
            disciplines, _ = entry
            try:
                canonical = rules_repo.canonical_disciplines(disciplines)
                return 0 if rules_repo.is_spatial_backbone_disciplines(canonical) else 1
            except KeyError:
                return 1

        valid_entries.sort(key=backbone_first)

        results = []
        for disciplines, file_info in valid_entries:
            result = self._validate_store_and_ingest_graph(disciplines, zone, file_info, database)
            results.append({
                "filename": file_info["name"],
                "result": result,
            })
        results.extend(invalid_results)

        self._send_json({
            "route": self.END_TO_END_BATCH_ROUTE,
            "batch": len(files),
            "results": results,
        })

    def _validate_store_and_ingest_graph(
        self, disciplines: list[str], zone: str, file_info: dict, database: str,
    ) -> dict:
        """Validation + stockage SeaweedFS + ingestion ArcadeDB en un seul flux.

        `database` : base graphe déjà résolue/créée une fois pour toute la
        zone par `_handle_ingest_batch` (isolation par zone, Phase 1)."""
        file_payload = self._validate_single_file(disciplines, file_info)

        if file_payload.get("error"):
            return {
                "validation_storage": file_payload,
                "graph_ingestion": {"status": "failed", "message": "Validation/storage step failed."},
            }

        if not file_payload.get("stored") or not file_payload.get("storage_url"):
            return {
                "validation_storage": file_payload,
                "graph_ingestion": {
                    "status": "skipped",
                    "message": "Model is not conformant or not stored in SeaweedFS.",
                },
            }

        graph_service = self._build_graph_ingestion_service(database)
        graph_result = graph_service.ingest_from_storage(
            object_key=file_payload["storage_url"],
            disciplines=disciplines,
            zone=zone,
            run_validation=False,
        )

        if graph_result.status == "ingested":
            # Best-effort : un souci Grafana (indisponible, credentials absents)
            # ne doit jamais faire échouer l'ingestion elle-même, seulement
            # laisser le cockpit un peu en retard sur le graphe.
            try:
                dashboard_sync = self._build_dashboard_sync(database)
                if dashboard_sync is not None:
                    dashboard_sync.sync_variables(zone=zone)
            except Exception as exc:
                print(f"Warning: Grafana dashboard sync failed: {exc}", file=sys.stderr)

        return {
            "validation_storage": file_payload,
            "graph_ingestion": serialize_graph_result(graph_result),
        }

    def _build_dashboard_sync(self, database: str) -> GrafanaDashboardSyncAdapter | None:
        """Resynchronise après chaque ingestion réussie les variables
        "Pièce/Étage" ET "Type de donnée" du cockpit Grafana, pour qu'elles
        reflètent l'état courant d'ArcadeDB/du catalogue plutôt qu'un
        instantané figé au moment de la création du dashboard.

        `database` : base graphe déjà résolue pour la zone de cet ingestion
        (voir _handle_ingest_batch) -- Grafana n'a aucune notion de zone
        aujourd'hui (dashboards configurés à la main, hors périmètre Phase 1
        de l'isolation par zone), donc cette synchro continue de ne
        représenter que la DERNIÈRE zone ingérée, comme avant ce changement.

        La variable Grafana native basée sur la datasource Infinity (requête
        dynamique) ne s'exécute jamais dans cette version de Grafana --
        aucune requête HTTP n'est jamais émise, cause non identifiée malgré
        plusieurs configurations testées. On pousse donc les listes
        nous-mêmes dans des variables "custom", régénérées à chaque
        ingestion -- plus fiable qu'une dépendance à ce mécanisme.
        Voir aussi scripts/sync_grafana_dashboard_variables.py (même
        logique, en script autonome pour une resynchronisation manuelle)."""
        grafana_url = os.environ.get("GRAFANA_URL")
        grafana_password = read_secret("GRAFANA_ADMIN_PASSWORD")
        if not grafana_url or not grafana_password:
            return None

        graph_repo = self._arcadedb_repo_factory()(database)

        return GrafanaDashboardSyncAdapter(
            grafana_url=grafana_url,
            grafana_user=os.environ.get("GRAFANA_ADMIN_USER", "admin"),
            grafana_password=grafana_password,
            dashboard_uid=os.environ.get("GRAFANA_DASHBOARD_UID", "bos-equipment-demo"),
            graph_repository=graph_repo,
            equipment_catalog=self._build_equipment_catalog(),
        )

    def _build_rules_repository(self) -> JsonRulesRepositoryAdapter:
        config_path = os.path.join(os.path.dirname(__file__), "config", "charte_bim_rules.json")
        return JsonRulesRepositoryAdapter(config_path)

    def _build_equipment_catalog(self) -> JsonEquipmentCatalogAdapter:
        config_path = os.path.join(os.path.dirname(__file__), "config", "equipment_simulation_catalog.json")
        return JsonEquipmentCatalogAdapter(config_path)

    def _build_equipment_simulation_service(self) -> EquipmentSimulationService:
        global _equipment_simulation_service_singleton
        if _equipment_simulation_service_singleton is None:
            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            timeseries_repo = ArcadeDbTimeseriesAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
            max_workers = int(os.environ.get("SIMULATION_BACKFILL_WORKERS", "2"))
            _equipment_simulation_service_singleton = EquipmentSimulationService(
                catalog=self._build_equipment_catalog(),
                timeseries_repository=timeseries_repo,
                max_workers=max_workers,
            )
        return _equipment_simulation_service_singleton

    def _build_graph_ingestion_service(self, database: str) -> BimGraphIngestionService:
        """`database` : base graphe déjà résolue/créée UNE FOIS par
        `_handle_ingest_batch` pour la zone de ce batch (isolation par
        zone, Phase 1) -- ne relit plus ARCADEDB_DATABASE ici."""
        graph_repo = self._arcadedb_repo_factory()(database)

        # object_key peut etre une URL complete retournee par SeaweedFsStorageAdapter.
        seaweed_reader = SeaweedStorageAdapter()

        if IfcOpenShellAdapter is None:
            raise RuntimeError(
                "IfcOpenShellAdapter is not available — ensure ifcopenshell is installed and adapters/ifcopenshell_adapter.py can be imported"
            )

        return BimGraphIngestionService(
            seaweed_storage=seaweed_reader,
            validator=None,
            graph_repository=graph_repo,
            ifc_reader=IfcOpenShellAdapter(),
            rules_repository=self._build_rules_repository(),
            equipment_catalog=self._build_equipment_catalog(),
            equipment_simulation_service=self._build_equipment_simulation_service(),
            arcade_batch_size=500,
        )

    def _validate_single_file(self, disciplines: list[str], file_info: dict) -> dict:
        """Valide un seul fichier et retourne le rapport."""
        name = file_info["name"]
        content = file_info["content"]
        
        # Décode le base64 si c'est une string
        if isinstance(content, str):
            try:
                content_bytes = base64.b64decode(content)
            except Exception:
                # Si ce n'est pas du base64 valide, assume que c'est du texte
                content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        suffix = os.path.splitext(name)[1] or ".ifc"

        with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as handle:
            handle.write(content_bytes)
            temp_path = handle.name

        try:
            if IfcOpenShellAdapter is None:
                raise RuntimeError("IfcOpenShellAdapter not available — ifcopenshell dependency missing")
            reader = IfcOpenShellAdapter()
            validator_service = BimValidatorService(reader, self._build_rules_repository())
            report = validator_service.validate(temp_path, disciplines)

            if not report.success:
                return serialize_validation_report(
                    report,
                    extra={"stored": False, "storage_url": None, "storage_error": None},
                )

            storage = SeaweedFsStorageAdapter(master_url=os.environ.get("SEAWEEDFS_MASTER_URL", "http://localhost:9333"))
            discipline_prefix = "+".join(sorted(d.lower() for d in disciplines))
            remote_name = f"{discipline_prefix}/{os.path.basename(temp_path)}"
            try:
                storage_url = storage.store(temp_path, remote_name)
            except Exception as exc:
                return serialize_validation_report(
                    report,
                    extra={
                        "stored": False,
                        "storage_url": None,
                        "storage_error": f"Maquette conforme mais échec du stockage SeaweedFS : {exc}",
                    },
                )

            return serialize_validation_report(
                report,
                extra={"stored": True, "storage_url": storage_url, "storage_error": None},
            )
        except Exception as exc:
            raise RuntimeError(f"Validation batch failed: {exc}") from exc
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def serialize_validation_report(report: ValidationReport, extra: dict | None = None) -> dict:
    payload = {
        "success": report.success,
        "status": report.status,
        "schema_version": report.schema_version,
        "errors": [issue.message for issue in report.errors()],
        "warnings": [issue.message for issue in report.warnings()],
    }
    if extra:
        payload.update(extra)
    return payload


def serialize_graph_result(result: BimGraphIngestionResult) -> dict:
    return {
        "status": result.status,
        "message": result.message,
        "source_key": result.source_key,
        "commands_count": result.commands_count,
        "nodes_count": result.nodes_count,
        "edges_count": result.edges_count,
        "equipment_count": result.equipment_count,
        "unmatched_equipment_count": result.unmatched_equipment_count,
        "validation": serialize_validation_report(result.validation) if result.validation else None,
    }


def _ensure_arcadedb_ready(attempts: int = 10, delay_seconds: float = 3.0) -> None:
    """Cree la base ArcadeDB 'bim' si besoin, avec re-essais : au demarrage
    de la stack Docker, ce conteneur peut demarrer avant qu'ArcadeDB soit
    pret a accepter des commandes (pas de healthcheck bloquant dans le
    docker-compose)."""
    repo = ArcadeDbRepositoryAdapter(
        base_url=os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480"),
        database=os.environ.get("ARCADEDB_DATABASE", "bim"),
        username=os.environ.get("ARCADEDB_USERNAME", "root"),
        password=read_secret("ARCADEDB_PASSWORD", "testBIM123"),
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            repo.ensure_database()
            print("ArcadeDB: base 'bim' prete.")
            return
        except Exception as exc:
            last_error = exc
            print(f"ArcadeDB pas encore pret (essai {attempt}/{attempts}) : {exc}", file=sys.stderr)
            time.sleep(delay_seconds)
    print(f"ArcadeDB toujours indisponible apres {attempts} essais, demarrage quand meme : {last_error}", file=sys.stderr)


def run_server(host="0.0.0.0", port=8000):
    _ensure_arcadedb_ready()
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"API server listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
