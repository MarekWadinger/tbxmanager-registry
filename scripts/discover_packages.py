#!/usr/bin/env python3
"""Discover new packages by searching GitHub for the tbxmanager-package topic.

For each repository found:
  - Fetch tbxmanager.json from the default branch
  - Check if the package is already registered
  - Check for an existing open submission issue
  - Create a submission issue if everything checks out

Environment variables:
    GH_TOKEN: GitHub token for API requests (required)
    DRY_RUN: If set to any non-empty value, print actions without executing
"""

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MAX_ISSUES_PER_RUN = 10
API_DELAY_SECONDS = 2

REGISTRY_REPO = "MarekWadinger/tbxmanager-registry"


def log(msg):
    """Print a log message to stdout."""
    print(f"[discover] {msg}", flush=True)


def make_request(url, method="GET", headers=None):
    """Make an HTTP(S) request with GH_TOKEN auth."""
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


def load_registered_names():
    """Load the set of package names already in the registry."""
    packages_dir = Path("packages")
    names = set()
    if packages_dir.is_dir():
        for pkg_dir in packages_dir.iterdir():
            pkg_file = pkg_dir / "package.json"
            if pkg_file.is_file():
                try:
                    with open(pkg_file) as f:
                        data = json.load(f)
                    names.add(data.get("name", pkg_dir.name))
                except (json.JSONDecodeError, OSError):
                    names.add(pkg_dir.name)
    return names


def search_topic_repos():
    """Search GitHub for repos with the tbxmanager-package topic.

    Returns a list of repo dicts from the GitHub API.
    Paginates through all results.
    """
    repos = []
    page = 1
    while True:
        url = (
            "https://api.github.com/search/repositories"
            f"?q=topic:tbxmanager-package&per_page=100&page={page}"
        )
        try:
            resp = make_request(url)
            data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            log(f"Error searching GitHub: {e}")
            break

        items = data.get("items", [])
        if not items:
            break
        repos.extend(items)
        if len(repos) >= data.get("total_count", 0):
            break
        page += 1
        time.sleep(API_DELAY_SECONDS)

    return repos


def fetch_tbxmanager_json(owner, repo, default_branch):
    """Fetch tbxmanager.json from the default branch of a repo.

    Returns parsed JSON dict or None if not found / invalid.
    """
    url = (
        f"https://raw.githubusercontent.com/{owner}/{repo}"
        f"/{default_branch}/tbxmanager.json"
    )
    try:
        resp = make_request(url)
        return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log(f"  No tbxmanager.json found in {owner}/{repo}")
        else:
            log(f"  HTTP {e.code} fetching tbxmanager.json from {owner}/{repo}")
        return None
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        log(f"  Error fetching tbxmanager.json from {owner}/{repo}: {e}")
        return None


def get_latest_release(owner, repo):
    """Fetch the latest release for a repo.

    Returns parsed release dict or None.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        resp = make_request(url)
        return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log(f"  No releases found for {owner}/{repo}")
        else:
            log(f"  HTTP {e.code} fetching latest release for {owner}/{repo}")
        return None
    except urllib.error.URLError as e:
        log(f"  Error fetching release for {owner}/{repo}: {e}")
        return None


def has_open_issue(name):
    """Check if there is already an open submission issue for this package.

    Uses the gh CLI for authenticated search.
    """
    try:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--search", f"Submit: {name} in:title",
                "--state", "open",
                "--json", "number",
                "--limit", "1",
                "--repo", REGISTRY_REPO,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            issues = json.loads(result.stdout)
            return len(issues) > 0
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return False


def determine_platform(pkg_data):
    """Determine the platform string from tbxmanager.json.

    Returns (platform_string, skip_reason) where skip_reason is None on success.
    """
    platforms = pkg_data.get("platforms", [])
    if not platforms:
        # Default to "all" if no platforms specified
        return "all (pure MATLAB, no MEX files)", None

    if isinstance(platforms, list):
        if len(platforms) == 1:
            p = platforms[0]
            if p == "all":
                return "all (pure MATLAB, no MEX files)", None
            return p, None
        if set(platforms) == {"all"}:
            return "all (pure MATLAB, no MEX files)", None
        return None, f"multiple platforms {platforms} -- manual submission required"
    elif isinstance(platforms, str):
        if platforms == "all":
            return "all (pure MATLAB, no MEX files)", None
        return platforms, None

    return "all (pure MATLAB, no MEX files)", None


def create_issue(name, owner, repo, tag, platform_string, dry_run):
    """Create a submission issue via gh CLI."""
    title = f"Submit: {name}"
    body = (
        f"### Repository URL\n\n"
        f"https://github.com/{owner}/{repo}\n\n"
        f"### Release tag\n\n"
        f"{tag}\n\n"
        f"### Platform\n\n"
        f"{platform_string}"
    )

    if dry_run:
        log(f"  DRY RUN: Would create issue '{title}'")
        log(f"  Body:\n{body}")
        return True

    try:
        result = subprocess.run(
            [
                "gh", "issue", "create",
                "--title", title,
                "--body", body,
                "--label", "submit-package,auto-discovered",
                "--repo", REGISTRY_REPO,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log(f"  Created issue: {result.stdout.strip()}")
            return True
        else:
            log(f"  Failed to create issue: {result.stderr.strip()}")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"  Error creating issue: {e}")
        return False


def ensure_label(dry_run):
    """Ensure the auto-discovered label exists."""
    if dry_run:
        log("DRY RUN: Would ensure 'auto-discovered' label exists")
        return

    try:
        subprocess.run(
            [
                "gh", "label", "create", "auto-discovered",
                "--repo", REGISTRY_REPO,
                "--description", "Package found via autodiscovery",
                "--color", "bfd4f2",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def main():
    dry_run = bool(os.environ.get("DRY_RUN", ""))

    if dry_run:
        log("Running in DRY RUN mode")

    # Ensure label exists
    ensure_label(dry_run)

    # Load registered package names
    registered = load_registered_names()
    log(f"Found {len(registered)} registered packages")

    # Search for repos with the topic
    log("Searching GitHub for repos with topic:tbxmanager-package ...")
    repos = search_topic_repos()
    log(f"Found {len(repos)} repositories")

    issues_created = 0

    for repo_info in repos:
        if issues_created >= MAX_ISSUES_PER_RUN:
            log(f"Reached max issues per run ({MAX_ISSUES_PER_RUN}), stopping")
            break

        owner = repo_info["owner"]["login"]
        repo_name = repo_info["name"]
        full_name = f"{owner}/{repo_name}"

        log(f"Processing {full_name} ...")

        # Skip forks
        if repo_info.get("fork", False):
            log(f"  Skipping {full_name}: fork")
            continue

        # Skip archived repos
        if repo_info.get("archived", False):
            log(f"  Skipping {full_name}: archived")
            continue

        time.sleep(API_DELAY_SECONDS)

        # Fetch tbxmanager.json
        default_branch = repo_info.get("default_branch", "main")
        pkg_data = fetch_tbxmanager_json(owner, repo_name, default_branch)
        if pkg_data is None:
            continue

        # Extract package name
        name = pkg_data.get("name")
        if not name:
            log(f"  Skipping {full_name}: no 'name' field in tbxmanager.json")
            continue

        # Check if already registered
        if name in registered:
            log(f"  Skipping {full_name}: package '{name}' already registered")
            continue

        # Get latest release
        time.sleep(API_DELAY_SECONDS)
        release = get_latest_release(owner, repo_name)
        if release is None:
            continue

        tag = release.get("tag_name")
        if not tag:
            log(f"  Skipping {full_name}: release has no tag_name")
            continue

        # Check for archive asset
        assets = release.get("assets", [])
        archive_assets = [
            a for a in assets
            if a["name"].endswith(".zip") or a["name"].endswith(".tar.gz")
        ]
        if not archive_assets:
            log(f"  Skipping {full_name}: no .zip or .tar.gz asset in release {tag}")
            continue

        # Determine platform
        platform_string, skip_reason = determine_platform(pkg_data)
        if skip_reason:
            log(f"  Skipping {full_name}: {skip_reason}")
            continue

        # Check for existing open issue
        if has_open_issue(name):
            log(f"  Skipping {full_name}: open submission issue already exists")
            continue

        # Create submission issue
        time.sleep(API_DELAY_SECONDS)
        if create_issue(name, owner, repo_name, tag, platform_string, dry_run):
            issues_created += 1

    log(f"Done. Created {issues_created} issues.")


if __name__ == "__main__":
    main()
