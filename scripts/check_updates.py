#!/usr/bin/env python3
"""Check for new upstream releases of registered packages.

Scans each package in the registry, extracts the GitHub owner/repo from
download URLs, queries the GitHub Releases API for versions not yet
registered, and reports (or applies) the missing entries.

Environment variables:
    GH_TOKEN: GitHub token for API requests (recommended — avoids rate limits)

Usage:
    # Report missing versions for all packages
    python scripts/check_updates.py

    # Check a single package
    python scripts/check_updates.py --package yalmip

    # Apply updates: download archives, compute SHA256, write package.json
    python scripts/check_updates.py --package yalmip --apply

    # Include yanked/pre-release-style tags (e.g. R20160930-patched)
    python scripts/check_updates.py --package yalmip --include-patches

    # Dry run: show what --apply would do without writing
    python scripts/check_updates.py --package yalmip --apply --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

API_DELAY_SECONDS = 1

# GitHub source archive URL for repos that don't attach release assets.
SOURCE_ARCHIVE_URL = (
    "https://github.com/{owner}/{repo}/archive/refs/tags/{tag}.zip"
)

# Match GitHub URLs in platform entries to extract owner/repo.
GITHUB_URL_PATTERN = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/"
)


def log(msg: str) -> None:
    """Print a log message to stderr."""
    print(f"[check-updates] {msg}", file=sys.stderr, flush=True)


def make_request(
    url: str, method: str = "GET", headers: dict[str, str] | None = None
) -> urllib.request.addinfourl:
    """Make an HTTP(S) request with optional GH_TOKEN auth."""
    headers = headers or {}
    token = os.environ.get("GH_TOKEN")
    if token and "github" in url:
        headers["Authorization"] = f"token {token}"
        headers["Accept"] = headers.get(
            "Accept", "application/vnd.github.v3+json"
        )
    headers.setdefault("User-Agent", "tbxmanager-registry-bot")

    req = urllib.request.Request(url, headers=headers, method=method)
    ctx = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=30, context=ctx)


def extract_github_repo(package: dict) -> tuple[str, str] | None:
    """Extract (owner, repo) from download URLs in a package.json.

    Scans all version/platform URLs for a GitHub origin.
    Returns the first match, or None.
    """
    for ver_data in package.get("versions", {}).values():
        for plat_data in ver_data.get("platforms", {}).values():
            url = plat_data.get("url", "")
            m = GITHUB_URL_PATTERN.search(url)
            if m:
                return m.group(1), m.group(2)
    return None


def fetch_releases(
    owner: str, repo: str
) -> list[dict]:
    """Fetch all releases from GitHub API (paginated)."""
    releases: list[dict] = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/releases"
            f"?per_page=100&page={page}"
        )
        try:
            resp = make_request(url)
            data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            log(f"Error fetching releases for {owner}/{repo}: {e}")
            break

        if not data:
            break
        releases.extend(data)
        if len(data) < 100:
            break
        page += 1
        time.sleep(API_DELAY_SECONDS)

    return releases


def tag_to_version_key(tag: str) -> str:
    """Convert a GitHub release tag to a registry version key.

    - Tags like 'v1.2.3' → '1.2.3' (strip v prefix)
    - Tags like 'R20250626' → 'R20250626' (keep as-is)
    """
    if tag.startswith("v") and re.match(r"^v\d+\.\d+", tag):
        return tag[1:]
    return tag


def get_release_download_url(
    release: dict, owner: str, repo: str, platform: str
) -> str:
    """Determine the download URL for a release.

    Prefers attached .zip/.tar.gz assets (matching platform if applicable).
    Falls back to the GitHub source archive URL for the tag.
    """
    tag = release["tag_name"]
    assets = release.get("assets", [])
    archive_assets = [
        a
        for a in assets
        if a["name"].endswith(".zip") or a["name"].endswith(".tar.gz")
    ]

    if archive_assets:
        # Try platform-specific match first
        if platform != "all":
            for asset in archive_assets:
                if platform in asset["name"]:
                    return asset["browser_download_url"]
        return archive_assets[0]["browser_download_url"]

    # No attached assets — use GitHub source archive
    return SOURCE_ARCHIVE_URL.format(owner=owner, repo=repo, tag=tag)


def download_and_hash(url: str) -> str:
    """Download a file and compute its SHA256 hash. Returns hex digest."""
    resp = make_request(url)
    sha = hashlib.sha256()
    with tempfile.NamedTemporaryFile() as tmp:
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            sha.update(chunk)
            tmp.write(chunk)
    return sha.hexdigest()


def detect_platforms(package: dict) -> list[str]:
    """Detect the platform set used by existing versions.

    Returns the platforms from the most recent version entry,
    defaulting to ['all'] if none found.
    """
    versions = package.get("versions", {})
    if not versions:
        return ["all"]
    # Use the last version entry (dict preserves insertion order in 3.7+)
    last_ver = list(versions.values())[-1]
    platforms = list(last_ver.get("platforms", {}).keys())
    return platforms or ["all"]


def detect_matlab_constraint(package: dict) -> str | None:
    """Detect the MATLAB constraint used by existing versions."""
    for ver_data in package.get("versions", {}).values():
        matlab = ver_data.get("matlab")
        if matlab:
            return matlab
    return None


def detect_version_convention(package: dict) -> str:
    """Detect the naming convention used by existing versions.

    Returns one of:
      - "semver"   : numeric dotted versions (1.2.3, 3.0)
      - "rdate"    : R-prefixed dates (R20250626, R20250626_fix2)
      - "date"     : bare date numbers (20210621)
      - "mixed"    : inconsistent (should not happen in well-maintained packages)
      - "unknown"  : no versions or unrecognizable format
    """
    semver_pat = re.compile(r"^v?\d+\.\d+(\.\d+)?$")
    rdate_pat = re.compile(r"^R\d{8}")
    bare_date_pat = re.compile(r"^\d{8}$")

    conventions: set[str] = set()
    for ver in package.get("versions", {}):
        if semver_pat.match(ver):
            conventions.add("semver")
        elif rdate_pat.match(ver):
            conventions.add("rdate")
        elif bare_date_pat.match(ver):
            conventions.add("date")

    if len(conventions) == 1:
        return conventions.pop()
    if len(conventions) > 1:
        return "mixed"
    return "unknown"


def tag_matches_convention(tag: str, convention: str) -> bool:
    """Check if a release tag matches the package's version convention."""
    version_key = tag_to_version_key(tag)
    if convention == "semver":
        return bool(re.match(r"^\d+\.\d+(\.\d+)?$", version_key))
    if convention == "rdate":
        return bool(re.match(r"^R\d{8}", version_key))
    if convention == "date":
        return bool(re.match(r"^\d{8}$", version_key))
    # unknown/mixed: accept anything
    return True


def check_package(
    name: str,
    pkg_path: Path,
    *,
    apply: bool = False,
    dry_run: bool = False,
    include_patches: bool = False,
) -> list[dict]:
    """Check a single package for upstream updates.

    Returns a list of dicts describing missing versions:
        {tag, version_key, date, url, sha256 (if applied)}
    """
    with open(pkg_path) as f:
        package = json.load(f)

    repo_info = extract_github_repo(package)
    if not repo_info:
        log(f"{name}: no GitHub URL found in download URLs, skipping")
        return []

    owner, repo = repo_info
    log(f"{name}: checking {owner}/{repo} ...")

    registered_versions = set(package.get("versions", {}).keys())
    releases = fetch_releases(owner, repo)
    time.sleep(API_DELAY_SECONDS)

    platforms = detect_platforms(package)
    matlab = detect_matlab_constraint(package)
    convention = detect_version_convention(package)

    missing = []
    skipped_convention = []

    for release in releases:
        tag = release["tag_name"]
        version_key = tag_to_version_key(tag)
        published = (release.get("published_at") or "")[:10]

        # Skip if already registered
        if version_key in registered_versions:
            continue

        # Skip drafts
        if release.get("draft", False):
            continue

        # Skip patch/hotfix tags unless requested
        if not include_patches:
            # Tags with suffixes like _fix, _fix2, -patched, _hotfix
            if re.search(r"[_-](fix|patch|hotfix)", tag, re.IGNORECASE):
                continue

        # Reject tags that violate the package's naming convention
        if not tag_matches_convention(tag, convention):
            skipped_convention.append(tag)
            continue

        entry = {
            "tag": tag,
            "version_key": version_key,
            "date": published,
            "platforms": {},
        }

        for platform in platforms:
            url = get_release_download_url(release, owner, repo, platform)
            entry["platforms"][platform] = {"url": url, "sha256": None}

        missing.append(entry)

    if skipped_convention:
        log(
            f"{name}: SKIPPED {len(skipped_convention)} tag(s) violating "
            f"'{convention}' convention: {', '.join(skipped_convention)}"
        )

    if not missing:
        log(f"{name}: up to date ({len(registered_versions)} versions)")
        return []

    log(f"{name}: {len(missing)} new version(s) found")

    if apply:
        new_versions: dict = {}
        for entry in missing:
            vk = entry["version_key"]
            for platform, plat_data in entry["platforms"].items():
                url = plat_data["url"]
                if dry_run:
                    log(f"  {vk} [{platform}]: would download {url}")
                else:
                    log(f"  {vk} [{platform}]: downloading {url} ...")
                    try:
                        sha256 = download_and_hash(url)
                        plat_data["sha256"] = sha256
                        log(f"  {vk} [{platform}]: SHA256 = {sha256}")
                    except (urllib.error.URLError, OSError) as e:
                        log(f"  {vk} [{platform}]: download failed: {e}")
                    time.sleep(API_DELAY_SECONDS)

            # Build version entry
            version_entry: dict = {
                "platforms": entry["platforms"],
            }
            if matlab:
                version_entry["matlab"] = matlab
            version_entry["dependencies"] = {}
            if entry["date"]:
                version_entry["released"] = entry["date"]

            new_versions[vk] = version_entry

        if not dry_run:
            # Prepend new versions (descending) before existing ones
            existing_versions = package.get("versions", {})
            package["versions"] = {**new_versions, **existing_versions}

            with open(pkg_path, "w") as f:
                json.dump(package, f, indent=2, ensure_ascii=False)
                f.write("\n")
            log(f"{name}: wrote {len(missing)} new version(s) to {pkg_path}")

    return missing


def format_report(
    name: str, missing: list[dict], *, owner: str, repo: str
) -> str:
    """Format a human-readable report of missing versions."""
    lines = [f"\n{name} ({owner}/{repo}): {len(missing)} new version(s)\n"]
    lines.append(f"  {'Version':<25} {'Date':<12} {'URL'}")
    lines.append(f"  {'-'*25} {'-'*12} {'-'*60}")
    for entry in missing:
        vk = entry["version_key"]
        date = entry["date"] or "unknown"
        # Show first platform URL
        first_plat = next(iter(entry["platforms"].values()))
        url = first_plat["url"]
        sha = first_plat.get("sha256") or ""
        line = f"  {vk:<25} {date:<12} {url}"
        if sha:
            line += f"\n  {'':25} {'':12} sha256={sha}"
        lines.append(line)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check for new upstream releases of registered packages"
    )
    parser.add_argument(
        "--package",
        help="Check a single package by name (default: all packages)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Download archives, compute SHA256, and update package.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --apply, show what would be done without writing",
    )
    parser.add_argument(
        "--include-patches",
        action="store_true",
        help="Include patch/hotfix tags (e.g. R20250626_fix2)",
    )
    args = parser.parse_args()

    packages_dir = Path("packages")
    if not packages_dir.is_dir():
        print(
            "Error: 'packages/' directory not found. "
            "Run from the registry root.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Collect packages to check
    if args.package:
        pkg_path = packages_dir / args.package / "package.json"
        if not pkg_path.is_file():
            print(f"Error: {pkg_path} not found", file=sys.stderr)
            sys.exit(1)
        targets = [(args.package, pkg_path)]
    else:
        targets = sorted(
            (d.name, d / "package.json")
            for d in packages_dir.iterdir()
            if (d / "package.json").is_file()
        )

    total_missing = 0
    for name, pkg_path in targets:
        with open(pkg_path) as f:
            package = json.load(f)

        repo_info = extract_github_repo(package)
        if not repo_info:
            continue

        owner, repo = repo_info

        missing = check_package(
            name,
            pkg_path,
            apply=args.apply,
            dry_run=args.dry_run,
            include_patches=args.include_patches,
        )

        if missing:
            print(format_report(name, missing, owner=owner, repo=repo))
            total_missing += len(missing)

    if total_missing == 0:
        print("\nAll packages are up to date.")
    else:
        action = "applied" if (args.apply and not args.dry_run) else "available"
        print(f"\nTotal: {total_missing} update(s) {action}.")


if __name__ == "__main__":
    main()
