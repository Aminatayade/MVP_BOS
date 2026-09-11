"""
FILE: security.py
DESCRIPTION: Dépendances FastAPI d'authentification/autorisation (RBAC Keycloak),
             extraites de main.py pour être réutilisables par tout routeur de
             l'API Gateway (ex: bim_proxy.py) sans import circulaire.

INPUTS:
  - Token JWT (Header HTTP Authorization: Bearer <token>)
  - Variables d'environnement Keycloak (KEYCLOAK_INTERNAL_URL / KEYCLOAK_EXTERNAL_URL)

OUTPUTS:
  - Dépendance FastAPI `verify_allowed_roles` : liste des rôles Keycloak de
    l'utilisateur si autorisé (Administrateur/Configurateur), sinon HTTP 401/403.
"""

import os
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt
import requests

# -----------------------------------------------------------------------------
# CONFIGURATION KEYCLOAK (RÉSEAU INTERNE DOCKER VS ACCÈS NAVIGATEUR EXTERNE)
# -----------------------------------------------------------------------------
KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080/realms/bos-realm")
KEYCLOAK_EXTERNAL_URL = os.getenv("KEYCLOAK_EXTERNAL_URL", "http://localhost:8080/realms/bos-realm")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{KEYCLOAK_EXTERNAL_URL}/protocol/openid-connect/token")

# Rôles autorisés à accéder aux fonctions de configuration (BOS Light ET BIM)
ALLOWED_ROLES = {"Administrateur", "Configurateur"}

# Rôles autorisés à accéder à BOS Exécution (web-app-execution) -- inclut Client
# en plus, contrairement à ALLOWED_ROLES ci-dessus qui protège les routes de
# configuration/import BIM. Volontairement une constante et une dépendance
# séparées : ne jamais ajouter "Client" à ALLOWED_ROLES, ça donnerait par
# erreur accès aux routes de configuration à ce rôle.
EXECUTION_ALLOWED_ROLES = {"Administrateur", "Configurateur", "Client"}


def decode_token_payload(token: str) -> dict:
    """
    Décode le token JWT émis par Keycloak, récupère les clés JWKS via le réseau
    interne Docker et valide la signature -- factorisé hors de
    decode_roles_from_token() pour être réutilisé aussi par get_current_user()
    (bibliothèque de cockpits, BOS Exécution) sans dupliquer l'appel JWKS.
    """
    jwks_url = f"{KEYCLOAK_INTERNAL_URL}/protocol/openid-connect/certs"
    jwks = requests.get(jwks_url).json()
    return jwt.decode(token, jwks, algorithms=["RS256"], options={"verify_aud": False})


def decode_roles_from_token(token: str) -> list:
    """
    Extrait les rôles Keycloak d'un token déjà validé par decode_token_payload().

    Extrait de get_current_user_roles() pour être réutilisable hors du flux
    FastAPI Depends(oauth2_scheme) -- notamment pour l'authentification
    WebSocket (voir ws_equipment_telemetry dans bim_proxy.py), où le
    navigateur ne peut pas poser de header Authorization sur la requête de
    handshake : le token y arrive en paramètre de requête, pas en header.
    """
    return decode_token_payload(token).get("realm_access", {}).get("roles", [])


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Identité de l'utilisateur courant (pas seulement ses rôles) -- nécessaire
    pour attribuer un cockpit sauvegardé à son créateur et distinguer "mes
    cockpits" de "partagés par l'équipe" (aucune route existante avant la
    bibliothèque de cockpits n'avait besoin de savoir QUI fait la requête,
    seulement quels rôles elle porte).
    """
    try:
        payload = decode_token_payload(token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Jeton d'authentification invalide : {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = payload.get("preferred_username")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Jeton sans preferred_username.")
    return {"username": username, "sub": payload.get("sub")}


def get_current_user_roles(token: str = Depends(oauth2_scheme)) -> list:
    """Dépendance FastAPI (routes HTTP classiques, header Authorization)."""
    try:
        return decode_roles_from_token(token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Jeton d'authentification invalide : {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_allowed_roles(roles: list = Depends(get_current_user_roles)) -> list:
    """
    Dépendance de sécurité vérifiant si l'utilisateur possède au moins un des rôles autorisés
    (Administrateur ou Configurateur). Rejette le rôle Client avec un code HTTP 403 Forbidden.
    """
    if not set(roles).intersection(ALLOWED_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé : Seuls les rôles Administrateur et Configurateur sont autorisés."
        )
    return roles


def verify_execution_allowed_roles(roles: list = Depends(get_current_user_roles)) -> list:
    """
    Dépendance de sécurité pour BOS Exécution : vérifie si l'utilisateur possède
    au moins un des rôles autorisés (Administrateur, Configurateur ou Client).
    """
    if not set(roles).intersection(EXECUTION_ALLOWED_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé : Seuls les rôles Administrateur, Configurateur et Client sont autorisés."
        )
    return roles
