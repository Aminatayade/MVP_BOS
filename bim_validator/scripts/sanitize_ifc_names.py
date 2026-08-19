#!/usr/bin/env python3
"""Sanitize IFC text by replacing common placeholder name values with empty string.
Creates a backup with .bak extension before modification.
Usage: python sanitize_ifc_names.py path/to/file.ifc
"""
import sys
import shutil
import re

PLACEHOLDERS = [
    "N/A",
    "<Default>",
    "Default",
    "None",
    "NULL",
    "na",
    "n/a",
    "<default>",
]


def sanitize_file(path: str) -> None:
    backup = path + ".bak"
    shutil.copy(path, backup)
    print(f"Backup created: {backup}")

    with open(path, "r", errors="ignore") as f:
        text = f.read()

    # Replace placeholder quoted tokens like 'N/A' with '' (empty quoted string)
    for token in PLACEHOLDERS:
        # build regex to match the exact quoted token regardless of surrounding whitespace/case
        pattern = re.compile(re.escape("'" + token + "'"), flags=re.IGNORECASE)
        text = pattern.sub("''", text)

    # Also collapse any sequences like '', '' to '', '' (keep as is)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"Sanitization applied to: {path}")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python sanitize_ifc_names.py path/to/file.ifc")
        sys.exit(2)
    sanitize_file(sys.argv[1])
