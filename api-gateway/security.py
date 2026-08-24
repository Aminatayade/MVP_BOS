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


def get_current_user_roles(token: str = Depends(oauth2_scheme)) -> list:
    """
    Décode le token JWT émis par Keycloak, récupère les clés JWKS via le réseau
    interne Docker, valide la signature et extrait les rôles de l'utilisateur.
    """
    try:
        jwks_url = f"{KEYCLOAK_INTERNAL_URL}/protocol/openid-connect/certs"
        jwks = requests.get(jwks_url).json()

        payload = jwt.decode(token, jwks, algorithms=["RS256"], options={"verify_aud": False})
        roles = payload.get("realm_access", {}).get("roles", [])
        return roles
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
