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
from domain.entities import ValidationReport
from infrastructure.adapters.arcadedb_repository_adapter import ArcadeDbRepositoryAdapter
from infrastructure.secrets import read_secret
from infrastructure.adapters.seaweed_storage_adapter import SeaweedStorageAdapter
from datetime import datetime

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

                arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
                arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
                arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
                arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
                repo = ArcadeDbRepositoryAdapter(
                    base_url=arcadedb_base_url,
                    database=arcadedb_database,
                    username=arcadedb_username,
                    password=arcadedb_password,
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

                    deleted_readings = 0
                    if equip_ids:
                        id_list = ", ".join("'" + _cypher_escape(eid) + "'" for eid in equip_ids)
                        resp = repo.query_sql(f"DELETE FROM EquipmentReading WHERE global_id IN [{id_list}]")
                        rows = resp.get("result") or []
                        deleted_readings = rows[0].get("count", 0) if rows else 0

                    # 2) Chaque type de nœud spatial/métier, un par un.
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
            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )

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
            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
            try:
                cy = "MATCH (e:Equipment) RETURN e LIMIT 500"
                resp = repo.query_cypher(cy)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/neighbors":
            params = parse_qs(parsed.query)
            gid = params.get("global_id", [None])[0]
            if not gid:
                self._send_json({"error": "global_id query param is required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )

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
            if not gid:
                self._send_json({"error": "global_id query param is required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )

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
            zone = params.get("zone", [None])[0]
            if not zone:
                self._send_json({"error": "zone query param is required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )

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
            # les deux niveaux, pas seulement les Rooms.
            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
            try:
                locations = []
                for label in ("Room", "Floor"):
                    resp = repo.query_cypher(f"MATCH (n:{label}) RETURN n LIMIT 1000")
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
            if not gid_param or not metric:
                self._send_json({"error": "global_id and metric query params are required"}, 400)
                return
            location_ids = sorted({g.strip() for g in gid_param.split(",") if g.strip()})
            if not location_ids:
                self._send_json({"error": "global_id and metric query params are required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
                resp = repo.query_cypher(neighbors_cy)
                rows = resp.get("result") or []
                equipment_ids = sorted({row.get("gid") for row in rows if row.get("gid")})

                if not equipment_ids:
                    self._send_json({"ok": True, "equipment_count": 0, "readings": []})
                    return

                escaped_metric = metric.replace("'", "\\'")
                id_list = ", ".join("'" + eid.replace("'", "\\'") + "'" for eid in equipment_ids)
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
                # sont gardées avant troncature.
                sql = (
                    "SELECT ts, value FROM EquipmentReading WHERE "
                    f"metric = '{escaped_metric}' AND global_id IN [{id_list}] "
                    f"ORDER BY ts DESC LIMIT {raw_limit}"
                )
                ts_resp = repo.query_sql(sql)
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
            if not gid_param or not metric:
                self._send_json({"error": "global_id and metric query params are required"}, 400)
                return
            location_ids = sorted({g.strip() for g in gid_param.split(",") if g.strip()})
            if not location_ids:
                self._send_json({"error": "global_id and metric query params are required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
                resp = repo.query_cypher(neighbors_cy)
                rows = resp.get("result") or []
                equipment_ids = sorted({row.get("gid") for row in rows if row.get("gid")})

                if not equipment_ids:
                    self._send_json({"ok": True, "equipment_count": 0, "value": None})
                    return

                escaped_metric = metric.replace("'", "\\'")
                id_list = ", ".join("'" + eid.replace("'", "\\'") + "'" for eid in equipment_ids)
                conditions = [f"metric = '{escaped_metric}'", f"global_id IN [{id_list}]"]
                if start_param:
                    conditions.append(f"ts >= {int(start_param)}")
                if end_param:
                    conditions.append(f"ts <= {int(end_param)}")

                # Alias testés directement contre ArcadeDB : "bucket"/"avg"
                # sont refusés comme alias (mots réservés du parseur SQL),
                # "total"/"moy"/"maxi"/"mini"/"n" fonctionnent.
                sql = (
                    "SELECT SUM(value) as total, AVG(value) as moy, MAX(value) as maxi, "
                    "MIN(value) as mini, MIN(unit) as u, COUNT(*) as n FROM EquipmentReading WHERE "
                    f"{' AND '.join(conditions)}"
                )
                agg_resp = repo.query_sql(sql)
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
            if not building_gid:
                self._send_json({"error": "global_id query param is required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
                grouping_resp = repo.query_cypher(grouping_cy)
                floor_rows = grouping_resp.get("result") or []

                escaped_metric = metric.replace("'", "\\'")

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
                    id_list = ", ".join("'" + eid.replace("'", "\\'") + "'" for eid in all_equip_ids)
                    fetch_limit = min(5000, max(200, len(all_equip_ids) * 50))
                    sql = (
                        f"SELECT global_id, value, unit FROM EquipmentReading WHERE metric = '{escaped_metric}' "
                        f"AND global_id IN [{id_list}] ORDER BY ts DESC LIMIT {fetch_limit}"
                    )
                    recent_rows = repo.query_sql(sql).get("result") or []
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
            if not gid_param:
                self._send_json({"error": "global_id query param is required"}, 400)
                return
            location_ids = sorted({g.strip() for g in gid_param.split(",") if g.strip()})
            if not location_ids:
                self._send_json({"error": "global_id query param is required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
                categories = []
                for entry in sorted(by_category.values(), key=lambda c: c["category"]):
                    type_counts = entry.pop("_type_counts")
                    entry["equipment_types"] = [
                        {"equipment_type": t, "count": n} for t, n in sorted(type_counts.items())
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
            if not gid_param:
                self._send_json({"error": "global_id query param is required"}, 400)
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

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
            if not building_gid:
                self._send_json({"error": "global_id query param is required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
            if not building_gid or not name:
                self._send_json({"error": "global_id and name query params are required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
            if not floor_gid:
                self._send_json({"error": "global_id query param is required"}, 400)
                return

            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
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
        self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
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
            result = self._validate_store_and_ingest_graph(disciplines, zone, file_info)
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

    def _validate_store_and_ingest_graph(self, disciplines: list[str], zone: str, file_info: dict) -> dict:
        """Validation + stockage SeaweedFS + ingestion ArcadeDB en un seul flux."""
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

        graph_service = self._build_graph_ingestion_service()
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
                dashboard_sync = self._build_dashboard_sync()
                if dashboard_sync is not None:
                    dashboard_sync.sync_variables(zone=zone)
            except Exception as exc:
                print(f"Warning: Grafana dashboard sync failed: {exc}", file=sys.stderr)

        return {
            "validation_storage": file_payload,
            "graph_ingestion": serialize_graph_result(graph_result),
        }

    def _build_dashboard_sync(self) -> GrafanaDashboardSyncAdapter | None:
        """Resynchronise après chaque ingestion réussie les variables
        "Pièce/Étage" ET "Type de donnée" du cockpit Grafana, pour qu'elles
        reflètent l'état courant d'ArcadeDB/du catalogue plutôt qu'un
        instantané figé au moment de la création du dashboard.

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

        arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
        arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
        arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
        arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
        graph_repo = ArcadeDbRepositoryAdapter(
            base_url=arcadedb_base_url,
            database=arcadedb_database,
            username=arcadedb_username,
            password=arcadedb_password,
        )

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

    def _build_graph_ingestion_service(self) -> BimGraphIngestionService:
        arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
        arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
        arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
        arcadedb_password = read_secret("ARCADEDB_PASSWORD", "testBIM123")

        graph_repo = ArcadeDbRepositoryAdapter(
            base_url=arcadedb_base_url,
            database=arcadedb_database,
            username=arcadedb_username,
            password=arcadedb_password,
        )

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
