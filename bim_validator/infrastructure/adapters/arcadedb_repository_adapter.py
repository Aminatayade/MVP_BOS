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

    def ensure_database(self) -> None:
        """Crée la base ArcadeDB si elle n'existe pas encore (idempotent).

        ArcadeDB accepte des commandes sur une base inexistante sans erreur
        immédiate (elle répond 200 à l'écriture), mais la base n'est alors
        jamais réellement enregistrée sur le serveur : toute lecture
        ultérieure échoue avec "Database '<nom>' is not available". La base
        doit être créée explicitement une fois via l'endpoint serveur avant
        la première utilisation -- ce que fait cette méthode, appelée au
        démarrage de chaque service (voir api_server.py / timeseries_api.py).
        """
        list_url = f"{self._base_url}/api/v1/databases"
        response = self._session.get(list_url, timeout=self._timeout_seconds)
        response.raise_for_status()
        if self._database in response.json().get("result", []):
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

    def query_cypher(self, command: str) -> dict:
        """Execute a single Cypher query and return the parsed JSON response."""
        payload = {"language": "cypher", "command": command}
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

    def query_sql(self, command: str) -> dict:
        """Execute a single SQL command and return the parsed JSON response."""
        payload = {"language": "sql", "command": command}
        response = self._session.post(self._endpoint, json=payload, timeout=self._timeout_seconds)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"ArcadeDB SQL query failed (status={response.status_code}): {response.text}") from exc
        try:
            return response.json()
        except Exception:
            return {"raw": response.text}