"""Lint daemonless image repos for common issues.

Auto-detects scope: if CWD is an image repo, lints just CWD.
Otherwise lints all subdirectories (e.g. run from the workspace root).
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from dbuild.config import VALID_CATEGORIES

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

REQUIRED_X_DAEMONLESS_FIELDS = [
    "title",
    "icon",
    "category",
    "description",
    "upstream_url",
    "user",
]


def _is_image_repo(path: Path) -> bool:
    return (path / "compose.yaml").exists() or (path / ".daemonless" / "config.yaml").exists()


def lint_repo(repo_path: Path, verbose: bool = False) -> tuple[list[str], list[str]]:
    """Lint a single repo. Returns (errors, warnings)."""
    if yaml is None:
        return ["PyYAML is not installed"], []

    errors: list[str] = []
    warnings: list[str] = []

    compose_path = repo_path / "compose.yaml"
    config_path = repo_path / ".daemonless" / "config.yaml"

    if not compose_path.exists() and not config_path.exists():
        return errors, warnings

    if compose_path.exists():
        if verbose:
            print("  checking compose.yaml")
        try:
            with open(compose_path) as f:
                data: dict[str, Any] = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            errors.append(f"Invalid YAML in compose.yaml: {e}")
            return errors, warnings

        if not data:
            errors.append("Empty compose.yaml")
            return errors, warnings

        meta = data.get("x-daemonless", {})

        if not meta:
            errors.append("Missing x-daemonless metadata block in compose.yaml")
        else:
            is_deprecated = "deprecated" in meta

            if verbose:
                print("  checking x-daemonless fields")
            # Fields that become optional (warnings only) for deprecated images
            OPTIONAL_WHEN_DEPRECATED = {"upstream_url", "description"}

            for field in REQUIRED_X_DAEMONLESS_FIELDS:
                if field not in meta or not meta[field]:
                    if is_deprecated and field in OPTIONAL_WHEN_DEPRECATED:
                        warnings.append(
                            f"Missing x-daemonless.{field}"
                            f" (optional for deprecated images)"
                        )
                    else:
                        errors.append(f"Missing required field: x-daemonless.{field}")

            description = meta.get("description", "")
            if "deprecated" in description.lower():
                warnings.append(
                    "x-daemonless.description contains the word 'deprecated'."
                    " Use the structured x-daemonless.deprecated block instead."
                )

            if verbose:
                print("  checking category")
            category = meta.get("category", "")
            if category and category not in VALID_CATEGORIES:
                errors.append(
                    f"Invalid category '{category}'."
                    f" Valid: {', '.join(VALID_CATEGORIES)}"
                )

            if verbose:
                print("  checking icon")
            icon = meta.get("icon", "")
            if icon and not (icon.startswith(":") and icon.endswith(":")):
                errors.append(f"Invalid icon format '{icon}'. Should be :icon-name:")

            if verbose:
                print("  checking env docs")
            docs = meta.get("docs", {})
            services = data.get("services", {})
            if isinstance(docs, dict) and services:
                service = next(iter(services.values()))
                env_vars = service.get("environment", [])
                if isinstance(env_vars, list):
                    env_keys = [e.split("=")[0] for e in env_vars if "=" in e]
                else:
                    env_keys = list(env_vars.keys())

                skip_vars = {"PUID", "PGID", "TZ"}
                doc_env = docs.get("env", {})
                for key in env_keys:
                    if key not in skip_vars and key not in doc_env:
                        warnings.append(
                            f"Undocumented env var: {key}"
                            f" (add to x-daemonless.docs.env)"
                        )

    if config_path.exists():
        if verbose:
            print("  checking .daemonless/config.yaml")
        try:
            with open(config_path) as f:
                config: dict[str, Any] = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            errors.append(f"Invalid YAML in .daemonless/config.yaml: {e}")
            return errors, warnings

        if not config:
            errors.append("Empty .daemonless/config.yaml")
            return errors, warnings

        archs = config.get("build", {}).get("architectures", [])
        if archs and not isinstance(archs, list):
            errors.append("build.architectures should be a list")
        else:
            valid_archs = {"amd64", "aarch64", "riscv64"}
            for arch in archs:
                if arch not in valid_archs:
                    errors.append(
                        f"Invalid architecture '{arch}'."
                        f" Valid: {', '.join(sorted(valid_archs))}"
                    )

    if compose_path.exists() and data:
        services = data.get("services", {})
        for svc_name, svc in services.items():
            image = svc.get("image", "")
            if image.startswith("localhost/"):
                warnings.append(
                    f"Service '{svc_name}' image references localhost: {image!r}"
                    " — run dbuild generate on a host with a git remote set"
                )

    readme_path = repo_path / "README.md"
    if readme_path.exists():
        readme_text = readme_path.read_text()
        if "localhost/" in readme_text:
            warnings.append(
                "README.md contains localhost/ image references"
                " — run dbuild generate on a host with a git remote set"
            )

    if verbose:
        print("  checking Containerfile")
    has_containerfile = any(
        (repo_path / name).exists()
        for name in ("Containerfile", "Containerfile.j2", "Containerfile.pkg", "Containerfile.pkg.j2")
    )
    if not has_containerfile:
        errors.append("Missing Containerfile")

    return errors, warnings


def run(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    verbose = getattr(args, "verbose", False)

    if _is_image_repo(cwd):
        repos = [cwd]
    else:
        repos = sorted(p for p in cwd.iterdir() if p.is_dir())

    all_errors: dict[str, list[str]] = {}
    all_warnings: dict[str, list[str]] = {}

    for repo in repos:
        if verbose:
            print(f"\n{repo.name}:")
        errors, warnings = lint_repo(repo, verbose=verbose)
        if errors:
            all_errors[repo.name] = errors
        if warnings:
            all_warnings[repo.name] = warnings

    if all_warnings:
        print("\nWARNINGS:")
        print("=" * 60)
        for name, warns in sorted(all_warnings.items()):
            print(f"\n{name}:")
            for w in warns:
                print(f"  - {w}")

    if all_errors:
        print("\nERRORS:")
        print("=" * 60)
        for name, errs in sorted(all_errors.items()):
            print(f"\n{name}:")
            for e in errs:
                print(f"  - {e}")
        n = len(all_errors)
        print(f"\n{n} repo{'s' if n != 1 else ''} with errors")
        return 1

    print("\nAll repos passed lint checks")
    return 0
