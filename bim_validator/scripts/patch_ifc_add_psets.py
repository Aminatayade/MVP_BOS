#!/usr/bin/env python3
"""Patch IFC: add missing Pset_SpaceCommon and Pset_WallCommon defaults.
Creates a backup <file>.bak before overwriting.
Usage: python patch_ifc_add_psets.py path/to/file.ifc
"""
import sys
import shutil
from pathlib import Path

try:
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.util.element
except Exception as e:
    print("Error: ifcopenshell is required to run this script. Details:", e)
    sys.exit(2)


def _find_pset_entity(element, name):
    # IsDefinedBy mélange IfcRelDefinesByProperties (a RelatingPropertyDefinition)
    # et IfcRelDefinesByType (n'en a pas) -- getattr(..., None) pour ne planter
    # que sur le premier type de relation.
    for rel in getattr(element, "IsDefinedBy", None) or []:
        pdef = getattr(rel, "RelatingPropertyDefinition", None)
        if pdef is not None and getattr(pdef, "Name", None) == name:
            return pdef
    return None


def add_space_pset(model, space):
    # Le Pset_SpaceCommon existe déjà sur la quasi-totalité des IfcSpace de
    # cette maquette (avec juste "Reference"), mais SANS les deux propriétés
    # requises par la charte -- le early-return d'origine ("si le Pset existe,
    # ne rien faire") laissait donc passer des Pset incomplets sans jamais les
    # compléter. On vérifie maintenant les propriétés elles-mêmes, pas juste
    # la présence du Pset.
    psets = ifcopenshell.util.element.get_psets(space) or {}
    existing = psets.get("Pset_SpaceCommon", {})
    missing = {}
    if not existing.get("IsExternal"):
        missing["IsExternal"] = False
    if not existing.get("OccupancyType"):
        missing["OccupancyType"] = "other"
    if not missing:
        return False
    try:
        pset = _find_pset_entity(space, "Pset_SpaceCommon")
        if pset is None:
            pset = ifcopenshell.api.run("pset.add_pset", model, product=space, name="Pset_SpaceCommon")
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=missing)
        return True
    except Exception as e:
        print(f"Failed to add Pset_SpaceCommon to {space.GlobalId}: {e}")
        return False


def add_wall_pset(model, wall):
    psets = ifcopenshell.util.element.get_psets(wall) or {}
    existing = psets.get("Pset_WallCommon", {})
    if existing.get("FireRating"):
        return False
    props = {
        "FireRating": "unknown",
    }
    try:
        pset = _find_pset_entity(wall, "Pset_WallCommon")
        if pset is None:
            pset = ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Pset_WallCommon")
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=props)
        return True
    except Exception as e:
        print(f"Failed to add Pset_WallCommon to {getattr(wall,'GlobalId',None)}: {e}")
        return False


def main():
    if len(sys.argv) != 2:
        print("Usage: python patch_ifc_add_psets.py path/to/file.ifc")
        sys.exit(2)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(2)

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy(path, backup)
    print(f"Backup created at: {backup}")

    model = ifcopenshell.open(str(path))

    spaces = model.by_type("IfcSpace")
    walls = model.by_type("IfcWall")

    added_spaces = 0
    for s in spaces:
        if add_space_pset(model, s):
            added_spaces += 1

    added_walls = 0
    for w in walls:
        if add_wall_pset(model, w):
            added_walls += 1

    if added_spaces or added_walls:
        out = str(path)
        model.write(out)
        print(f"Wrote updated IFC: {out}")

    print(f"Psets added: spaces={added_spaces}, walls={added_walls}")


if __name__ == '__main__':
    main()
