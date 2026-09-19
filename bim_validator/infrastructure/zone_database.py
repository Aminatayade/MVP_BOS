"""
FILE: zone_database.py
DESCRIPTION: Résolution du nom de base ArcadeDB correspondant à une zone --
             Phase 1 de l'isolation par zone (voir le plan
             "Isolation des données par zone"). Chaque zone ingérée à partir
             de maintenant obtient sa PROPRE base ArcadeDB (même serveur,
             pas de conteneur séparé) ; les zones déjà ingérées avant ce
             changement (ex: Batipompe) restent dans la base partagée
             historique ("bim" par défaut) -- aucune migration de données
             existantes, uniquement un repli sur l'existant.

             Ne couvre QUE le graphe (Building/Floor/Room/Equipment/
             Typology). Le magasin timeseries (EquipmentReading) reste
             délibérément unique et partagé, hors périmètre de cette phase
             (voir le plan).

INPUTS:
  - `repo_factory(database_name) -> ArcadeDbRepositoryAdapter` : fourni par
    l'appelant (api_server.py) pour ne pas dépendre ici de la construction
    des identifiants de connexion (base_url/utilisateur/mot de passe).

OUTPUTS:
  - `sanitize_zone_to_database(zone)` : nom de base déterministe.
  - `resolve_database_for_zone(repo_factory, zone, for_write=...)` : nom de
    base à utiliser, ou None si la zone est inconnue en lecture.
  - `invalidate_zone_cache(zone)` : à appeler après suppression d'une zone.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable

LEGACY_DATABASE = "bim"

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Résolution zone -> nom de base, en cache pour la durée de vie du process.
# Le mapping d'une zone est immuable une fois établi (une zone ne change
# jamais de base une fois créée) -- sans ce cache, CHAQUE lecture pour une
# zone déjà connue (Batipompe y compris, pour toujours, puisqu'elle ne
# migrera jamais vers sa propre base) déclencherait deux allers-retours
# ArcadeDB (liste des bases + comptage dans la base legacy) avant même la
# vraie requête, alors qu'on vient de gagner un facteur 4-10 sur ces mêmes
# endpoints cette session (requêtes paramétrées). Invalidée explicitement
# par `invalidate_zone_cache` après suppression d'une zone (voir DELETE
# /graph/zone dans api_server.py) : sinon une zone supprimée puis ré-ingérée
# sous le même nom resterait à tort résolue vers l'ancienne base.
_resolution_cache: dict[str, str] = {}


def sanitize_zone_to_database(zone: str) -> str:
    """"Bâtiment A / Aile Nord" -> "bim_batiment_a_aile_nord_3f2a9c1d".

    Déterministe (jamais besoin d'interroger ArcadeDB pour décider) : accents
    supprimés (NFKD), tout non-alphanumérique réduit à "_", tronqué, puis un
    suffixe de hash court TOUJOURS ajouté (pas seulement en cas de collision
    détectée) -- deux zones différentes dont le nom se réduit au même slug
    (ponctuation/accents différents) ne partagent alors jamais accidentellement
    la même base. La propriété `zone` posée sur les nœuds reste le texte
    original tel que tapé : ce nom de base n'est qu'un identifiant technique.
    """
    normalized = unicodedata.normalize("NFKD", zone.strip().lower())
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("_", normalized).strip("_") or "zone"
    slug = slug[:40]
    digest = hashlib.sha1(zone.encode("utf-8")).hexdigest()[:8]
    return f"bim_{slug}_{digest}"


def _zone_has_data_in_legacy(legacy_repo) -> Callable[[str], bool]:
    def check(zone: str) -> bool:
        try:
            resp = legacy_repo.query_sql(
                "SELECT COUNT(*) as n FROM Building WHERE zone = :zone LIMIT 1",
                params={"zone": zone},
            )
            rows = resp.get("result") or []
            return bool(rows and (rows[0].get("n") or 0) > 0)
        except Exception:
            # Base legacy injoignable/vide : traité comme "pas de données
            # legacy pour cette zone", pas comme une erreur qui bloquerait
            # toute résolution.
            return False
    return check


def resolve_database_for_zone(
    repo_factory: Callable[[str], object],
    zone: str,
    *,
    for_write: bool,
) -> str | None:
    """Nom de base ArcadeDB à utiliser pour `zone`.

    Ordre de résolution :
      1. Une base dédiée `bim_<slug>_<hash>` existe déjà -> on l'utilise.
      2. Sinon, la zone a des données dans la base legacy partagée -> on
         continue d'y écrire/lire (zone ingérée avant ce changement, jamais
         migrée).
      3. Sinon, zone réellement inconnue : en ÉCRITURE (ingestion), c'est une
         zone toute neuve -> on renvoie le nom candidat pour que l'appelant
         la crée (`ensure_database()`). En LECTURE, on renvoie None (zone
         inconnue, à traiter comme un 404 côté appelant) -- une lecture ne
         doit JAMAIS avoir pour effet de bord de créer une base vide pour
         une zone mal tapée.
    """
    cached = _resolution_cache.get(zone)
    if cached is not None:
        return cached

    candidate = sanitize_zone_to_database(zone)
    if repo_factory(candidate).database_exists():
        _resolution_cache[zone] = candidate
        return candidate

    legacy_repo = repo_factory(LEGACY_DATABASE)
    if _zone_has_data_in_legacy(legacy_repo)(zone):
        _resolution_cache[zone] = LEGACY_DATABASE
        return LEGACY_DATABASE

    if for_write:
        # Pas mis en cache ici : la base n'existe pas encore réellement tant
        # que l'appelant n'a pas appelé ensure_database() dessus. Le mettre
        # en cache prématurément referait échouer une lecture concurrente qui
        # verrait "connue" une base qui n'a pas encore été créée.
        return candidate
    return None


def invalidate_zone_cache(zone: str) -> None:
    """À appeler après suppression d'une zone (DELETE /graph/zone) -- sans
    ça, une zone supprimée puis ré-ingérée sous le même nom resterait à tort
    résolue vers son ancienne base (le cache est sinon permanent, voir plus
    haut)."""
    _resolution_cache.pop(zone, None)
