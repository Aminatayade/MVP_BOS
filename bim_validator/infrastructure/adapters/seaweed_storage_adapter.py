from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import requests

from domain.ports import SeaweedStoragePort


class SeaweedStorageAdapter(SeaweedStoragePort):
    """HTTP adapter to retrieve IFC models from SeaweedFS (REST or S3 gateway style URLs)."""

    def __init__(
        self,
        *,
        volume_base_url: str = "http://localhost:8080",
        s3_gateway_url: str | None = None,
        default_bucket: str | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self._volume_base_url = volume_base_url.rstrip("/")
        self._s3_gateway_url = s3_gateway_url.rstrip("/") if s3_gateway_url else None
        self._default_bucket = default_bucket
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()

    def download_ifc(
        self,
        object_key: str,
        *,
        bucket: str | None = None,
        target_path: str | None = None,
    ) -> str:
        if not object_key:
            raise ValueError("object_key must not be empty")

        source_url = self._build_source_url(object_key=object_key, bucket=bucket)
        destination = Path(target_path) if target_path else self._new_temp_ifc_path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        with self._session.get(source_url, stream=True, timeout=self._timeout_seconds) as response:
            response.raise_for_status()
            with destination.open("wb") as output_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output_file.write(chunk)

        return str(destination)

    def _build_source_url(self, *, object_key: str, bucket: str | None) -> str:
        if object_key.startswith("http://") or object_key.startswith("https://"):
            return object_key

        normalized_key = object_key.lstrip("/")
        resolved_bucket = bucket or self._default_bucket

        if resolved_bucket and self._s3_gateway_url:
            return (
                f"{self._s3_gateway_url}/"
                f"{quote(resolved_bucket)}/{quote(normalized_key, safe='/')}"
            )

        return f"{self._volume_base_url}/{quote(normalized_key, safe='/')}"

    @staticmethod
    def _new_temp_ifc_path(object_key: str) -> Path:
        suffix = Path(object_key).suffix or ".ifc"
        handle = tempfile.NamedTemporaryFile(prefix="ifc_ingest_", suffix=suffix, delete=False)
        handle.close()
        return Path(handle.name)
