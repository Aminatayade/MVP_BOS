"""
FILE: occupancy.py
DESCRIPTION: Réglage des DEUX seuils d'occupation (bas/haut) pour le cockpit
             "Compréhension du taux d'usage des espaces" (BOS Exécution). Le
             tableau de bord lui-même (salles/capteurs simulés, agrégats) vit
             entièrement côté bim_api (voir bim_validator/api_server.py
             /graph/occupancy-dashboard, proxié par api-gateway/bim_proxy.py)
             puisque les données sources vivent dans ArcadeDB, pas Postgres.
             Seuls les seuils (config relationnelle simple, éditable) restent
             ici, même motif que MaintenanceSettingsModel/maintenance.py.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import OccupancySettingsModel
from schemas import OccupancySettingsIn
from security import verify_execution_allowed_roles

router = APIRouter(prefix="/api/v1/occupancy", tags=["occupancy"])

SETTINGS_SINGLETON_ID = "default"
DEFAULT_SEUIL_BAS = 20
DEFAULT_SEUIL_HAUT = 80


def _get_or_create_settings(db: Session) -> OccupancySettingsModel:
    settings = db.query(OccupancySettingsModel).filter(OccupancySettingsModel.id == SETTINGS_SINGLETON_ID).first()
    if not settings:
        settings = OccupancySettingsModel(id=SETTINGS_SINGLETON_ID, seuil_bas=DEFAULT_SEUIL_BAS, seuil_haut=DEFAULT_SEUIL_HAUT)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _as_dict(settings: OccupancySettingsModel) -> dict:
    return {"seuil_bas": float(settings.seuil_bas), "seuil_haut": float(settings.seuil_haut)}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), roles: list = Depends(verify_execution_allowed_roles)):
    return _as_dict(_get_or_create_settings(db))


@router.put("/settings")
def update_settings(
    body: OccupancySettingsIn,
    db: Session = Depends(get_db),
    roles: list = Depends(verify_execution_allowed_roles),
):
    settings = _get_or_create_settings(db)
    settings.seuil_bas = body.seuil_bas
    settings.seuil_haut = body.seuil_haut
    db.commit()
    db.refresh(settings)
    return _as_dict(settings)
