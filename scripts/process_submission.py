#!/usr/bin/env python3
"""Process a package submission from a GitHub issue.

Parses the issue body, fetches tbxmanager.json from the author's repo,
downloads the release asset, computes SHA256, and generates the registry
package.json entry.

Reads from environment variables:
    ISSUE_BODY: The GitHub issue body text
    ISSUE_NUMBER: The issue number
    ISSUE_AUTHOR: The issue author's GitHub username
    GH_TOKEN: GitHub token for API requests (optional, avoids rate limits)
"""

import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# Import from sibling module
sys.path.insert(0, str(Path(__file__).parent))
import convert_to_registry  # noqa: E402

REPO_URL_PATTERN = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def make_request(url, method="GET", headers=None):
    """Make an HTTP(S) request with optional auth token."""
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


def parse_issue_body(body):
    """Parse GitHub issue form format into a dict.

    Issue forms produce markdown with ### headings followed by values.
    Returns dict with keys: repo_url, release_tag, platform.
    """
    result = {}
    field_map = {
        "Repository URL": "repo_url",
        "Release tag": "release_tag",
        "Platform": "platform",
    }

    lines = body.strip().split("\n")
    current_key = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            heading = stripped[4:].strip()
            current_key = field_map.get(heading)
        elif current_key and stripped and stripped != "_No response_":
            result[current_key] = stripped
            current_key = None

    missing = []
    for label, key in field_map.items():
        if key not in result or not result[key]:
            missing.append(label)

    if missing:
        raise ValueError(
            "Could not parse the following fields from the issue: "
            + ", ".join(f"**{m}**" for m in missing)
            + ".\n\nPlease make sure you filled out all required fields in the issue form."
        )

    return result


def parse_repo_url(url):
    """Extract (owner, repo) from a GitHub URL.

    Raises ValueError with user-friendly message on invalid format.
    """
    match = REPO_URL_PATTERN.match(url.strip())
    if not match:
        raise ValueError(
            f"Invalid repository URL: `{url}`\n\n"
            "Expected format: `https://github.com/owner/repo`"
        )
    return match.group(1), match.group(2)


def fetch_tbxmanager_json(owner, repo, tag):
    """Fetch tbxmanager.json from the repo at the given tag.

    Returns parsed JSON dict.
    Raises ValueError with user-friendly message on failure.
    """
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{tag}/tbxmanager.json"
    try:
        resp = make_request(url)
        return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(
                f"Could not find `tbxmanager.json` in your repository at tag `{tag}`. "
                "Make sure the file exists in your repo root.\n\n"
                "See [Creating tbxmanager.json](https://tbxmanager.com/quick-start-authors) for help."
            ) from e
        raise ValueError(
            f"Failed to fetch `tbxmanager.json` (HTTP {e.code}). "
            "Please check that the repository and tag are correct."
        ) from e
    except json.JSONDecodeError as e:
        raise ValueError(
            "`tbxmanager.json` contains invalid JSON. "
            "Please fix the syntax errors and try again."
        ) from e


def get_release_asset_url(owner, repo, tag, platform):
    """Find a .zip or .tar.gz asset URL from the GitHub release.

    For non-"all" platforms, tries to match the platform name in the filename.
    Returns the browser_download_url of the first matching asset.
    Raises ValueError with user-friendly message on failure.
    """
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
    try:
        resp = make_request(api_url)
        release = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(
                f"No release found for tag `{tag}`. "
                "Please [create a GitHub Release]"
                "(https://docs.github.com/en/repositories/releasing-projects-on-github/"
                "managing-releases-in-a-repository) first."
            ) from e
        raise ValueError(
            f"Failed to fetch release information (HTTP {e.code})."
        ) from e

    assets = release.get("assets", [])
    archive_assets = [
        a
        for a in assets
        if a["name"].endswith(".zip") or a["name"].endswith(".tar.gz")
    ]

    if not archive_assets:
        raise ValueError(
            "No `.zip` or `.tar.gz` archive attached to the release. "
            "Please attach an archive file to your "
            f"[GitHub Release](https://github.com/{owner}/{repo}/releases/tag/{tag})."
        )

    # For platform-specific packages, try to match platform in filename
    if platform != "all":
        for asset in archive_assets:
            if platform in asset["name"]:
                return asset["browser_download_url"]

    # Fall back to first archive
    return archive_assets[0]["browser_download_url"]


def download_and_hash(url):
    """Download a file and compute its SHA256 hash.

    Returns hex digest string.
    """
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


def main():
    """Process the package submission."""
    try:
        body = os.environ.get("ISSUE_BODY", "")
        if not body:
            raise ValueError(
                "Issue body is empty. Please fill out the submission form."
            )

        # 1. Parse issue body
        fields = parse_issue_body(body)

        # 2. Parse repo URL
        owner, repo = parse_repo_url(fields["repo_url"])
        tag = fields["release_tag"].strip()

        # 3. Extract platform (strip parenthetical)
        platform = fields["platform"].split("(")[0].strip()

        # 4. Fetch tbxmanager.json
        pkg = fetch_tbxmanager_json(owner, repo, tag)

        # 5. Validate (set placeholder URLs so platform validation passes —
        #    real URLs are provided as overrides to convert())
        for plat in list(pkg.get("platforms", {})):
            if not isinstance(pkg["platforms"][plat], str) or not pkg["platforms"][plat].startswith("https://"):
                pkg["platforms"][plat] = "https://placeholder"
        errors = convert_to_registry.validate_input(pkg)
        if errors:
            error_list = "\n".join(f"- {e}" for e in errors)
            raise ValueError(
                f"Validation errors in `tbxmanager.json`:\n\n{error_list}\n\n"
                "See [Creating tbxmanager.json](https://tbxmanager.com/quick-start-authors) for help."
            )

        name = pkg["name"]

        # Extra name validation for user-friendly message
        if not convert_to_registry.NAME_PATTERN.match(name):
            raise ValueError(
                f"Invalid package name: `{name}`. "
                "Names must be lowercase, using only letters, numbers, hyphens, and underscores."
            )

        # 6. Get release asset URL
        asset_url = get_release_asset_url(owner, repo, tag, platform)

        # 7. Download and hash
        print(f"Downloading {asset_url} ...")
        sha256 = download_and_hash(asset_url)
        print(f"SHA256: {sha256}")

        # 8. Convert to registry format
        # Override the platform URL and SHA256 with the actual release asset
        url_overrides = {platform: asset_url}
        sha256_overrides = {platform: sha256}

        top_level, version_key, version_entry = convert_to_registry.convert(
            pkg,
            sha256_overrides=sha256_overrides,
            url_overrides=url_overrides,
        )

        # 9. Merge or create
        pkg_dir = Path("packages") / name
        pkg_file = pkg_dir / "package.json"

        if pkg_file.exists():
            with open(pkg_file) as f:
                existing = json.load(f)
            result, warnings = convert_to_registry.merge_into_existing(
                existing, top_level, version_key, version_entry
            )
            for w in warnings:
                print(f"[WARN] {w}", file=sys.stderr)
        else:
            result = convert_to_registry.build_new_entry(
                top_level, version_key, version_entry
            )

        # 10. Write package.json
        pkg_dir.mkdir(parents=True, exist_ok=True)
        with open(pkg_file, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            f.write("\n")

        print(f"Wrote {pkg_file}: {name}@{version_key}")

        # 11. Write outputs for workflow
        Path("/tmp/submission_pkg_name").write_text(name)
        Path("/tmp/submission_version").write_text(version_key)

    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] {error_msg}", file=sys.stderr)
        Path("/tmp/submission_error").write_text(error_msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
