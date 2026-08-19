-- Scripts de migration pour ArcadeDB
-- 1) Supprimer toutes les relations (optionnel avant nettoyage)
MATCH ()-[r]->() DELETE r;

-- 2) Supprimer nœuds Typology, Room, Floor, Equipment (exemple)
MATCH (n:Typology) DELETE n;
MATCH (n:Room) DELETE n;
MATCH (n:Floor) DELETE n;
MATCH (n:Equipment) DELETE n;

-- 3) Trouver doublons par global_id
MATCH (n) WITH n.global_id AS gid, collect(n) AS nodes, size(collect(n)) AS cnt WHERE gid IS NOT NULL AND cnt > 1 RETURN gid, cnt LIMIT 1000;

-- 4) Nœuds avec noms placeholder
MATCH (n) WHERE n.name IN ['', 'null', 'none', 'n/a', 'na'] RETURN labels(n), n.global_id, n.ifc_type, n.name LIMIT 1000;

-- Remarque: Exécutez ces commandes depuis ArcadeDB Studio ou via l'API Cypher.
