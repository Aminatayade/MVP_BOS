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


def add_space_pset(model, space):
    psets = ifcopenshell.util.element.get_psets(space) or {}
    if "Pset_SpaceCommon" in psets:
        return False
    props = {
        "IsExternal": False,
        "OccupancyType": "other",
    }
    try:
        ifcopenshell.api.run("pset.add_pset", model, product=space, name="Pset_SpaceCommon", properties=props)
        return True
    except Exception as e:
        print(f"Failed to add Pset_SpaceCommon to {space.GlobalId}: {e}")
        return False


def add_wall_pset(model, wall):
    psets = ifcopenshell.util.element.get_psets(wall) or {}
    if "Pset_WallCommon" in psets:
        # ensure FireRating exists
        ps = psets.get("Pset_WallCommon", {})
        if "FireRating" in ps:
            return False
    props = {
        "FireRating": "unknown",
    }
    try:
        ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Pset_WallCommon", properties=props)
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
