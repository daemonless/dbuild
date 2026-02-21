""".daemonless/config.yaml parsing and auto-detection.

This module has ZERO side effects.  It reads YAML (or the filesystem for
auto-detection) and returns frozen dataclasses.  It does not run podman,
know about CI, or touch the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# Suffixes to ignore when auto-detecting variants from Containerfile.*
_IGNORE_SUFFIXES: set[str] = {".j2", ".bak", ".orig", ".swp", ".tmp"}

# Global config path — shared variant templates for all repos.
_GLOBAL_CONFIG_PATH = Path("/usr/local/etc/daemonless.yaml")

# ── Dataclasses ──────────────────────────────────────────────────────

@dataclass
class TestConfig:
    """CIT test configuration."""

    mode: str = ""
    port: int | None = None
    health: str | None = None
    wait: int = 120
    ready: str | None = None
    screenshot_wait: int | None = None
    screenshot_path: str | None = None
    https: bool = False
    compose: bool = False
    annotations: list[str] = field(default_factory=list)


@dataclass
class Variant:
    """A single build variant (e.g. :latest, :pkg, :15-quarterly)."""

    tag: str
    containerfile: str = "Containerfile"
    args: dict[str, str] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    auto_version: bool = False
    default: bool = False
    pkg_name: str | None = None


@dataclass
class Metadata:
    """Rich metadata for documentation and discovery (from x-daemonless)."""

    title: str = ""
    description: str = ""
    category: str = "Apps"
    upstream_url: str = ""
    web_url: str = ""
    freshports_url: str = ""
    user: str = "bsd"
    upstream_binary: bool = True
    icon: str = ":material-docker:"
    notes: str = ""
    healthcheck: dict[str, Any] | None = None
    docs: dict[str, Any] | str = field(default_factory=dict)


@dataclass
class Config:
    """Top-level build configuration for an image."""

    image: str
    registry: str
    type: str = "app"
    variants: list[Variant] = field(default_factory=list)
    test: TestConfig | None = None
    architectures: list[str] = field(default_factory=lambda: ["amd64"])
    metadata: Metadata = field(default_factory=Metadata)

    # Merged service data (from compose.yaml or config.yaml)
    env: list[dict[str, Any]] = field(default_factory=list)
    volumes: list[dict[str, Any]] = field(default_factory=list)
    ports: list[dict[str, Any]] = field(default_factory=list)

    @property
    def full_image(self) -> str:
        """Return the fully-qualified image reference (registry/image)."""
        return f"{self.registry}/{self.image}"


# ── Loading ──────────────────────────────────────────────────────────

_LEGACY_CONFIG_PATHS = [
    ".dbuild.yaml",
    ".daemonless/config.yaml",
]


def _detect_registry() -> str:
    """Detect registry from DBUILD_REGISTRY env or derive from git remote.

    Falls back to ``ghcr.io/<org>`` where ``<org>`` is extracted from
    the git remote URL (e.g. ``github.com/daemonless/radarr`` → ``ghcr.io/daemonless``).
    If the remote cannot be parsed, returns ``localhost`` so builds still work locally.
    """
    env = os.environ.get("DBUILD_REGISTRY")
    if env:
        return env
    org = _git_remote_org()
    if org:
        return f"ghcr.io/{org}"
    return "localhost"


def _git_remote_org() -> str | None:
    """Extract the org/owner from the git remote origin URL.

    Supports HTTPS (``https://github.com/org/repo``) and SSH
    (``git@github.com:org/repo.git``) formats.
    """
    import re
    import subprocess
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        # SSH: git@github.com:org/repo.git
        m = re.match(r"git@[^:]+:([^/]+)/", url)
        if m:
            return m.group(1)
        # HTTPS: https://github.com/org/repo
        m = re.match(r"https?://[^/]+/([^/]+)/", url)
        if m:
            return m.group(1)
    except FileNotFoundError:
        pass
    return None


def _detect_image_name(base: Path) -> str:
    """Derive image name from directory name."""
    return base.name


def _auto_detect_variants(
    base: Path,
    pkg_name: str | None = None,
    auto_version: bool = False,
    ignore: list[str] | None = None,
) -> list[Variant]:
    """Auto-detect variants from Containerfiles present in *base*.

    Scans for ``Containerfile`` (tag: latest) and ``Containerfile.*``
    (tag: suffix).  No hardcoded args or version assumptions.

    Parameters
    ----------
    base:
        Project directory.
    pkg_name:
        Default pkg_name from ``build.pkg_name`` in config.
    auto_version:
        Default auto_version from ``build.auto_version`` in config.
    ignore:
        Additional filenames to skip (merged with ``_IGNORE_SUFFIXES``).
    """
    ignore_names: set[str] = set(ignore) if ignore else set()
    variants: list[Variant] = []

    if (base / "Containerfile").is_file():
        variants.append(Variant(
            tag="latest",
            containerfile="Containerfile",
            default=True,
            pkg_name=pkg_name,
            auto_version=auto_version,
        ))

    for cf in sorted(base.glob("Containerfile.*")):
        # Skip files whose suffix matches the built-in ignore set
        ext = cf.suffix  # e.g. ".j2", ".pkg"
        if ext in _IGNORE_SUFFIXES:
            continue
        # Skip files explicitly listed in build.ignore
        if cf.name in ignore_names:
            continue
        suffix = cf.name.split(".", 1)[1]
        variants.append(Variant(
            tag=suffix,
            containerfile=cf.name,
            pkg_name=pkg_name,
            auto_version=auto_version,
        ))

    return variants


def _load_global_config(path: Path | None = None) -> dict[str, Any]:
    """Load the global daemonless config if it exists.

    Returns an empty dict when the file is missing or PyYAML is unavailable.
    """
    if path is None:
        path = _GLOBAL_CONFIG_PATH
    if yaml is None or not path.is_file():
        return {}
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _global_extra_variants(base: Path, global_data: dict[str, Any]) -> list[Variant]:
    """Return extra variants from the global config, filtered by existing Containerfiles.

    These are appended to auto-detected variants to add variants that
    auto-detection cannot discover (e.g. ``pkg-latest`` with custom args).
    """
    raw_variants = global_data.get("build", {}).get("variants", [])
    if not raw_variants:
        return []

    variants: list[Variant] = []
    for v in raw_variants:
        cf = v.get("containerfile", "Containerfile")
        if not (base / cf).is_file():
            continue
        variants.append(
            Variant(
                tag=str(v["tag"]),
                containerfile=cf,
                args=v.get("args", {}),
                aliases=v.get("aliases", []),
                auto_version=v.get("auto_version", False),
                default=v.get("default", False),
                pkg_name=v.get("pkg_name"),
            )
        )

    return variants


def _parse_service_data(
    data: dict[str, Any], compose_data: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract env, volumes, and ports from compose.yaml or legacy config."""
    env = []
    volumes = []
    ports = []

    # 1. Try compose.yaml (Truth)
    services = compose_data.get("services", {})
    if services:
        service = next(iter(services.values()))

        # Env
        raw_env = service.get("environment", [])
        if isinstance(raw_env, dict):
            for k, v in raw_env.items():
                env.append({"name": str(k), "default": str(v)})
        else:
            for e in raw_env:
                if "=" in e:
                    k, v = e.split("=", 1)
                    env.append({"name": k.strip(), "default": v.strip()})
                else:
                    env.append({"name": e.strip(), "default": ""})

        # Volumes
        for v in service.get("volumes", []):
            if isinstance(v, str):
                parts = v.split(":")
                src = parts[0]
                tgt = parts[1] if len(parts) > 1 else parts[0]
            else:
                src = v.get("source", "")
                tgt = v.get("target", "")
            volumes.append({"source": src, "target": tgt})

        # Ports
        for p in service.get("ports", []):
            if isinstance(p, str):
                parts = p.split(":")
                pub = parts[0]
                tgt = parts[1] if len(parts) > 1 else parts[0]
                proto = "tcp"
                if "/" in tgt:
                    tgt, proto = tgt.split("/", 1)
            else:
                pub = str(p.get("published", ""))
                tgt = str(p.get("target", ""))
                proto = p.get("protocol", "tcp")
            ports.append({"published": pub, "target": tgt, "protocol": proto})

    # 2. Fallback to legacy config.yaml (if compose missing or lists empty)
    if not env:
        for e in data.get("env", []):
            env.append({"name": e.get("name", ""), "default": str(e.get("default", ""))})

    if not volumes:
        for v in data.get("volumes", []):
            volumes.append({"source": v.get("source", ""), "target": v.get("path", "")})

    if not ports:
        for p in data.get("ports", []):
            ports.append({
                "published": str(p.get("port", "")),
                "target": str(p.get("port", "")),
                "protocol": p.get("protocol", "tcp")
            })

    return env, volumes, ports


def _parse_metadata(data: dict[str, Any], app_name: str) -> Metadata:
    """Parse the ``x-daemonless:`` section of the config file."""
    meta = data.get("x-daemonless", {})
    return Metadata(
        title=meta.get("title", app_name.title()),
        description=meta.get("description", ""),
        category=meta.get("category", "Apps"),
        upstream_url=meta.get("upstream_url", ""),
        web_url=meta.get("web_url", ""),
        freshports_url=meta.get("freshports_url", ""),
        user=meta.get("user", "bsd"),
        upstream_binary=meta.get("upstream_binary", True),
        icon=meta.get("icon", ":material-docker:"),
        notes=meta.get("notes", ""),
        healthcheck=meta.get("healthcheck"),
        docs=meta.get("docs", {}),
    )


def _parse_test_config(data: dict[str, Any], compose_data: dict[str, Any] | None = None) -> TestConfig | None:
    """Parse the ``cit:`` section and merge with compose.yaml metadata."""
    # 1. Start with values from cit: section (legacy/override)
    cit = data.get("cit", {})

    mode = cit.get("mode", "")
    port = cit.get("port")
    health_path = cit.get("health")
    wait = cit.get("wait", 120)
    ready = cit.get("ready")
    screenshot_wait = cit.get("screenshot_wait")
    screenshot_path = cit.get("screenshot")
    https = cit.get("https", False)
    compose = cit.get("compose", False)
    annotations = []

    # 2. Merge annotations from cit: section
    def normalize_anno(a: str) -> str:
        if "=" in a:
            k, v = a.split("=", 1)
            return f"{k.strip()}={v.strip()}"
        return a.strip()

    cit_annotations = cit.get("annotations", [])
    if isinstance(cit_annotations, list):
        for a in cit_annotations:
            norm = normalize_anno(a)
            if norm not in annotations:
                annotations.append(norm)

    # 3. Pull from x-daemonless: healthcheck if not set in cit:
    if compose_data:
        meta = compose_data.get("x-daemonless", {})
        health = meta.get("healthcheck")
        if health:
            if not port:
                port = health.get("port")
            if not health_path:
                health_path = health.get("path", "/")
            if not ready:
                ready = health.get("ready")
            if mode == "":
                mode = "http" if port else "none"

        # 4. Extract annotations from the first service in compose.yaml
        services = compose_data.get("services", {})
        if services:
            first_service = next(iter(services.values()))
            raw_annotations = first_service.get("annotations", [])
            service_annotations = []
            if isinstance(raw_annotations, dict):
                for k, v in raw_annotations.items():
                    service_annotations.append(f"{k.strip()}={v.strip()}")
            elif isinstance(raw_annotations, list):
                service_annotations = [normalize_anno(a) for a in raw_annotations]

            # Merge unique annotations
            for sa in service_annotations:
                if sa not in annotations:
                    annotations.append(sa)

    if not mode and not port and not annotations and not cit:
        return None

    return TestConfig(
        mode=mode,
        port=port,
        health=health_path,
        wait=wait,
        ready=ready,
        screenshot_wait=screenshot_wait,
        screenshot_path=screenshot_path,
        https=https,
        compose=compose,
        annotations=annotations,
    )


def _parse_variants(data: dict[str, Any]) -> list[Variant]:
    """Parse the ``build.variants:`` section of the config file."""
    build_section = data.get("build", {})
    raw_variants = build_section.get("variants", [])
    build_auto_version = build_section.get("auto_version", False)
    variants: list[Variant] = []
    for v in raw_variants:
        variants.append(
            Variant(
                tag=str(v["tag"]),
                containerfile=v.get("containerfile", "Containerfile"),
                args=v.get("args", {}),
                aliases=v.get("aliases", []),
                auto_version=v.get("auto_version", build_auto_version),
                default=v.get("default", False),
                pkg_name=v.get("pkg_name"),
            )
        )
    return variants


def load(base: Path | None = None) -> Config:
    """Load configuration from file or auto-detect.

    Configuration is merged from two primary sources:
    1. compose.yaml: Truth for rich metadata and deployment annotations.
    2. .daemonless/config.yaml: Truth for build variants and CIT overrides.

    Parameters
    ----------
    base:
        Project root directory.  Defaults to the current working directory.
    """
    if base is None:
        base = Path.cwd()
    base = Path(base)

    image_name = _detect_image_name(base)
    registry = _detect_registry()

    global_data = _load_global_config()

    # Load compose.yaml (Truth for metadata)
    compose_data: dict[str, Any] = {}
    compose_path = base / "compose.yaml"
    if compose_path.is_file() and yaml is not None:
        with open(compose_path) as fh:
            compose_data = yaml.safe_load(fh) or {}

    # Load legacy config file (Truth for build settings)
    local_data: dict[str, Any] = {}
    config_file = None
    for name in _LEGACY_CONFIG_PATHS:
        candidate = base / name
        if candidate.is_file():
            config_file = candidate
            break

    if config_file is not None:
        if yaml is None:
            from dbuild import log
            log.warn(f"Config file {config_file} found but PyYAML is not installed.")
        else:
            with open(config_file) as fh:
                local_data = yaml.safe_load(fh) or {}

    # Parse build sections
    local_build = local_data.get("build", {})
    global_build = global_data.get("build", {})

    build_pkg_name = local_build.get("pkg_name")
    build_auto_version = local_build.get("auto_version", False)
    build_ignore: list[str] = local_build.get("ignore", [])

    # Resolve variants: local explicit > auto-detect + global extras
    variants = _parse_variants(local_data)
    if not variants:
        variants = _auto_detect_variants(
            base, build_pkg_name, build_auto_version, ignore=build_ignore,
        )
        if global_data:
            existing_tags = {v.tag for v in variants}
            for gv in _global_extra_variants(base, global_data):
                if gv.tag not in existing_tags:
                    variants.append(gv)

    # Merge other fields: local overrides global
    architectures = local_build.get(
        "architectures",
        global_build.get("architectures", ["amd64"]),
    )
    image_type = local_data.get("type", global_data.get("type", "app"))

    # CIT config (Merge legacy + compose)
    test = _parse_test_config(local_data, compose_data)

    # Metadata (Prioritize x-daemonless in compose.yaml)
    metadata = _parse_metadata(compose_data or local_data, image_name)

    # Service data (Env, Volumes, Ports)
    env, volumes, ports = _parse_service_data(local_data, compose_data)

    return Config(
        image=image_name,
        registry=registry,
        type=image_type,
        variants=variants,
        test=test,
        architectures=architectures,
        metadata=metadata,
        env=env,
        volumes=volumes,
        ports=ports,
    )
