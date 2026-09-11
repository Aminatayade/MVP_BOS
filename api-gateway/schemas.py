"""
FILE: schemas.py
DESCRIPTION: Schémas Pydantic de validation des requêtes JSON de l'API Gateway.

NOTES / PRÉREQUIS:
  - Première route "JSON-blob" du gateway (les routes existantes -- /api/v1/phases,
    bim_proxy.py -- ne valident rien via Pydantic, elles renvoient directement des
    objets ORM ou relaient une réponse amont). Validation volontairement légère
    ici : les champs obligatoires sont présents et `composition` est un objet,
    pas un schéma imbriqué décrivant chaque type de widget (qui évoluera côté
    frontend sans qu'il faille toucher au backend à chaque nouveau réglage).
"""

from typing import Optional
from pydantic import BaseModel


class CockpitIn(BaseModel):
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = []
    composition: dict
