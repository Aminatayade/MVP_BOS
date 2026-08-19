import json
import os
import sys
import tempfile
import time
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))

try:
    from adapters.ifcopenshell_adapter import IfcOpenShellAdapter
except Exception:
    IfcOpenShellAdapter = None
from adapters.json_rules_adapter import JsonRulesRepositoryAdapter
from adapters.seaweedfs_adapter import SeaweedFsStorageAdapter
from application.services.bim_graph_ingestion_service import BimGraphIngestionResult, BimGraphIngestionService
from application.bim_validator_service import BimValidatorService
from application.bim_ingestion_service import BimIngestionService
from domain.entities import ValidationReport
from infrastructure.adapters.arcadedb_repository_adapter import ArcadeDbRepositoryAdapter
from infrastructure.adapters.seaweed_storage_adapter import SeaweedStorageAdapter


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

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return
        # Simple graph view: /graph/view?floor_id=<global_id>
        parsed = urlparse(self.path)
        if parsed.path == "/graph/view":
            params = parse_qs(parsed.query)
            floor_id = params.get("floor_id", [None])[0]
            if not floor_id:
                self._send_json({"error": "floor_id query param is required"}, 400)
                return

            # Build a simple cypher to fetch the floor, its rooms and equipment
            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )

            # Note: ArcadeDB may return nodes under a wrapper; frontend will adapt.
            cypher = f"""
MATCH (f:Floor {{global_id: '{floor_id}'}})-[:HAS_CHILD]->(r:Room)
OPTIONAL MATCH (e:Equipment)-[:LOCATED_IN]->(r)
RETURN f AS floor, collect(distinct r) AS rooms, collect(distinct e) AS equipment
"""
            try:
                resp = repo.query_cypher(cypher)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/floors":
            # return a simple list of floor nodes (global_id + name)
            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
            try:
                cy = "MATCH (f:Floor) RETURN f LIMIT 500"
                resp = repo.query_cypher(cy)
                self._send_json({"ok": True, "result": resp})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if parsed.path == "/graph/buildings":
            arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
            arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
            arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
            arcadedb_password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )
            try:
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
            arcadedb_password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")
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
            arcadedb_password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")
            repo = ArcadeDbRepositoryAdapter(
                base_url=arcadedb_base_url,
                database=arcadedb_database,
                username=arcadedb_username,
                password=arcadedb_password,
            )

            cypher = f"""
MATCH (n {{global_id: '{gid}'}})-[r]-(m)
RETURN n AS node, collect(distinct m) AS neighbors
"""
            try:
                resp = repo.query_cypher(cypher)
                self._send_json({"ok": True, "result": resp})
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

            result = self._validate_single_file(discipline, file_info)
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

        valid_entries: list[tuple[str, dict]] = []
        invalid_results = []
        for file_info in files:
            discipline = (file_info.get("discipline") or "").strip().upper() if isinstance(file_info, dict) else ""
            if not isinstance(file_info, dict) or "name" not in file_info or "content" not in file_info or not discipline:
                invalid_results.append({
                    "filename": file_info.get("name", "unknown") if isinstance(file_info, dict) else "unknown",
                    "result": {"error": "File must have name, content (base64) and discipline fields"},
                })
                continue
            valid_entries.append((discipline, file_info))

        # Les maquettes backbone (ARCHI...) doivent être ingérées AVANT les
        # maquettes équipement (CVC, ELEC...), sinon le rattachement LOCATED_IN
        # ne trouve encore aucun Room/Floor pour cette zone. On trie ici plutôt
        # que d'imposer un ordre d'upload au client.
        rules_repo = self._build_rules_repository()

        def backbone_first(entry: tuple[str, dict]) -> int:
            discipline, _ = entry
            try:
                canonical = rules_repo.canonical_discipline(discipline)
                return 0 if rules_repo.is_spatial_backbone_discipline(canonical) else 1
            except KeyError:
                return 1

        valid_entries.sort(key=backbone_first)

        results = []
        for discipline, file_info in valid_entries:
            result = self._validate_store_and_ingest_graph(discipline, zone, file_info)
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

    def _validate_store_and_ingest_graph(self, discipline: str, zone: str, file_info: dict) -> dict:
        """Validation + stockage SeaweedFS + ingestion ArcadeDB en un seul flux."""
        file_payload = self._validate_single_file(discipline, file_info)

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
            discipline=discipline,
            zone=zone,
            run_validation=False,
        )
        return {
            "validation_storage": file_payload,
            "graph_ingestion": serialize_graph_result(graph_result),
        }

    def _build_rules_repository(self) -> JsonRulesRepositoryAdapter:
        config_path = os.path.join(os.path.dirname(__file__), "config", "charte_bim_rules.json")
        return JsonRulesRepositoryAdapter(config_path)

    def _build_graph_ingestion_service(self) -> BimGraphIngestionService:
        arcadedb_base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
        arcadedb_database = os.environ.get("ARCADEDB_DATABASE", "bim")
        arcadedb_username = os.environ.get("ARCADEDB_USERNAME", "root")
        arcadedb_password = os.environ.get("ARCADEDB_PASSWORD", "testBIM123")

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
            arcade_batch_size=500,
        )

    def _validate_single_file(self, discipline: str, file_info: dict) -> dict:
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
            report = validator_service.validate(temp_path, discipline)

            if not report.success:
                return serialize_validation_report(
                    report,
                    extra={"stored": False, "storage_url": None, "storage_error": None},
                )

            storage = SeaweedFsStorageAdapter(master_url=os.environ.get("SEAWEEDFS_MASTER_URL", "http://localhost:9333"))
            remote_name = f"{discipline.lower()}/{os.path.basename(temp_path)}"
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
        password=os.environ.get("ARCADEDB_PASSWORD", "testBIM123"),
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
