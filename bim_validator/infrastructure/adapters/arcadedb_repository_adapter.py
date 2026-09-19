from __future__ import annotations

from collections.abc import Sequence

import requests

from domain.ports import BimGraphRepositoryPort


class ArcadeDbRepositoryAdapter(BimGraphRepositoryPort):
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
        self._endpoint = f"{self._base_url}/api/v1/command/{self._database}"

    def list_databases(self) -> list[str]:
        """Toutes les bases enregistrées sur le serveur ArcadeDB (pas
        seulement `self._database`) -- utilisé par `GET /graph/zones` pour
        retrouver les bases `bim_<slug>_<hash>` d'une zone (isolation par
        zone, Phase 1)."""
        list_url = f"{self._base_url}/api/v1/databases"
        response = self._session.get(list_url, timeout=self._timeout_seconds)
        response.raise_for_status()
        return response.json().get("result", [])

    def database_exists(self) -> bool:
        """Vrai si `self._database` est déjà enregistrée sur le serveur ArcadeDB.

        Factorisé hors de `ensure_database()` pour être réutilisé par
        `zone_database.resolve_database_for_zone()` (choix de la base d'une
        zone à la LECTURE, qui ne doit jamais créer de base au passage --
        seul `ensure_database()`, appelé uniquement à l'ingestion, le fait).
        """
        return self._database in self.list_databases()

    def ensure_database(self) -> None:
        """Crée la base ArcadeDB si elle n'existe pas encore (idempotent).

        ArcadeDB accepte des commandes sur une base inexistante sans erreur
        immédiate (elle répond 200 à l'écriture), mais la base n'est alors
        jamais réellement enregistrée sur le serveur : toute lecture
        ultérieure échoue avec "Database '<nom>' is not available". La base
        doit être créée explicitement une fois via l'endpoint serveur avant
        la première utilisation -- ce que fait cette méthode, appelée au
        démarrage de chaque service (voir api_server.py / timeseries_api.py)
        et, désormais, à l'ingestion d'une zone dont c'est la première fois
        (voir zone_database.resolve_database_for_zone).
        """
        if self.database_exists():
            return

        create_url = f"{self._base_url}/api/v1/server"
        response = self._session.post(
            create_url,
            json={"command": f"create database {self._database}"},
            timeout=self._timeout_seconds,
        )
        if response.ok:
            return
        # "already exists" possible en cas de course entre plusieurs services
        # démarrant en parallèle (bim_api / timeseries_api) : pas une erreur.
        if "already exists" in response.text.lower():
            return
        raise RuntimeError(
            f"Impossible de créer la base ArcadeDB '{self._database}' "
            f"(status={response.status_code}): {response.text}"
        )

    def execute_cypher_batch(self, commands: Sequence[str], batch_size: int = 500) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")

        cleaned_commands = [command.strip().rstrip(";") for command in commands if command.strip()]
        if not cleaned_commands:
            return

        for start in range(0, len(cleaned_commands), batch_size):
            chunk = cleaned_commands[start : start + batch_size]
            self._execute_chunk(chunk)

    def _execute_chunk(self, chunk: Sequence[str]) -> None:
        for command in chunk:
            self._execute_command(command)

    def _execute_command(self, command: str) -> None:
        payload = {"language": "cypher", "command": command}

        response = self._session.post(
            self._endpoint,
            json=payload,
            timeout=self._timeout_seconds,
        )

        if response.ok:
            return

        error_message: str
        try:
            error_payload = response.json()
            error_message = str(error_payload)
        except Exception:
            error_message = response.text

        raise RuntimeError(
            "ArcadeDB OpenCypher batch execution failed "
            f"(status={response.status_code}): {error_message}"
        )

    def query_cypher(self, command: str, params: dict | None = None) -> dict:
        """Execute a single Cypher query and return the parsed JSON response.

        `params` : liaison native ArcadeDB (`:nom` dans `command`) -- à
        utiliser pour toute liste de dizaines/centaines d'IDs (équipements)
        plutôt que de les interpoler en littéraux dans le texte de la
        requête. Un `IN [...]` avec des centaines de littéraux entre quotes
        mesuré à 13-52s (voire une connexion perdue côté serveur pour
        ~4800 IDs) contre ~5-6s de façon fiable avec un paramètre lié -- le
        texte de la requête reste petit et ArcadeDB n'a pas à le reparser à
        chaque appel avec un contenu différent.
        """
        payload: dict = {"language": "cypher", "command": command}
        if params:
            payload["params"] = params
        response = self._session.post(self._endpoint, json=payload, timeout=self._timeout_seconds)
        try:
            response.raise_for_status()
        except Exception as exc:
            # raise a clearer error for callers
            raise RuntimeError(f"ArcadeDB query failed (status={response.status_code}): {response.text}") from exc
        try:
            return response.json()
        except Exception:
            return {"raw": response.text}

    def query_sql(self, command: str, params: dict | None = None) -> dict:
        """Execute a single SQL command and return the parsed JSON response.

        Voir `query_cypher` pour `params` -- même raisonnement, même gain
        mesuré côté SQL (`WHERE global_id IN :ids` avec params={"ids": [...]})."""
        payload: dict = {"language": "sql", "command": command}
        if params:
            payload["params"] = params
        response = self._session.post(self._endpoint, json=payload, timeout=self._timeout_seconds)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"ArcadeDB SQL query failed (status={response.status_code}): {response.text}") from exc
        try:
            return response.json()
        except Exception:
            return {"raw": response.text}