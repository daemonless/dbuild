# dbuild

Build, test, and push FreeBSD OCI container images.

dbuild is a build tool for [daemonless](https://github.com/daemonless) container images. It reads a project directory containing `Containerfile` templates and an optional `.daemonless/config.yaml`, then handles the full image lifecycle: build, integration test, SBOM generation, and registry push.

It runs the same way locally and in CI. GitHub Actions and Woodpecker call dbuild — dbuild handles the FreeBSD-specific logic (variant detection, architecture mapping, OCI labels, registry auth, skip directives, SBOM generation) so CI workflows stay generic.

```
GitHub Actions / Woodpecker / local
  └── dbuild detect     →  build matrix
  └── dbuild build      →  podman build per variant
  └── dbuild test       →  container integration tests
  └── dbuild sbom       →  CycloneDX SBOM
  └── dbuild push       →  push to ghcr.io
  └── dbuild manifest   →  multi-arch manifest lists
```

## Requirements

- Python 3.11+
- PyYAML
- Podman (with `doas` on FreeBSD when not root)
- Buildah (for OCI label application and SBOM generation)

Optional:

| Feature | Packages |
|---------|----------|
| Multi-arch manifests | `skopeo` |
| Compose testing | `podman-compose` |
| SBOM generation | `trivy` |
| Screenshot testing | `py311-selenium`, `py311-scikit-image`, `chromium`, `chromedriver` |

## Install

```sh
pip install .

# With dev tools (pytest + ruff)
pip install ".[dev]"
```

Or use without installing:

```sh
PYTHONPATH=/path/to/dbuild python3 -m dbuild info
```

## Quick Start

```sh
# Scaffold a new image project
mkdir myapp && cd myapp
dbuild init

# From an existing image repo (e.g. radarr/)
cd radarr

# See what would be built
dbuild info

# Build all variants
dbuild build

# Build one variant
dbuild build --variant pkg

# Test built images
dbuild test

# Build and push in one step
dbuild build --push
```

## Project Layout

dbuild expects this directory structure in each image repo:

```
myapp/
  Containerfile           # upstream binary build (:latest tag)
  Containerfile.pkg       # FreeBSD package build (:pkg tag) - optional
  root/                   # files copied into the container
  .daemonless/
    config.yaml           # build + test configuration - optional
    baseline.png          # screenshot baseline - optional
    baselines/            # per-variant baselines (baseline-pkg.png) - optional
    compose.yaml          # multi-service test stack - optional
```

If no `config.yaml` exists, dbuild auto-detects variants from the Containerfiles present:

| File | Variant tag |
|------|------------|
| `Containerfile` | `:latest` |
| `Containerfile.<suffix>` | `:<suffix>` (e.g. `Containerfile.pkg` → `:pkg`) |

## Configuration

`.daemonless/config.yaml` (or `.dbuild.yaml`):

```yaml
# Image type: "app" (default) or "base"
type: app

# Build configuration
build:
  auto_version: true                # extract version from built image
  pkg_name: myapp                   # FreeBSD package name (for pkg variants)
  architectures: [amd64, aarch64]   # target architectures (default: [amd64])
  variants:
    - tag: latest
      containerfile: Containerfile
      default: true
    - tag: pkg
      containerfile: Containerfile.pkg
      args:
        BASE_VERSION: "15-quarterly"
      aliases: [pkg-quarterly]
    - tag: pkg-latest
      containerfile: Containerfile.pkg
      args:
        BASE_VERSION: "15-latest"

# Container integration test configuration
cit:
  mode: health                      # shell | port | health | screenshot
  port: 8080
  health: /api/health               # health endpoint path
  wait: 120                         # startup timeout (seconds)
  ready: "Server started"           # log ready-pattern (regex)
  https: false                      # use HTTPS for health checks
  screenshot: /web                  # custom screenshot URL path
  screenshot_wait: 5                # seconds to wait before screenshot
  compose: false                    # use podman-compose for testing
  annotations:                      # container annotations for testing
    - "org.freebsd.jail.allow.mlock=true"
```

## Commands

### `dbuild build`

Build container images for all (or selected) variants.

```sh
dbuild build                    # all variants
dbuild build --variant latest   # one variant
dbuild build --arch aarch64     # cross-architecture
dbuild build --push             # build + push to registry
```

Build output is tagged as `{registry}/{image}:build-{tag}` (e.g. `ghcr.io/daemonless/radarr:build-pkg`).

### `dbuild test`

Run container integration tests against built images.

```sh
dbuild test                         # test all variants
dbuild test --variant pkg           # test one variant
dbuild test --json result.json      # write JSON result
```

Test modes are cumulative — each includes all tests below it:

| Mode | Tests |
|------|-------|
| `screenshot` | health + capture screenshot + visual verification |
| `health` | port + HTTP endpoint responds (2xx/4xx = ok) |
| `port` | shell + TCP port is listening |
| `shell` | container starts, can exec into it |

**Auto-detection**: If no mode is set in config, dbuild picks the highest applicable mode:
- `baseline.png` exists and screenshot deps installed → `screenshot`
- Health endpoint defined (config or OCI label) → `health`
- Port defined (config or OCI label) → `port`
- Otherwise → `shell`

If screenshot dependencies aren't installed, it downgrades automatically and tells you what's missing.

**OCI labels**: dbuild reads `io.daemonless.port`, `io.daemonless.healthcheck-url`, and `org.freebsd.jail.*` labels from the built image. Config values override labels.

### `dbuild push`

Tag and push built images to the configured registry.

```sh
dbuild push
dbuild push --variant latest
```

Supports Docker Hub mirroring when `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` are set.

### `dbuild sbom`

Generate a CycloneDX SBOM for built images. Uses trivy for application-level dependencies and `pkg query` for FreeBSD packages.

```sh
dbuild sbom
dbuild sbom --variant pkg
```

Output is written to `sbom-results/{image}-{tag}-sbom.json`.

### `dbuild manifest`

Create and push multi-architecture manifest lists. Only useful when `architectures` has more than one entry.

```sh
dbuild manifest
```

For each variant tag (plus aliases), creates a manifest referencing the architecture-specific images:
- `latest` → `latest` (amd64) + `latest-arm64` (aarch64)
- `pkg` → `pkg` (amd64) + `pkg-arm64` (aarch64)

### `dbuild detect`

Output the build matrix as JSON for CI integration.

```sh
dbuild detect                       # plain JSON to stdout
dbuild detect --format github       # write to $GITHUB_OUTPUT
dbuild detect --format woodpecker   # JSON to stdout
```

### `dbuild info`

Human-readable overview of detected configuration.

```sh
$ dbuild info
=== Image: ghcr.io/daemonless/radarr ===
[info] Type: app
[info] Architectures: amd64
[info] Variants: 3
[info]   latest (amd64) -> Containerfile
[info]   pkg (amd64) -> Containerfile.pkg
[info]     BASE_VERSION=15-quarterly
[info]   pkg-latest (amd64) -> Containerfile.pkg
[info]     BASE_VERSION=15-latest
[info] Test: mode= port=7878
```

### `dbuild init`

Scaffold a new dbuild project with starter files.

```sh
dbuild init                     # config.yaml + Containerfile
dbuild init --github            # + GitHub Actions workflow
dbuild init --woodpecker        # + Woodpecker CI pipeline
```

## Global Options

```
--variant TAG     filter to a single variant
--arch ARCH       override target architecture (amd64, aarch64, riscv64)
--registry URL    override the container registry
--push            push images after building
-v, --verbose     enable debug logging
```

## Skip Directives

Add `[skip <step>]` to a commit message to skip CI steps:

```
fix: update config [skip test]              # skip testing
feat: bump version [skip push]              # skip all pushes
chore: docs only [skip push:dockerhub]      # skip Docker Hub mirror only
chore: update readme [skip sbom]            # skip SBOM generation
fix: ci [skip test] [skip sbom]             # skip multiple steps
```

`[skip push]` also skips `push:dockerhub`. `[skip push:dockerhub]` only skips the Docker Hub mirror.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DBUILD_REGISTRY` | `ghcr.io/daemonless` | Default container registry |
| `GITHUB_TOKEN` | | Forwarded as build secret + registry auth |
| `GITHUB_ACTOR` | | Registry login username |
| `DOCKERHUB_USERNAME` | | Enable Docker Hub mirroring |
| `DOCKERHUB_TOKEN` | | Docker Hub auth token |
| `CHROME_BIN` | `/usr/local/bin/chrome` | Chrome binary for screenshots |
| `CHROMEDRIVER_BIN` | `/usr/local/bin/chromedriver` | ChromeDriver binary |
| `SCREENSHOT_SIZE` | `1920,1080` | Screenshot viewport size |
| `VERIFY_SSIM_THRESHOLD` | `0.95` | SSIM threshold for baseline comparison |

## CI Integration

### GitHub Actions

```sh
dbuild init --github
```

This generates `.github/workflows/build.yaml` which:
1. Runs `dbuild detect --format github` to generate the build matrix
2. Spins up a FreeBSD VM per matrix entry (via vmactions/freebsd-vm)
3. Runs `dbuild build → test → sbom → push` inside the VM

### Woodpecker CI

```sh
dbuild init --woodpecker
```

Generates `.woodpecker.yaml`. The pipeline fetches `dbuild-ci.sh` which runs the full build pipeline on the native FreeBSD agent.

### Local development

```sh
# Full pipeline (same as CI)
dbuild build --variant latest
dbuild test --variant latest
dbuild sbom --variant latest

# Transfer to another host
doas podman save ghcr.io/daemonless/myapp:build-latest | ssh jupiter doas podman load
```

## JSON Test Output

`dbuild test --json result.json` writes:

```json
{
  "image": "ghcr.io/daemonless/radarr:build-pkg",
  "mode": "health",
  "timestamp": "2026-02-08T17:01:11Z",
  "shell": "pass",
  "port": "pass",
  "health": "pass",
  "screenshot": "skip",
  "verify": "skip",
  "result": "pass"
}
```

Each test is `"pass"`, `"fail"`, or `"skip"`.

## Development

```sh
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check dbuild/ tests/
```

## License

BSD-2-Clause
