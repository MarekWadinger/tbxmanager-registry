# tbxmanager-registry

Community package registry for [tbxmanager](https://tbxmanager.com) — the MATLAB package manager.

Packages are served as `index.json` via GitHub Pages.

## Submit a Package

The easiest way — fill in a form:

1. Add a `tbxmanager.json` to your repository root ([how](https://tbxmanager.com/quick-start-authors))
2. Create a GitHub Release and attach a `.zip` archive of your package
3. **[Submit here](https://github.com/MarekWadinger/tbxmanager-registry/issues/new?template=submit-package.yml)** — fill in your repo URL and release tag

A bot will fetch your metadata, download the archive, compute SHA256, and open a PR. A maintainer reviews and merges it.

### Autodiscovery

Add the `tbxmanager-package` topic to your GitHub repository. A weekly bot will find it and create a submission automatically.

### Alternative: Manual Pull Request

Fork this repo, add `packages/your-package/package.json`, and open a PR. See [Creating Packages](https://tbxmanager.com/creating-packages) for the format.

### For Registry Collaborators

Automate publishing with the [tbxmanager-publish](https://github.com/MarekWadinger/tbxmanager-publish) GitHub Action. Requires write access to this repo.

## Package Format

Each package lives at `packages/[name]/package.json`:

```json
{
  "name": "my-toolbox",
  "description": "A useful MATLAB toolbox",
  "homepage": "https://github.com/you/my-toolbox",
  "license": "MIT",
  "authors": ["Your Name <your@email.com>"],
  "versions": {
    "1.0.0": {
      "matlab": ">=R2022a",
      "dependencies": {},
      "platforms": {
        "all": {
          "url": "https://github.com/you/my-toolbox/releases/download/v1.0.0/my-toolbox-all.zip",
          "sha256": "64-char-hex-hash"
        }
      },
      "released": "2026-03-29"
    }
  }
}
```

## How It Works

- Each `packages/[name]/package.json` contains metadata for one package (all versions)
- On merge, CI rebuilds `index.json` — a combined index of all packages
- `index.json` is served via GitHub Pages
- The MATLAB client fetches this index to discover and install packages

## CI Checks

- **PR validation** — JSON schema, URL liveness, SHA256 format
- **Index rebuild** — `index.json` updated automatically on merge
- **Weekly link check** — broken URLs create issues
- **Autodiscovery** — weekly scan for `tbxmanager-package` tagged repos

## License

[MIT](LICENSE)
