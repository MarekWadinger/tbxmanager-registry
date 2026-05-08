"""Check that packages with platform-specific binaries have a maca64 entry.

Usage:
    python3 scripts/check_maca64_parity.py packages/foo/package.json [...]

Exits with code 0 always (warnings only, not blocking).
Prints warning lines to stdout; empty output means no issues.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PLATFORM_SPECIFIC = {"maci64", "win64", "glnxa64"}


def find_missing_maca64(filepath: Path) -> list[str]:
    """Return warning strings for versions missing maca64 in *filepath*."""
    try:
        data = json.loads(filepath.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return [f"SKIP {filepath}: {exc}"]

    name = data.get("name", filepath.parent.name)
    warnings: list[str] = []

    for ver, vdata in data.get("versions", {}).items():
        if not isinstance(vdata, dict):
            continue
        platforms = set(vdata.get("platforms", {}).keys())
        has_specific = bool(platforms & PLATFORM_SPECIFIC)
        has_maca64 = "maca64" in platforms
        has_all = "all" in platforms
        if has_specific and not has_maca64 and not has_all:
            present = sorted(platforms & PLATFORM_SPECIFIC)
            warnings.append(
                f"{name}@{ver}: has {present} but no maca64 or 'all' entry"
            )

    return warnings


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_maca64_parity.py <package.json> [...]")
        sys.exit(0)

    all_warnings: list[str] = []
    for arg in sys.argv[1:]:
        all_warnings.extend(find_missing_maca64(Path(arg)))

    if all_warnings:
        print("\n".join(all_warnings))


if __name__ == "__main__":
    main()
