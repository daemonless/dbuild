"""Project scaffolding for new dbuild projects.

Generates starter files (.daemonless/config.yaml, Containerfile, CI configs)
from embedded templates with dynamic placeholder replacement.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from dbuild import log

# Templates are co-located in the package
_TEMPLATES_DIR = Path(__file__).parent / "templates"


# FreeBSD port category → daemonless category.
# FreeBSD categories don't align with daemonless's vocabulary, so we map
# best-effort.  Unmapped categories fall back to None (triggers a warning).
_CATEGORY_MAP: dict[str, str] = {
    "databases":    "Databases",
    "devel":        "Development",
    "lang":         "Development",
    "editors":      "Development",
    "dns":          "Network",
    "ftp":          "Downloaders",
    "net":          "Network",
    "net-im":       "Network",
    "net-mgmt":     "Monitoring",
    "net-p2p":      "Downloaders",
    "net-vpn":      "Network",
    "security":     "Security",
    "sysutils":     "Utilities",
    "multimedia":   "Media Servers",
    "graphics":     "Photos & Media",
    "audio":        "Media Servers",
    "www":          "Network",
    "finance":      "Productivity",
    "misc":         "Utilities",
    "ports-mgmt":   "Utilities",
    "shells":       "Utilities",
    "textproc":     "Utilities",
    "archivers":    "Utilities",
}


# FreeBSD port LICENSE identifiers mapped to SPDX.
# The ports tree (bsd.licenses.db.mk) does not expose SPDX identifiers — it
# only defines human-readable _LICENSE_NAME_* values.  This map is maintained
# manually, but FreeBSD port license identifiers are stable and rarely change.
_LICENSE_SPDX: dict[str, str] = {
    # Copyleft — AGPL
    "AGPL3":        "AGPL-3.0-only",
    "AGPL3+":       "AGPL-3.0-or-later",
    # Artistic
    "ARTISTIC":     "Artistic-1.0",
    "ARTISTIC2":    "Artistic-2.0",
    # BSD
    "BSD2CLAUSE":   "BSD-2-Clause",
    "BSD3CLAUSE":   "BSD-3-Clause",
    "BSD4CLAUSE":   "BSD-4-Clause",
    # Creative Commons
    "CC-BY-4.0":    "CC-BY-4.0",
    "CC-BY-SA-4.0": "CC-BY-SA-4.0",
    "CC0-1.0":      "CC0-1.0",
    # CDDL
    "CDDL":         "CDDL-1.0",
    # EUPL
    "EUPL11":       "EUPL-1.1",
    "EUPL12":       "EUPL-1.2",
    # Copyleft — GPL
    "GPLv1":        "GPL-1.0-only",
    "GPLv1+":       "GPL-1.0-or-later",
    "GPLv2":        "GPL-2.0-only",
    "GPLv2+":       "GPL-2.0-or-later",
    "GPLv3":        "GPL-3.0-only",
    "GPLv3+":       "GPL-3.0-or-later",
    # ISC
    "ISC":          "ISC",
    # Copyleft — LGPL
    "LGPL20":       "LGPL-2.0-only",
    "LGPL20+":      "LGPL-2.0-or-later",
    "LGPL21":       "LGPL-2.1-only",
    "LGPL21+":      "LGPL-2.1-or-later",
    "LGPL3":        "LGPL-3.0-only",
    "LGPL3+":       "LGPL-3.0-or-later",
    # MIT
    "MIT":          "MIT",
    # Mozilla
    "MPL20":        "MPL-2.0",
    # Apache
    "APACHE20":     "Apache-2.0",
}


def _to_spdx(raw: str) -> str:
    """Map a FreeBSD port LICENSE token to its SPDX identifier."""
    return _LICENSE_SPDX.get(raw, raw or "UNKNOWN")


def _pkg_run_deps(pkgname: str) -> list[str]:
    """Return runtime dependency package names via pkg rquery.

    Returns an empty list if the package is not found in the repo or pkg
    is unavailable — the caller should warn and fall back gracefully.
    """
    try:
        out = subprocess.check_output(
            ["pkg", "rquery", "%dn", pkgname],
            text=True, stderr=subprocess.DEVNULL,
        )
        return [line.strip() for line in out.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _make_query(port_path: Path, variables: list[str]) -> dict[str, str]:
    """Query port variables via make -V, one call per variable for reliability."""
    result: dict[str, str] = {}
    for var in variables:
        try:
            out = subprocess.check_output(
                ["make", "-C", str(port_path), "-V", var],
                text=True, stderr=subprocess.DEVNULL,
            )
            result[var] = out.strip()
        except subprocess.CalledProcessError:
            result[var] = ""
    return result


def _first_paragraph(text: str) -> str:
    """Return the first non-empty paragraph of text."""
    lines = text.splitlines()
    paragraph: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("WWW:"):
            break
        if not stripped and paragraph:
            break
        if stripped:
            paragraph.append(stripped)
    return " ".join(paragraph)


def _fetch_port_metadata(port_path: str) -> dict[str, str] | None:
    """Fetch metadata from a FreeBSD port in /usr/ports."""
    if "/" not in port_path or port_path.count("/") != 1:
        log.error(
            f"invalid port path '{port_path}' — "
            "expected category/portname (e.g. devel/ccache)"
        )
        return None

    if not Path("/usr/ports").exists():
        log.error("/usr/ports not found")
        log.error("check out the ports tree first:")
        log.error("  git clone --depth=1 https://git.FreeBSD.org/ports.git /usr/ports")
        log.error("  or: portsnap fetch extract")
        return None

    full_path = Path("/usr/ports") / port_path
    if not full_path.exists():
        log.error(f"port not found: {full_path}")
        return None

    log.info(f"querying port metadata for {port_path}...")

    try:
        data = _make_query(full_path, [
            "PORTNAME", "PKGNAME", "PORTVERSION", "COMMENT",
            "WWW", "LICENSE", "USE_RC_SUBR", "CATEGORIES",
        ])
    except RuntimeError as exc:
        log.error(str(exc))
        return None

    # SPDX license — first token only (ports can have multiple)
    spdx_license = _to_spdx(data.get("LICENSE", "").split()[0] if data.get("LICENSE") else "")

    # pkg name: PKGNAME may include version suffix (foo-1.2.3), strip it
    pkgname = re.sub(r"-[\d].*$", "", data.get("PKGNAME", "") or data.get("PORTNAME", ""))

    # Description: first paragraph of pkg-descr, fallback to COMMENT
    description = data.get("COMMENT", "")
    if (full_path / "pkg-descr").exists():
        description = _first_paragraph((full_path / "pkg-descr").read_text()) or description

    web_url = data.get("WWW", "")
    portname = data.get("PORTNAME", pkgname)
    raw_cat = data.get("CATEGORIES", "").split()[0]
    category = _CATEGORY_MAP.get(raw_cat, "")
    rc_name = (
        data.get("USE_RC_SUBR", "").split()[0]
        if data.get("USE_RC_SUBR") else portname
    )

    log.info(f"querying runtime deps for {pkgname}...")
    _deps = _pkg_run_deps(pkgname)
    if not _deps:
        log.warn(f"no runtime deps found for '{pkgname}' via pkg rquery"
                 " — is the pkg index up to date?")

    meta = {
        "name":           portname,
        "pkgname":        pkgname,
        "web_url":        web_url,
        "upstream_url":   web_url,
        "description":    description,
        "license":        spdx_license,
        "packages":       pkgname,
        "run_deps":       " ".join(_deps),
        "rc_name":        rc_name,
        "freshports_url": f"https://www.freshports.org/{port_path}/",
        "category":       category,
    }

    desc_preview = meta["description"][:80] + ("..." if len(meta["description"]) > 80 else "")

    log.step("Port metadata")
    log.success(f"name:        {portname} (pkg: {pkgname})")
    log.success(f"description: {desc_preview}")
    log.success(f"license:     {spdx_license}")
    log.success(f"web:         {web_url}")
    log.success(f"category:    {category or f'(unmapped: {raw_cat})'}")
    log.success(f"run_deps:    {meta['run_deps'] or '(none found)'}")
    if not category:
        log.warn(f"no daemonless category mapping for '{raw_cat}' — set --category manually")
    if not web_url:
        log.warn("WWW not set in port — web_url left as placeholder")
    if spdx_license == "UNKNOWN":
        log.warn("LICENSE not set in port — update org.opencontainers.image.licenses manually")

    return meta


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
        content = re.sub(r"{%- if mlock %}.*?{%- endif %}", "", content, flags=re.DOTALL)

    # Handle variant conditionals: {%- if "tag" in variants %}...{%- endif %}
    variants = context.get("variants", [])
    if isinstance(variants, str):
        variants = [v.strip() for v in variants.split(",")]

    def _variant_sub(m: re.Match) -> str:
        tag = m.group(1)
        block = m.group(2)
        return block.strip("\n") + "\n" if tag in variants else ""

    content = re.sub(
        r'[ \t]*\{%- if "(\w[\w-]*)" in variants %\}\n?(.*?)\n?[ \t]*\{%- endif %\}[ \t]*\n?',
        _variant_sub,
        content,
        flags=re.DOTALL,
    )

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


def _scaffold(
    template: str,
    path: Path,
    context: dict[str, str],
    dry_run: bool,
    executable: bool = False,
) -> int:
    """Render a template and write it; return 1 if created, 0 otherwise."""
    log.debug(f"scaffold {template!r} → {path}")
    content = _render_template(template, context)
    if content and _write_file(path, content, dry_run):
        if executable and not dry_run:
            path.chmod(0o755)
        return 1
    return 0


def run(args: argparse.Namespace) -> int:
    """Scaffold a new dbuild project in the current directory."""
    base = Path.cwd()

    port_meta: dict[str, str] = {}
    if getattr(args, "freebsd_port", None):
        port_meta = _fetch_port_metadata(args.freebsd_port) or {}

    app_name = args.name or port_meta.get("name") or base.name
    title = args.title or app_name.capitalize()
    if port_meta and not args.title:
        log.warn(f"title set to '{title}' — use --title for proper capitalization (e.g. --title FFmpeg)")
    category = (
        args.category if args.category != "Apps"
        else (port_meta.get("category") or "Utilities")
    )
    port = str(args.port)
    variants = [v.strip() for v in args.variants.split(",")]
    dry_run = args.dry_run
    mlock = "true" if args.type == "dotnet" else "false"

    context = {
        "name":           app_name,
        "title":          title,
        "category":       category,
        "port":           port,
        "mlock":          mlock,
        "mlock_bool":     mlock,
        "description":    port_meta.get("description") or f"{title} on FreeBSD.",
        "web_url":        port_meta.get("web_url") or f"https://{app_name}.org/",
        "upstream_url":   port_meta.get("upstream_url") or port_meta.get("web_url") or f"https://github.com/daemonless/{app_name}",
        "repo_url":       f"https://github.com/daemonless/{app_name}",
        "freshports_url": (
            port_meta.get("freshports_url") or
            f"https://www.freshports.org/net-p2p/{app_name}/"
        ),
        "community":      args.community or "",
        "variants":       variants,
        "pkgname":        port_meta.get("pkgname") or app_name,
        "packages":       port_meta.get("packages") or app_name,
        "run_deps":       port_meta.get("run_deps") or app_name,
        "license":        port_meta.get("license") or "UNKNOWN",
        "rc_name":        port_meta.get("rc_name") or app_name,
    }

    has_pkg = any(v.startswith("pkg") for v in variants)
    log.debug(f"variants: {variants}  has_pkg: {has_pkg}  dry_run: {dry_run}")
    log.debug("context:\n" + "\n".join(f"  {k} = {v!r}" for k, v in context.items()))

    created = 0
    created += _scaffold("config.yaml", base / ".daemonless" / "config.yaml", context, dry_run)
    created += _scaffold("compose.yaml", base / "compose.yaml", context, dry_run)
    if "latest" in variants or not has_pkg:
        created += _scaffold("template-upstream.j2", base / "Containerfile.j2", context, dry_run)
    if has_pkg:
        created += _scaffold("template-pkg.j2", base / "Containerfile.pkg.j2", context, dry_run)

    run_path = base / "root" / "etc" / "services.d" / app_name / "run"
    created += _scaffold("run.sh", run_path, context, dry_run, executable=True)
    created += _scaffold("healthz.sh", base / "root" / "healthz", context, dry_run, executable=True)

    if getattr(args, "woodpecker", False):
        created += _scaffold("woodpecker.yaml", base / ".woodpecker.yaml", context, dry_run)
    if getattr(args, "github", False):
        created += _scaffold(
            "github-workflow.yaml",
            base / ".github" / "workflows" / "build.yaml",
            context, dry_run,
        )

    if created == 0:
        log.info("nothing to do (all files already exist)")
    else:
        label = "would be created" if dry_run else "created"
        log.step(f"scaffolded {created} file(s) ({label})")

    return 0
