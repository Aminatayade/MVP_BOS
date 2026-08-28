"""
Couche ADAPTER (ArcadeDB time-series natif)
---------------------------------------------
Implémente TimeseriesRepositoryPort. Deux endpoints ArcadeDB distincts sont
utilisés ici, volontairement séparés :
  - /api/v1/command/{db} (SQL classique) : ensure_schema() (DDL) et
    equipment_exists() (Equipment est un vertex normal du graphe BIM déjà
    ingéré par ailleurs, PAS une série temporelle).
  - /api/v1/ts/{db}/write (Line Protocol) : write_point()/write_points_batch(),
    confirmé comme le chemin d'écriture le plus rapide pour le type TIMESERIES.
"""

from __future__ import annotations

import time
from datetime import datetime

import requests

from domain.entities import UnifiedMessage
from domain.ports import TimeseriesRepositoryPort

TIMESERIES_TYPE_NAME = "EquipmentReading"


class ArcadeDbTimeseriesAdapter(TimeseriesRepositoryPort):
    def __init__(
        self,
        *,
        base_url: str,
        database: str,
        username: str,
        password: str,
        timeout_seconds: int = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._database = database
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()
        self._session.auth = (username, password)
        self._command_endpoint = f"{self._base_url}/api/v1/command/{self._database}"
        self._write_endpoint = f"{self._base_url}/api/v1/ts/{self._database}/write"

    # --- TimeseriesRepositoryPort -----------------------------------------

    def ensure_schema(self) -> None:
        """Idempotent : ré-exécutable sans risque à chaque démarrage.

        response.ok=True ne suffit PAS pour conclure que le type existe : observé
        en pratique un CREATE TIMESERIES TYPE renvoyant un succès HTTP sans que
        le type apparaisse ensuite dans schema:types (écritures concurrentes au
        démarrage avec l'ingestion du graphe BIM sur la même base). On vérifie
        donc explicitement via schema:types, avec quelques tentatives avant
        d'abandonner — mieux vaut un léger délai au démarrage qu'un consumer qui
        tourne indéfiniment en droppant tous les messages sans que ça se voie."""
        if self._timeseries_type_exists():
            return

        command = (
            f"CREATE TIMESERIES TYPE {TIMESERIES_TYPE_NAME} "
            "TIMESTAMP ts "
            "TAGS (global_id STRING, metric STRING, unit STRING, quality STRING, source STRING) "
            "FIELDS (value DOUBLE)"
        )
        payload = {"language": "sql", "command": command}

        for attempt in range(1, 4):
            response = self._session.post(self._command_endpoint, json=payload, timeout=self._timeout_seconds)
            if not response.ok and "already exists" not in response.text.lower():
                raise RuntimeError(
                    f"ArcadeDB ensure_schema failed (status={response.status_code}): {response.text}"
                )
            if self._timeseries_type_exists():
                return
            time.sleep(0.5 * attempt)

        raise RuntimeError(
            f"ArcadeDB ensure_schema: {TIMESERIES_TYPE_NAME} absent de schema:types "
            "après plusieurs tentatives de création (réponse HTTP OK mais type non visible)."
        )

    def _timeseries_type_exists(self) -> bool:
        payload = {
            "language": "sql",
            "command": f"SELECT name FROM schema:types WHERE name = '{TIMESERIES_TYPE_NAME}'",
        }
        response = self._session.post(self._command_endpoint, json=payload, timeout=self._timeout_seconds)
        if not response.ok:
            return False
        rows = response.json().get("result", [])
        return len(rows) > 0

    def equipment_exists(self, global_id: str) -> bool:
        escaped = _sql_escape(global_id)
        command = f"SELECT count(*) AS c FROM Equipment WHERE global_id = '{escaped}'"
        payload = {"language": "sql", "command": command}
        response = self._session.post(self._command_endpoint, json=payload, timeout=self._timeout_seconds)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"ArcadeDB equipment_exists query failed (status={response.status_code}): {response.text}"
            ) from exc

        result = response.json().get("result", [])
        if not result:
            return False
        return int(result[0].get("c", 0)) > 0

    def write_point(self, message: UnifiedMessage) -> None:
        self.write_points_batch([message])

    def write_points_batch(self, messages: list[UnifiedMessage]) -> None:
        if not messages:
            return

        body = "\n".join(_to_line_protocol(message) for message in messages)
        response = self._post_line_protocol(body)

        if not response.ok and "unknown timeseries type" in response.text.lower():
            # Observé en prod : le type TIMESERIES peut disparaître du schéma
            # runtime d'ArcadeDB en cours de fonctionnement, sans redémarrage
            # du serveur (comportement instable de cette feature, encore
            # SNAPSHOT côté ArcadeDB). Sans ce filet, le consumer NATS reste
            # bloqué à re-livrer indéfiniment le même message en échec.
            self.ensure_schema()
            response = self._post_line_protocol(body)

        if not response.ok:
            raise RuntimeError(
                f"ArcadeDB timeseries write failed (status={response.status_code}): {response.text}"
            )

    def _post_line_protocol(self, body: str):
        return self._session.post(
            f"{self._write_endpoint}?precision=ns",
            data=body.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
            timeout=self._timeout_seconds,
        )

    def query_readings(
        self,
        global_id: str,
        *,
        metric: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        # ArcadeDB refuse les littéraux de date en chaîne ("Cannot parse timestamp"
        # quel que soit le format ISO essayé) : seule la comparaison sur epoch
        # millis (entier, sans guillemets) fonctionne contre le champ ts TIMESERIES.
        conditions = [f"global_id = '{_sql_escape(global_id)}'"]
        if metric:
            conditions.append(f"metric = '{_sql_escape(metric)}'")
        if start is not None:
            conditions.append(f"ts >= {int(start.timestamp() * 1000)}")
        if end is not None:
            conditions.append(f"ts <= {int(end.timestamp() * 1000)}")

        command = (
            "SELECT ts, metric, value, unit, quality, source FROM "
            f"{TIMESERIES_TYPE_NAME} WHERE {' AND '.join(conditions)} "
            f"ORDER BY ts ASC LIMIT {int(limit)}"
        )
        payload = {"language": "sql", "command": command}
        response = self._session.post(self._command_endpoint, json=payload, timeout=self._timeout_seconds)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"ArcadeDB query_readings failed (status={response.status_code}): {response.text}"
            ) from exc

        rows = response.json().get("result", [])
        # ArcadeDB ajoute un champ interne "@props" (métadonnées de type) à
        # chaque ligne TIMESERIES : bruit à retirer avant de renvoyer au métier.
        return [{k: v for k, v in row.items() if not k.startswith("@")} for row in rows]

    def query_latest(
        self,
        global_id: str,
        *,
        metric: str | None = None,
        limit: int = 1,
    ) -> list[dict]:
        # Volontairement une méthode séparée de query_readings (ts ASC, pensé
        # pour tracer une courbe) : "valeur courante" veut le tri INVERSE
        # (ts DESC), sinon LIMIT renvoie les N points les plus ANCIENS de
        # tout l'historique au lieu des plus récents.
        conditions = [f"global_id = '{_sql_escape(global_id)}'"]
        if metric:
            conditions.append(f"metric = '{_sql_escape(metric)}'")

        command = (
            "SELECT ts, metric, value, unit, quality, source FROM "
            f"{TIMESERIES_TYPE_NAME} WHERE {' AND '.join(conditions)} "
            f"ORDER BY ts DESC LIMIT {int(limit)}"
        )
        payload = {"language": "sql", "command": command}
        response = self._session.post(self._command_endpoint, json=payload, timeout=self._timeout_seconds)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"ArcadeDB query_latest failed (status={response.status_code}): {response.text}"
            ) from exc

        rows = response.json().get("result", [])
        return [{k: v for k, v in row.items() if not k.startswith("@")} for row in rows]


def _sql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _escape_line_protocol_value(value: str) -> str:
    """Échappe espace/virgule/égal dans une valeur de TAG Line Protocol.
    Appliquée SYSTÉMATIQUEMENT (même sur des valeurs qui n'en ont pas
    besoin aujourd'hui) pour éviter un bug silencieux le jour où une valeur
    contient un de ces caractères."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace("=", "\\=")
        .replace(" ", "\\ ")
    )


def _to_line_protocol(message: UnifiedMessage) -> str:
    tags = {
        "global_id": message.global_id,
        "metric": message.metric,
        "unit": message.unit,
        "quality": message.quality,
        "source": message.source,
    }
    tag_str = ",".join(f"{key}={_escape_line_protocol_value(value)}" for key, value in tags.items())
    epoch_ns = int(message.timestamp.timestamp() * 1_000_000_000)
    return f"{TIMESERIES_TYPE_NAME},{tag_str} value={message.value} {epoch_ns}"
