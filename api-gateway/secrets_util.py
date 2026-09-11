"""
FILE: secrets_util.py
DESCRIPTION: Lecture d'un identifiant depuis un secret Docker monté en
             fichier, avant de retomber sur la variable d'environnement
             classique. Convention standard des images officielles (ex:
             `POSTGRES_PASSWORD_FILE` sur l'image `postgres`) :
             `{env_var}_FILE` pointe vers un fichier (`/run/secrets/...`
             typiquement) -- voir docker-compose.fusion.yml, section
             `secrets:`.

Objectif : un mot de passe passé en variable d'environnement classique est
visible en clair via `docker inspect <conteneur>` par quiconque a accès à
Docker sur la machine. Un secret en fichier évite ça -- sans rien changer
pour qui ne configure pas de secret Docker (fallback sur la variable
d'environnement directe, comportement inchangé).
"""

import os


def read_secret(env_var: str, default: str | None = None) -> str | None:
    file_path = os.environ.get(f"{env_var}_FILE")
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    return os.environ.get(env_var, default)
