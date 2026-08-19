"""
Couche ADAPTER
---------------
SeaweedFS n'a pas d'API "upload direct" simple : il faut d'abord demander un
identifiant de fichier (fid) au Master, puis pousser le binaire sur le
Volume Server qu'il désigne. C'est ce ballet en 2 temps qu'on isole ici.

Ta config docker-compose expose :
  - 9333 -> Master (assignation des fid)
  - 8080 -> Volume server (upload/download du binaire)

Si tu migres un jour vers MinIO ou S3, seul ce fichier change (compatible
S3 direct, pas besoin du ballet assign/upload) : le port StorageRepositoryPort
reste identique, donc application/ n'a rien à savoir de ce détail.
"""

import os
from urllib.parse import urlparse

import requests

from domain.ports import StorageRepositoryPort


class SeaweedFsStorageAdapter(StorageRepositoryPort):

    def __init__(self, master_url: str = "http://localhost:9333"):
        self._master_url = master_url.rstrip("/")

    def _assign_file_id(self) -> dict:
        """Demande au Master un identifiant de fichier + le volume server à utiliser."""
        response = requests.get(f"{self._master_url}/dir/assign", timeout=10)
        response.raise_for_status()
        return response.json()  # {"fid": "...", "url": "host:port", "publicUrl": "..."}

    def _resolve_reachable_url(self, assignment: dict) -> str:
        """
        SeaweedFS renvoie souvent l'adresse INTERNE du Volume Server (ex: l'IP du
        conteneur sur le réseau Docker bridge), injoignable depuis l'hôte ou un
        autre réseau. En déploiement mono-nœud (comme ici, master+volume dans le
        même conteneur), le Volume Server tourne sur la même machine que le
        Master : on ne garde que le PORT renvoyé par /dir/assign, et on
        reconstruit l'hôte à partir de master_url, qu'on sait déjà joignable.
        """
        assigned_port = assignment["url"].split(":")[-1]
        master_host = urlparse(self._master_url).hostname
        return f"http://{master_host}:{assigned_port}"

    def store(self, local_file_path: str, remote_name: str) -> str:
        if not os.path.exists(local_file_path):
            raise FileNotFoundError(f"Fichier introuvable pour upload : {local_file_path}")

        assignment = self._assign_file_id()
        fid = assignment["fid"]
        volume_base_url = self._resolve_reachable_url(assignment)

        upload_url = f"{volume_base_url}/{fid}"
        with open(local_file_path, "rb") as f:
            files = {"file": (remote_name, f)}
            response = requests.post(upload_url, files=files, timeout=60)
            response.raise_for_status()

        # URL de référence stockée dans ArcadeDB plus tard (Étape 4)
        return upload_url

    def delete(self, reference: str) -> None:
        """SeaweedFS supprime un fichier via un simple DELETE HTTP sur son URL
        (host:port/fid) — c'est la même URL que store() a retournée."""
        response = requests.delete(reference, timeout=10)
        response.raise_for_status()

    def retrieve_url(self, fid: str) -> str:
        """Utilitaire pour reconstruire une URL de lecture à partir d'un fid déjà connu."""
        master_host = urlparse(self._master_url).hostname
        return f"http://{master_host}:8080/{fid}"
