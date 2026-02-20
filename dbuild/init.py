"""Project scaffolding for new dbuild projects.

Generates starter files (.daemonless/config.yaml, Containerfile, CI configs)
from embedded templates with dynamic placeholder replacement.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dbuild import log

# Templates are co-located in the package
_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _render_template(template_name: str, context: dict[str, str]) -> str | None:
    """Read a template and replace {{ key }} with context[key]."""
    src = _TEMPLATES_DIR / template_name
    if not src.exists():
        log.error(f"template not found: {template_name}")
        return None

    content = src.read_text()
    for key, val in context.items():
        content = content.replace(f"{{{{ {key} }}}}", str(val))

    # Handle simple conditionals for mlock
    if context.get("mlock") == "true":
        content = content.replace("{%- if mlock %}", "").replace("{%- endif %}", "")
    else:
        import re
        content = re.sub(r"{%- if mlock %}.*?{%- endif %}", "", content, flags=re.DOTALL)

    return content


def _write_file(path: Path, content: str, dry_run: bool = False) -> bool:
    """Write content to path, skipping if path already exists.

    Returns True if the file was written (or would be), False if skipped.
    """
    if path.exists():
        log.warn(f"skipped {path} (already exists)")
        return False

    if dry_run:
        log.info(f"[dry-run] would create {path}")
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    log.step(f"created {path}")
    return True


def run(args: argparse.Namespace) -> int:
    """Scaffold a new dbuild project in the current directory."""
    base = Path.cwd()

    # Defaults
    app_name = args.name or base.name
    title = args.title or app_name.capitalize()
    category = args.category
    app_type = args.type
    port = str(args.port)
    variants = [v.strip() for v in args.variants.split(",")]
    dry_run = args.dry_run

    mlock = "true" if app_type == "dotnet" else "false"

    context = {
        "name": app_name,
        "title": title,
        "category": category,
        "port": port,
        "mlock": mlock,
        "mlock_bool": mlock,
        "description": f"{title} on FreeBSD.",
    }

    created = 0

    # 1. .daemonless/config.yaml
    config_content = _render_template("config.yaml", context)
    if config_content and _write_file(base / ".daemonless" / "config.yaml", config_content, dry_run):
        created += 1

    # 2. compose.yaml
    compose_content = _render_template("compose.yaml", context)
    if compose_content and _write_file(base / "compose.yaml", compose_content, dry_run):
        created += 1

    # 3. Containerfile.j2 (Source variant - latest)
    # Only create if 'latest' is in variants (or no pkg variants asked for)
    if "latest" in variants or not any(v.startswith("pkg") for v in variants):
        upstream_content = _render_template("template-upstream.j2", context)
        if upstream_content and _write_file(base / "Containerfile.j2", upstream_content, dry_run):
            created += 1

    # 4. Containerfile.pkg.j2 (Package variant - pkg, pkg-latest)
    if any(v.startswith("pkg") for v in variants):
        pkg_content = _render_template("template-pkg.j2", context)
        if pkg_content and _write_file(base / "Containerfile.pkg.j2", pkg_content, dry_run):
            created += 1

    # 5. root/etc/services.d/<app>/run
    run_content = _render_template("run.sh", context)
    if run_content and _write_file(base / "root" / "etc" / "services.d" / app_name / "run", run_content, dry_run):
        # Set executable bit if not dry-run
        if not dry_run:
            (base / "root" / "etc" / "services.d" / app_name / "run").chmod(0o755)
        created += 1

    # 6. root/healthz
    healthz_content = _render_template("healthz.sh", context)
    if healthz_content and _write_file(base / "root" / "healthz", healthz_content, dry_run):
        # Set executable bit if not dry-run
        if not dry_run:
            (base / "root" / "healthz").chmod(0o755)
        created += 1

    # Optional: CI configs
    if getattr(args, "woodpecker", False):
        wp_content = _render_template("woodpecker.yaml", context)
        if wp_content and _write_file(base / ".woodpecker.yaml", wp_content, dry_run):
            created += 1

    if getattr(args, "github", False):
        gh_content = _render_template("github-workflow.yaml", context)
        if gh_content and _write_file(base / ".github" / "workflows" / "build.yaml", gh_content, dry_run):
            created += 1

    if created == 0:
        log.info("nothing to do (all files already exist)")
    else:
        label = "would be created" if dry_run else "created"
        log.step(f"scaffolded {created} file(s) ({label})")

    return 0
