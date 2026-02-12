"""Project scaffolding for new dbuild projects.

Generates starter files (.daemonless/config.yaml, Containerfile, CI configs)
from embedded templates.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from dbuild import log

# Templates are co-located in the package
_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _copy_template(template_name: str, dest: Path) -> bool:
    """Copy a template file to dest, skipping if dest already exists.

    Returns True if the file was written, False if skipped.
    """
    if dest.exists():
        log.warn(f"skipped {dest} (already exists)")
        return False

    src = _TEMPLATES_DIR / template_name
    if not src.exists():
        log.error(f"template not found: {template_name}")
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    log.step(f"created {dest}")
    return True


def run(args: argparse.Namespace) -> int:
    """Scaffold a new dbuild project in the current directory."""
    base = Path.cwd()
    created = 0

    # Always generate config + Containerfile
    if _copy_template("config.yaml", base / ".daemonless" / "config.yaml"):
        created += 1
    if _copy_template("Containerfile", base / "Containerfile"):
        created += 1

    # Optional: Woodpecker CI
    if getattr(args, "woodpecker", False) and _copy_template(
        "woodpecker.yaml", base / ".woodpecker.yaml"
    ):
        created += 1

    # Optional: GitHub Actions
    if getattr(args, "github", False) and _copy_template(
        "github-workflow.yaml",
        base / ".github" / "workflows" / "build.yaml",
    ):
        created += 1

    if created == 0:
        log.info("nothing to do (all files already exist)")
    else:
        log.step(f"scaffolded {created} file(s)")

    return 0
