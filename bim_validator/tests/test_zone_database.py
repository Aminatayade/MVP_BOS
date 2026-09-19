from infrastructure.zone_database import (
    LEGACY_DATABASE,
    invalidate_zone_cache,
    resolve_database_for_zone,
    sanitize_zone_to_database,
)


def test_sanitize_zone_to_database_is_deterministic():
    assert sanitize_zone_to_database("Batipompe") == sanitize_zone_to_database("Batipompe")


def test_sanitize_zone_to_database_strips_accents_and_punctuation():
    slug = sanitize_zone_to_database("Bâtiment A / Aile Nord")
    assert slug.startswith("bim_batiment_a_aile_nord_")
    assert " " not in slug and "â" not in slug and "/" not in slug


def test_sanitize_zone_to_database_disambiguates_colliding_slugs():
    # Deux zones différentes qui se réduiraient au même slug une fois les
    # accents/la ponctuation supprimés ne doivent PAS finir sur le même nom
    # de base -- le hash toujours ajouté (pas seulement en cas de collision
    # détectée) garantit ça sans avoir besoin d'interroger ArcadeDB.
    a = sanitize_zone_to_database("Bâtiment-A")
    b = sanitize_zone_to_database("Batiment A")
    assert a != b


class _FakeRepo:
    def __init__(self, *, exists: bool, legacy_zone_count: int = 0):
        self._exists = exists
        self._legacy_zone_count = legacy_zone_count
        self.query_sql_calls: list[dict] = []

    def database_exists(self) -> bool:
        return self._exists

    def query_sql(self, sql, params=None):
        self.query_sql_calls.append({"sql": sql, "params": params})
        return {"result": [{"n": self._legacy_zone_count}]}


def _factory(*, dedicated_exists: bool, legacy_zone_count: int):
    """repo_factory de test : renvoie un _FakeRepo "existe" pour la base
    candidate (préfixe bim_) si `dedicated_exists`, et un _FakeRepo simulant
    le comptage legacy pour "bim"."""
    repos: dict[str, _FakeRepo] = {}

    def factory(database: str) -> _FakeRepo:
        if database == LEGACY_DATABASE:
            repo = repos.setdefault(database, _FakeRepo(exists=True, legacy_zone_count=legacy_zone_count))
        else:
            repo = repos.setdefault(database, _FakeRepo(exists=dedicated_exists))
        return repo

    factory.repos = repos
    return factory


def test_resolve_database_for_zone_uses_dedicated_database_if_it_exists():
    zone = "QA-Test-Dedicated-Exists"
    factory = _factory(dedicated_exists=True, legacy_zone_count=0)

    database = resolve_database_for_zone(factory, zone, for_write=False)

    assert database == sanitize_zone_to_database(zone)
    invalidate_zone_cache(zone)


def test_resolve_database_for_zone_falls_back_to_legacy_when_zone_has_data_there():
    zone = "QA-Test-Legacy-Fallback"
    factory = _factory(dedicated_exists=False, legacy_zone_count=1)

    database = resolve_database_for_zone(factory, zone, for_write=False)

    assert database == LEGACY_DATABASE
    invalidate_zone_cache(zone)


def test_resolve_database_for_zone_read_unknown_zone_returns_none():
    zone = "QA-Test-Unknown-Read"
    factory = _factory(dedicated_exists=False, legacy_zone_count=0)

    database = resolve_database_for_zone(factory, zone, for_write=False)

    assert database is None
    invalidate_zone_cache(zone)


def test_resolve_database_for_zone_write_unknown_zone_returns_candidate_without_caching():
    zone = "QA-Test-Unknown-Write"
    factory = _factory(dedicated_exists=False, legacy_zone_count=0)

    database = resolve_database_for_zone(factory, zone, for_write=True)

    assert database == sanitize_zone_to_database(zone)
    # Pas mis en cache : la base n'existe pas encore réellement tant que
    # l'appelant n'a pas appelé ensure_database() dessus -- une lecture
    # concurrente juste après doit encore voir "zone inconnue".
    assert resolve_database_for_zone(factory, zone, for_write=False) is None
    invalidate_zone_cache(zone)


def test_resolve_database_for_zone_caches_successful_resolution():
    zone = "QA-Test-Cache-Hit"
    factory = _factory(dedicated_exists=True, legacy_zone_count=0)

    first = resolve_database_for_zone(factory, zone, for_write=False)
    # Deuxième appel : si le cache ne fonctionnait pas, la base "légacy"
    # serait quand même interrogée (query_sql) puisque _factory rebâtit un
    # nouveau _FakeRepo par défaut -- ici on vérifie juste que le résultat
    # est stable et identique, le comportement observable du cache.
    second = resolve_database_for_zone(factory, zone, for_write=False)

    assert first == second == sanitize_zone_to_database(zone)
    invalidate_zone_cache(zone)


def test_invalidate_zone_cache_forces_fresh_resolution():
    zone = "QA-Test-Invalidate"
    factory = _factory(dedicated_exists=False, legacy_zone_count=1)
    resolve_database_for_zone(factory, zone, for_write=False)  # met en cache "bim"

    invalidate_zone_cache(zone)

    # Zone supprimée entre-temps (n'a plus de données legacy) : sans
    # invalidation, le cache renverrait encore "bim" à tort.
    factory_after_delete = _factory(dedicated_exists=False, legacy_zone_count=0)
    assert resolve_database_for_zone(factory_after_delete, zone, for_write=False) is None
