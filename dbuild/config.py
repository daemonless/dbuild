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
class Config:
    """Top-level build configuration for an image."""

    image: str
    registry: str
    type: str = "app"
    variants: list[Variant] = field(default_factory=list)
    test: TestConfig | None = None
    architectures: list[str] = field(default_factory=lambda: ["amd64"])

    @property
    def full_image(self) -> str:
        """Return the fully-qualified image reference (registry/image)."""
        return f"{self.registry}/{self.image}"


# ── Loading ──────────────────────────────────────────────────────────

_CONFIG_PATHS = [
    ".dbuild.yaml",
    ".daemonless/config.yaml",
]


def _find_config_file(base: Path) -> Path | None:
    """Return the first config file that exists, or None."""
    for name in _CONFIG_PATHS:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


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


def _parse_test_config(data: dict[str, Any]) -> TestConfig | None:
    """Parse the ``cit:`` section of the config file."""
    cit = data.get("cit")
    if not cit:
        return None
    return TestConfig(
        mode=cit.get("mode", ""),
        port=cit.get("port"),
        health=cit.get("health"),
        wait=cit.get("wait", 120),
        ready=cit.get("ready"),
        screenshot_wait=cit.get("screenshot_wait"),
        screenshot_path=cit.get("screenshot"),
        https=cit.get("https", False),
        compose=cit.get("compose", False),
        annotations=cit.get("annotations", []),
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

    Variant resolution:
    1. If local config defines ``build.variants``, use those exclusively.
    2. Otherwise, auto-detect from Containerfile names and append any
       extra variants from the global config whose Containerfile exists.

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

    config_file = _find_config_file(base)
    if config_file is not None and yaml is None:
        from dbuild import log
        log.warn(f"Config file {config_file} found but PyYAML is not installed -- "
                 "falling back to auto-detection (pip install PyYAML)")

    local_data: dict[str, Any] = {}
    if config_file is not None and yaml is not None:
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
        # Append global extras (filtered by existing Containerfiles),
        # skipping any tags that auto-detect already produced.
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

    # CIT config from local only
    test = _parse_test_config(local_data)

    return Config(
        image=image_name,
        registry=registry,
        type=image_type,
        variants=variants,
        test=test,
        architectures=architectures,
    )
