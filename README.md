# tbxmanager-registry

Community package registry for [tbxmanager](https://github.com/MarekWadinger/tbxmanager) — the MATLAB package manager.

## For Package Authors

The easiest way to publish is with the [tbxmanager-publish](https://github.com/MarekWadinger/tbxmanager-publish) GitHub Action. See the [Quick Start](https://marekwadinger.github.io/tbxmanager/quick-start-authors/).

### Manual Submission

1. Fork this repo
2. Create `packages/your-package/package.json` (see format below)
3. Open a pull request
4. CI validates automatically
5. Once merged, the package appears in the registry

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

Pull requests are automatically validated:

- Valid JSON syntax
- Required fields (name, description, versions)
- Package name matches directory name
- Valid semver versions
- Valid platform names (win64, maci64, maca64, glnxa64, all)
- HTTPS download URLs
- SHA256 hash format (64-char hex or null)
- URL reachability (HEAD request)

## License

[MIT](LICENSE)
