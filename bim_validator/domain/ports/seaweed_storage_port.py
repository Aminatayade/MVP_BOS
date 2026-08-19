from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence


class SeaweedStoragePort(ABC):
    """Port used by the application layer to fetch validated IFC files from object storage."""

    @abstractmethod
    def download_ifc(
        self,
        object_key: str,
        *,
        bucket: str | None = None,
        target_path: str | None = None,
    ) -> str:
        """Download an IFC object and return the local file path."""
        raise NotImplementedError


class BimGraphRepositoryPort(ABC):
    """Port used by the application layer to persist BIM graph data into ArcadeDB."""

    @abstractmethod
    def execute_cypher_batch(self, commands: Sequence[str], batch_size: int = 500) -> None:
        """Execute OpenCypher statements in batches."""
        raise NotImplementedError
