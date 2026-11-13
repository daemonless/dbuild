"""Project scaffolding for new dbuild projects.

Generates starter files (.daemonless/config.yaml, Containerfile, CI configs)
from embedded templates with dynamic placeholder replacement.

With --freebsd-port the port itself fills in what it knows: name, comment,
license, WWW, category, the daemon binary and flags (from its rc script),
the installed binaries (from its plist) and the web port (rc script or
pkg-message).  Without a ports tree the base metadata comes from `pkg rquery`.

The image is a single process on base-exec: a launcher in
root/daemonless/libexec (the CMD) and a setup script in root/daemonless/init.d.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from dbuild import log
from dbuild.config import DEFAULT_COMMUNITY

# Templates are co-located in the package
_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Placeholders a scaffolded Containerfile.j2 keeps for `dbuild generate`,
# which fills them from compose.yaml on every run; init must not bake the
# values in, or compose.yaml stops being the single source of truth.
_GENERATOR_KEYS = frozenset({"title", "description", "repo_url", "web_url", "category"})

_DEFAULT_PORT = 8080
_PORT_VARIANTS = "pkg,pkg-latest"
_FRESHPORTS_RE = re.compile(r"^https?://(?:www\.)?freshports\.org/([^/]+/[^/]+)/?$")
_RC_VAR_RE = re.compile(r"\$\{(\w+)_port:?=\"?(\d{2,5})")
# "localhost:8080", "http://127.0.0.1:9091/web", "https://<host>:3000"
_MSG_PORT_RE = re.compile(r"(?<![\w.])(?:https?://)?[\w.\[\]<>-]+:(\d{2,5})(?![\d.])")


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


# FreeBSD port LICENSE tokens (Mk/bsd.licenses.db.mk) mapped to SPDX ids.
# The ports tree only carries human-readable names, so this map is kept by
# hand.  Keys are the tokens ports actually use ("AGPLv3", "ISCL", "ART20"),
# not SPDX-ish guesses.  Tokens with no exact SPDX id (PD, EPL, GFDL, BSD,
# GMGPL, LPPL13, WTFPL1 ...) are deliberately absent: an absent token means
# "UNKNOWN, go read the upstream LICENSE", never a near miss.
_LICENSE_SPDX: dict[str, str] = {
    "AGPLv3":       "AGPL-3.0-only",
    "AGPLv3+":      "AGPL-3.0-or-later",
    "APACHE10":     "Apache-1.0",
    "APACHE11":     "Apache-1.1",
    "APACHE20":     "Apache-2.0",
    "ART10":        "Artistic-1.0",
    "ARTPERL10":    "Artistic-1.0-Perl",
    "ART20":        "Artistic-2.0",
    "BSD0CLAUSE":   "0BSD",
    "BSD2CLAUSE":   "BSD-2-Clause",
    "BSD3CLAUSE":   "BSD-3-Clause",
    "BSD4CLAUSE":   "BSD-4-Clause",
    "BSL":          "BSL-1.0",
    "CDDL":         "CDDL-1.0",
    "ClArtistic":   "ClArtistic",
    "CPAL-1.0":     "CPAL-1.0",
    "EUPL11":       "EUPL-1.1",
    "EUPL12":       "EUPL-1.2",
    "GPLv1":        "GPL-1.0-only",
    "GPLv1+":       "GPL-1.0-or-later",
    "GPLv2":        "GPL-2.0-only",
    "GPLv2+":       "GPL-2.0-or-later",
    "GPLv3":        "GPL-3.0-only",
    "GPLv3+":       "GPL-3.0-or-later",
    "ISCL":         "ISC",
    "LGPL20":       "LGPL-2.0-only",
    "LGPL20+":      "LGPL-2.0-or-later",
    "LGPL21":       "LGPL-2.1-only",
    "LGPL21+":      "LGPL-2.1-or-later",
    "LGPL3":        "LGPL-3.0-only",
    "LGPL3+":       "LGPL-3.0-or-later",
    "LPPL10":       "LPPL-1.0",
    "LPPL11":       "LPPL-1.1",
    "LPPL12":       "LPPL-1.2",
    "LPPL13a":      "LPPL-1.3a",
    "LPPL13c":      "LPPL-1.3c",
    "MIT":          "MIT",
    "MPL10":        "MPL-1.0",
    "MPL11":        "MPL-1.1",
    "MPL20":        "MPL-2.0",
    "NCSA":         "NCSA",
    "ODbL":         "ODbL-1.0",
    "OFL10":        "OFL-1.0",
    "OFL11":        "OFL-1.1",
    "OpenSSL":      "OpenSSL",
    "PHP202":       "PHP-2.02",
    "PHP30":        "PHP-3.0",
    "PHP301":       "PHP-3.01",
    "PostgreSQL":   "PostgreSQL",
    "PSFL":         "PSF-2.0",
    "RUBY":         "Ruby",
    "TclTk":        "TCL",
    "UNLICENSE":    "Unlicense",
    "WTFPL":        "WTFPL",
    "ZLIB":         "Zlib",
    "ZPL21":        "ZPL-2.1",
}
# Creative Commons tokens are already SPDX ids (CC-BY-4.0, CC-BY-SA-3.0, CC0-1.0 ...)
_LICENSE_PASSTHROUGH_RE = re.compile(r"^CC(0|-BY(-NC)?(-ND|-SA)?)-\d\.\d$")
# The port explicitly says it doesn't know
_LICENSE_UNSET = {"", "NA", "NONE", "UNKNOWN"}


def _to_spdx(raw: str, comb: str = "single") -> tuple[str, list[str]]:
    """Map a port's LICENSE line (plus LICENSE_COMB) to an SPDX expression.

    Returns (expression, unmapped_tokens).  Any token without an exact SPDX
    mapping makes the whole expression "UNKNOWN" -- a partial expression
    would read as authoritative when it isn't.
    """
    tokens = raw.split()
    if not tokens or any(t in _LICENSE_UNSET for t in tokens):
        return "UNKNOWN", tokens
    mapped: list[str] = []
    unmapped: list[str] = []
    for tok in tokens:
        spdx = _LICENSE_SPDX.get(tok) or (tok if _LICENSE_PASSTHROUGH_RE.match(tok) else None)
        (mapped if spdx else unmapped).append(spdx or tok)
    if unmapped:
        return "UNKNOWN", unmapped
    if len(mapped) == 1:
        return mapped[0], []
    joiner = " OR " if comb == "dual" else " AND "
    return joiner.join(mapped), []


def _pkg_rquery(fmt: str, pkgname: str) -> str:
    """`pkg rquery <fmt> <pkgname>`; "" if pkg or the package is absent."""
    return _pkg_rquery_argv([fmt, pkgname])


def _pkg_rquery_where(fmt: str, condition: str) -> str:
    """`pkg rquery -e <condition> <fmt>`, e.g. every package of one origin."""
    return _pkg_rquery_argv(["-e", condition, fmt])


def _pkg_rquery_argv(argv: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["pkg", "rquery", *argv], text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _pkg_run_deps(pkgname: str) -> list[str]:
    """Runtime dependency package names via pkg rquery ([] if unknown)."""
    return [line.strip() for line in _pkg_rquery("%dn", pkgname).splitlines() if line.strip()]


def _make_query(
    port_path: Path, variables: list[str], flavor: str | None = None,
) -> dict[str, str]:
    """Query port variables via make -V, one call per variable for reliability.

    *flavor* is passed as the FLAVOR env var (FreeBSD's own selection
    mechanism), not a -V variable — ports don't accept it as a make arg.
    """
    env = os.environ.copy()
    if flavor:
        env["FLAVOR"] = flavor
    result: dict[str, str] = {}
    for var in variables:
        try:
            out = subprocess.check_output(
                ["make", "-C", str(port_path), "-V", var],
                text=True, stderr=subprocess.DEVNULL, env=env,
            )
            result[var] = out.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            result[var] = ""
    return result


def _strip_pkg_version(raw: str) -> str:
    """Strip the trailing version (foo-1.2.3 -> foo) from a PKGNAME."""
    return re.sub(r"-[\d].*$", "", raw)


def _portsdir() -> Path | None:
    """The ports tree, or None when there isn't one (pkg rquery still works)."""
    portsdir = Path(os.getenv("PORTSDIR", "/usr/ports"))
    return portsdir if (portsdir / "Mk").is_dir() else None


def _resolve_port_spec(spec: str, portsdir: Path | None) -> str | None:
    """Normalize what the user typed into a category/portname origin.

    Accepts "net-p2p/transmission", a FreshPorts URL, a path inside the
    ports tree, or a bare port/package name (looked up in the ports tree,
    then in the pkg repo).  Logs and returns None when it can't.
    """
    spec = spec.strip()
    m = _FRESHPORTS_RE.match(spec)
    if m:
        spec = m.group(1)
    if portsdir and spec.startswith(f"{portsdir}/"):
        spec = spec[len(str(portsdir)) + 1:]
    spec = spec.strip("/")
    parts = [p for p in spec.split("/") if p]

    if len(parts) == 2:
        return "/".join(parts)
    if len(parts) != 1:
        log.error(
            f"invalid port '{spec}' — expected category/portname "
            "(e.g. net-p2p/transmission), a FreshPorts URL, or a bare name"
        )
        return None

    name = parts[0]
    if portsdir:
        hits = sorted(
            p for p in portsdir.glob(f"*/{name}")
            if (p / "Makefile").is_file() and p.parent.name[0].islower()
        )
        if len(hits) == 1:
            return f"{hits[0].parent.name}/{name}"
        if len(hits) > 1:
            log.error(
                f"'{name}' is ambiguous — pick one of: "
                + ", ".join(f"{p.parent.name}/{name}" for p in hits)
            )
            return None
    origin = _pkg_rquery("%o", name)
    if origin:
        log.info(f"{name} → {origin} (from the pkg repo)")
        return origin
    where = "the ports tree or the pkg repo" if portsdir else "the pkg repo"
    log.error(f"no port or package named '{name}' in {where}")
    return None


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


def _resolve_flavors(
    full_path: Path, port_path: str, requested: list[str], available: list[str],
) -> dict[str, str] | None:
    """Validate *requested* flavors against the port and return their pkgnames.

    Returns {} if *requested* is empty. Returns None (after logging) if the
    port has no FLAVORS, or a requested flavor isn't one of them — `make`
    silently ignores an unrecognized FLAVOR value instead of erroring, so
    this has to be checked explicitly before it's used for anything.
    """
    if not requested:
        return {}

    if not available:
        log.error(f"{port_path} has no FLAVORS — --flavors doesn't apply here")
        return None

    unknown = [f for f in requested if f not in available]
    if unknown:
        log.error(
            f"unknown flavor(s) {', '.join(unknown)} for {port_path} — "
            f"available: {', '.join(available)}"
        )
        return None

    log.info(f"querying pkgnames for flavors: {', '.join(requested)}...")
    flavor_pkgnames: dict[str, str] = {}
    for flavor in requested:
        raw = _make_query(full_path, ["PKGNAME"], flavor=flavor).get("PKGNAME", "")
        flavor_pkgnames[flavor] = _strip_pkg_version(raw)
        log.success(f"flavor {flavor}: pkg = {flavor_pkgnames[flavor]}")
    return flavor_pkgnames


def _read(path: Path) -> str:
    return path.read_text(errors="replace") if path.is_file() else ""


def _continued_lines(text: str, pattern: str) -> list[str]:
    """Lines matching *pattern* (anchored at line start), each with its
    backslash continuations; `.*(?:\\\n.*)*` can't do this because the
    greedy `.*` keeps the backslash and the optional group matches nothing."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if re.match(pattern, lines[i]):
            while lines[i].rstrip().endswith("\\") and i + 1 < len(lines):
                out.append(lines[i].rstrip().replace("\t", "    "))
                i += 1
            out.append(lines[i].rstrip().replace("\t", "    "))
        i += 1
    return out


def _rc_script_info(
    port_dir: Path, rc_names: list[str], subst: dict[str, str] | None = None,
) -> dict[str, object]:
    """What the port's rc script (files/<name>.in) says about running the daemon.

    Returns {} when there is none.  Otherwise: file, binary (the real daemon,
    looked through /usr/sbin/daemon wrappers), extra_args (words after the
    binary in command=, e.g. "web" for `gitea web`), ports (from *_port
    defaults) and hints (the command/flags lines, for the run-script comment).
    *subst* maps %%VAR%% placeholders (PREFIX, ETCDIR ...) to their values.
    """
    for rc in rc_names:
        rc_file = port_dir / "files" / f"{rc}.in"
        if rc_file.is_file():
            break
    else:
        return {}
    text = _read(rc_file)
    for key, val in {"PREFIX": "/usr/local", "LOCALBASE": "/usr/local", **(subst or {})}.items():
        if val:
            text = text.replace(f"%%{key}%%", val)
    # Placeholders the port's Makefile would fill (%%GITUSER%%) stay readable
    text = re.sub(r"%%(\w+)%%", r"<\1>", text)
    # A value continued with a backslash (adguardhome's command_args) is one
    # line to the shell; the hints keep the original layout.
    joined = re.sub(r"\\\n[ \t]*", " ", text)

    def var(name: str) -> str:
        m = re.search(rf'^{name}=["\']?(.*?)["\']?[ \t]*$', joined, re.M)
        return m.group(1).strip() if m else ""

    rc_name = var("name") or rc_names[0]
    text = text.replace("${name}", rc_name).replace("$name", rc_name)
    joined = joined.replace("${name}", rc_name).replace("$name", rc_name)
    command = var("command")
    procname = var("procname")
    command_args = var("command_args")

    binary, extra_args = "", ""
    words = command.split()
    if words and os.path.basename(words[0]) != "daemon":
        binary, extra_args = words[0], " ".join(words[1:])
    else:
        # /usr/sbin/daemon wrapper: the real program is in procname or command_args
        for src in (procname, command_args):
            m = re.search(r"/usr/local/s?bin/[\w.+-]+", src)
            if m:
                binary = m.group(0)
                break

    hints = _continued_lines(
        text, rf"(?:command|command_args|procname|{re.escape(rc_name)}_(?:flags|args))=",
    )
    # rc.conf defaults (conf dirs, users ...) tell which paths to remap
    hints += _continued_lines(text, rf": \$\{{{re.escape(rc_name)}_(?!enable)\w+[:=]")

    return {
        "file":       rc_file,
        "binary":     binary,
        "extra_args": extra_args,
        "ports":      [int(p) for _, p in _RC_VAR_RE.findall(text)],
        "hints":      hints[:20],
    }


def _plist_binaries(port_dir: Path, plist_files: str) -> list[str]:
    """Programs the package installs under /usr/local/{bin,sbin}, from the plist(s)."""
    entries = plist_files.split()
    for plist in sorted(port_dir.glob("pkg-plist*")):
        entries.extend(_read(plist).splitlines())
    found: list[str] = []
    for entry in entries:
        m = re.match(r'^"?(?:%%\w+%%)*(?:/usr/local/)?(s?bin/[\w.+-]+)"?$', entry.strip())
        if m and f"/usr/local/{m.group(1)}" not in found:
            found.append(f"/usr/local/{m.group(1)}")
    return found


def _message_ports(port_dir: Path) -> list[int]:
    """Ports mentioned as host:port in pkg-message (e.g. "browse to localhost:9091")."""
    text = _read(port_dir / "pkg-message") + _read(port_dir / "files" / "pkg-message.in")
    ports: list[int] = []
    for p in _MSG_PORT_RE.findall(text):
        n = int(p)
        if 80 <= n <= 65535 and n not in ports:
            ports.append(n)
    return ports


def _pick_binary(
    rc_binary: str, binaries: list[str], names: list[str],
) -> tuple[str, str]:
    """The most likely daemon and where it came from: the rc script first,
    then the plist (the only program, or one named like a flavor/port/pkg)."""
    if rc_binary:
        return rc_binary, "rc script"
    if len(binaries) == 1:
        return binaries[0], "plist"
    for candidate in names:
        for b in binaries:
            if os.path.basename(b) == candidate:
                return b, "plist"
    return (binaries[0], "plist (first of several)") if binaries else ("", "")


def _fetch_port_metadata(
    port_path: str, flavors: list[str] | None = None, want_run_deps: bool = False,
) -> dict[str, object] | None:
    """Fetch metadata from a FreeBSD port (ports tree, else the pkg repo)."""
    portsdir = _portsdir()
    origin = _resolve_port_spec(port_path, portsdir)
    if origin is None:
        return None

    if portsdir is None:
        if flavors:
            log.error("--flavors needs a ports tree (FLAVORS come from the port's Makefile)")
            return None
        return _fetch_pkg_metadata(origin, want_run_deps)

    full_path = portsdir / origin
    if not (full_path / "Makefile").is_file():
        log.error(f"port not found: {full_path}")
        name = origin.split("/")[-1]
        known = sorted(
            {f"{p.parent.name}/{name}" for p in portsdir.glob(f"*/{name}") if (p / "Makefile").is_file()}
            | {o for o in [_pkg_rquery("%o", name)] if o}
        )
        if known:
            log.error(f"did you mean: {', '.join(known)}")
        return None

    log.info(f"querying port metadata for {origin}...")
    data = _make_query(full_path, [
        "PORTNAME", "PKGNAME", "PORTVERSION", "COMMENT", "WWW", "LICENSE",
        "LICENSE_COMB", "USE_RC_SUBR", "CATEGORIES", "FLAVORS", "PLIST_FILES",
        "ETCDIR", "DATADIR", "WWWDIR",
    ])

    portname = data["PORTNAME"]
    pkgname = _strip_pkg_version(data["PKGNAME"] or portname)
    available_flavors = data["FLAVORS"].split()
    flavor_pkgnames = _resolve_flavors(full_path, origin, flavors or [], available_flavors)
    if flavor_pkgnames is None:
        return None

    spdx, unmapped = _to_spdx(data["LICENSE"], data["LICENSE_COMB"] or "single")
    raw_cat = (data["CATEGORIES"].split() or [""])[0]
    rc_names = data["USE_RC_SUBR"].split()
    rc = _rc_script_info(
        full_path, rc_names,
        {"PORTNAME": portname, **{k: data[k] for k in ("ETCDIR", "DATADIR", "WWWDIR")}},
    )
    binaries = _plist_binaries(full_path, data["PLIST_FILES"])
    binary, binary_source = _pick_binary(
        str(rc.get("binary", "")), binaries,
        [*flavor_pkgnames.values(), portname, pkgname],
    )

    # Web port: the rc script's *_port default beats a pkg-message mention;
    # either is used only when there is exactly one candidate.
    port_candidates = list(rc.get("ports", [])) or _message_ports(full_path)
    port_source = "rc script" if rc.get("ports") else "pkg-message"

    # WWW may list several URLs: the first is the site, a GitHub one the source
    www = data["WWW"].split()
    meta: dict[str, object] = {
        "origin":          origin,
        "name":            portname,
        "pkgname":         pkgname,
        "version":         data["PORTVERSION"],
        "web_url":         www[0] if www else "",
        "upstream_url":    next((u for u in www if "github.com/" in u), www[0] if www else ""),
        "description":     data["COMMENT"],
        "long_description": _first_paragraph(_read(full_path / "pkg-descr")),
        "license":         spdx,
        "license_raw":     data["LICENSE"],
        "license_unmapped": unmapped,
        "packages":        pkgname,
        "run_deps":        " ".join(_pkg_run_deps(pkgname)) if want_run_deps else "",
        "freshports_url":  f"https://www.freshports.org/{origin}/",
        "category":        _CATEGORY_MAP.get(raw_cat, ""),
        "category_raw":    raw_cat,
        "flavor_pkgnames": flavor_pkgnames,
        "flavors":         available_flavors,
        "binary":          binary,
        "binary_source":   binary_source,
        "binaries":        binaries,
        "extra_args":      rc.get("extra_args", ""),
        "rc_file":         rc.get("file"),
        "rc_hints":        rc.get("hints", []),
        "port":            port_candidates[0] if len(port_candidates) == 1 else 0,
        "port_source":     port_source,
        "port_candidates": port_candidates,
        "in_repo":         bool(_pkg_rquery("%n", pkgname)),
        "source":          "ports tree",
    }
    return meta


def _pick_pkg(names: list[str], portname: str) -> str:
    """Which of an origin's packages is the service: the port's own name, then
    a daemon/server sub-package (transmission-components -> transmission-daemon)."""
    for cand in (portname, portname.removesuffix("-components")):
        if cand in names:
            return cand
    for n in names:
        if re.search(r"-(daemon|server|nox)$", n):
            return n
    return names[0]


def _fetch_pkg_metadata(origin: str, want_run_deps: bool) -> dict[str, object] | None:
    """Metadata from the pkg repo alone (no ports tree): enough for the labels."""
    log.info(f"no ports tree — querying the pkg repo for {origin}...")
    names = _pkg_rquery_where("%n", f"%o = {origin}").split()
    if not names:
        log.error(f"no package with origin {origin} in the pkg repo")
        log.error("for a port that isn't packaged yet, check out the ports tree:")
        log.error("  git clone --depth=1 https://git.FreeBSD.org/ports.git /usr/ports")
        return None
    portname = origin.split("/")[-1]
    pkgname = _pick_pkg(names, portname)
    # a -daemon/-server sub-package carries the service; the app is the stem
    portname = re.sub(r"-(daemon|server|nox|cli)$", "", pkgname)
    fields = _pkg_rquery("%v\n%c\n%w\n%l\n%L", pkgname).split("\n") + [""] * 5
    version, comment, www, comb, licenses = fields[:5]
    comb = {"and": "multi", "or": "dual"}.get(comb, "single")
    spdx, unmapped = _to_spdx(licenses.replace(",", " "), comb)
    raw_cat = origin.split("/")[0]
    return {
        "origin":           origin,
        "name":             portname,
        "pkgname":          pkgname,
        "version":          version,
        "web_url":          www,
        "upstream_url":     www,
        "description":      comment,
        "long_description": "",
        "license":          spdx,
        "license_raw":      licenses,
        "license_unmapped": unmapped,
        "packages":         pkgname,
        "run_deps":         " ".join(_pkg_run_deps(pkgname)) if want_run_deps else "",
        "freshports_url":   f"https://www.freshports.org/{origin}/",
        "category":         _CATEGORY_MAP.get(raw_cat, ""),
        "category_raw":     raw_cat,
        "flavor_pkgnames":  {},
        "flavors":          [],
        "binary":           "",
        "binary_source":    "",
        "binaries":         [],
        "extra_args":       "",
        "rc_file":          None,
        "rc_hints":         [],
        "port":             0,
        "port_source":      "",
        "port_candidates":  [],
        "in_repo":          True,
        "sibling_packages": names,
        "source":           "pkg repo",
    }


def _build_variants_block(
    variants: list[str], flavor_pkgnames: dict[str, str], default: str | None = None,
) -> str:
    """Render the `build.variants:` YAML list for config.yaml.

    Source-type tags (not starting with "pkg") get a plain Containerfile
    entry. Pkg-type tags get Containerfile.pkg; if *flavor_pkgnames* is
    non-empty, each pkg-type tag is replaced by one entry per flavor (e.g.
    pkg -> pkg-lua, pkg-wolfssl), each carrying that flavor's own resolved
    pkg_name -- flavors are a FreeBSD-package concept, so source variants
    are never crossed with them.

    *default* names the tag `:latest` points at; a pkg tag in that role
    also gets the `latest` alias, so a pkg-only image still publishes one.
    """
    lines: list[str] = []
    for tag in variants:
        is_pkg = tag.startswith("pkg")
        is_latest_pkgs = tag.endswith("-latest")
        targets = (
            [(f"{tag}-{flavor}", pkg) for flavor, pkg in flavor_pkgnames.items()]
            if is_pkg and flavor_pkgnames
            else [(tag, None)]
        )
        for out_tag, pkg_name in targets:
            lines.append(f"    - tag: {out_tag}")
            lines.append(f"      containerfile: Containerfile{'.pkg' if is_pkg else ''}")
            if pkg_name:
                lines.append(f"      pkg_name: {pkg_name}")
            if is_pkg and is_latest_pkgs:
                lines.append("      args:")
                lines.append('        BASE_VERSION: "15.1-latest"')
            if out_tag == default:
                if out_tag != "latest":
                    lines.append('      aliases: ["latest"]')
                lines.append("      default: true")
    return "\n".join(lines)


def _build_block(variants: list[str], flavor_pkgnames: dict[str, str], pkgname: str) -> str:
    """The `build:` body for config.yaml: the package name once at the top
    (never per variant, which hides the image from the status page) and the
    variants, the first one the default."""
    lines: list[str] = []
    has_pkg = any(v.startswith("pkg") for v in variants)
    if has_pkg and not flavor_pkgnames:
        lines.append(f"  pkg_name: {pkgname}")
    lines.append("  variants:")
    default = variants[0] if variants else None
    if default and default.startswith("pkg") and flavor_pkgnames:
        default = f"{default}-{next(iter(flavor_pkgnames))}"
    lines.append(_build_variants_block(variants, flavor_pkgnames, default))
    return "\n".join(lines)


def _render_template(
    template_name: str, context: dict[str, object], generator: bool = False,
) -> str | None:
    """Read a template and replace {{ key }} with context[key].

    With *generator* the output is itself a Jinja template for `dbuild
    generate`: the keys it owns and its `{%- if mlock %}` blocks are left
    for it.
    """
    src = _TEMPLATES_DIR / template_name
    if not src.exists():
        log.error(f"template not found: {template_name}")
        return None

    content = src.read_text()
    for key, val in context.items():
        if generator and key in _GENERATOR_KEYS:
            continue
        content = content.replace(f"{{{{ {key} }}}}", str(val))
    if generator:
        return content

    # {%- if mlock %}...{%- endif %}: drop the block (and the line it starts
    # on) when mlock is off; when on, keep it without doubling blank lines
    # for a block that opens with its own newline.  Inline uses (a trailing
    # label continuation) have no leading newline to deal with.
    mlock_on = context.get("mlock") == "true"

    def _mlock_sub(m: re.Match) -> str:
        if not mlock_on:
            return ""
        prefix, block = m.group(1) or "", m.group(2).rstrip(" \t\n")
        return block if block.startswith("\n") else prefix + block

    content = re.sub(
        r"(\n[ \t]*)?\{%- if mlock %\}(.*?)\{%- endif %\}", _mlock_sub,
        content, flags=re.DOTALL,
    )

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

    # A dropped block leaves its indentation and a doubled blank line behind
    content = re.sub(r"[ \t]+\n", "\n", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.rstrip("\n") + "\n"


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
    context: dict[str, object],
    dry_run: bool,
    executable: bool = False,
) -> int:
    """Render a template and write it; return 1 if created, 0 otherwise."""
    log.debug(f"scaffold {template!r} → {path}")
    content = _render_template(template, context, generator=path.suffix == ".j2")
    if content and _write_file(path, content, dry_run):
        if executable and not dry_run:
            path.chmod(0o755)
        return 1
    return 0


def _looks_like_workspace_root(d: Path) -> bool:
    """A directory holding image repos (not an image repo itself)."""
    if (d / "compose.yaml").exists() or (d / ".daemonless").exists():
        return False
    try:
        images = [p for p in d.iterdir() if (p / ".daemonless" / "config.yaml").is_file()]
    except OSError:
        return False
    return len(images) >= 2


def _run_hints(meta: dict[str, object]) -> str:
    """Comment block for the launcher: what the port's rc script does."""
    lines: list[str] = []
    rc_file = meta.get("rc_file")
    if rc_file:
        rc_path = Path(str(rc_file))
        rel = rc_path.relative_to(rc_path.parents[3]) if len(rc_path.parents) > 3 else rc_path
        lines.append(f"# From the port's rc script ({rel}). Map rc variables such as")
        lines.append("# conf dirs to container paths (/config, /data); drop daemonizing flags.")
        lines.extend(f"#   {h}" for h in meta.get("rc_hints", []))
    others = [
        os.path.basename(str(b)) for b in meta.get("binaries", []) if b != meta.get("binary")
    ]
    if others:
        lines.append("# Other programs in the package: " + ", ".join(others))
    if not lines:
        return ""
    return "\n" + "\n".join(lines) + "\n"


_EMPTY_META: dict[str, object] = {
    "origin": "", "pkgname": "", "version": "", "source": "", "description": "",
    "license": "UNKNOWN", "license_raw": "", "license_unmapped": [], "web_url": "",
    "category": "", "category_raw": "", "binary": "", "binary_source": "",
    "extra_args": "", "rc_file": None, "port": 0, "port_source": "",
    "port_candidates": [], "flavors": [], "in_repo": True,
}


def _port_summary(meta: dict[str, object], title: str, port: int, port_from_arg: bool,
                  variants: list[str], flavors: list[str],
                  category_from_arg: bool = False) -> list[str]:
    """Log the metadata table; return the review items for the next-steps block."""
    review: list[str] = []
    meta = {**_EMPTY_META, **meta}
    pkg = f"{meta['pkgname']} {meta['version']}".strip()
    log.step("Port metadata")
    log.success(f"port:        {meta['origin']} ({pkg}, via {meta['source']})")
    log.success(f"title:       {title}  (--title to override)")
    log.success(f"description: {meta['description'] or '(no COMMENT)'}")

    if meta["license"] == "UNKNOWN":
        raw = meta["license_raw"] or "unset"
        log.warn(f"license:     UNKNOWN  (port says: {raw})")
        review.append(
            f"license: port LICENSE is '{raw}' with no SPDX mapping — read the upstream "
            "LICENSE and set org.opencontainers.image.licenses in Containerfile.pkg.j2"
        )
    else:
        log.success(f"license:     {meta['license']}  (port: {meta['license_raw']})")

    if meta["web_url"]:
        log.success(f"web:         {meta['web_url']}")
    else:
        log.warn("web:         (WWW unset in port)")
        review.append("web_url/upstream_url in compose.yaml: the port has no WWW")

    if meta["category"]:
        log.success(f"category:    {meta['category']}  (port: {meta['category_raw']})")
        if meta["category_raw"] == "www" and not category_from_arg:
            review.append(
                "category: www/ ports land in Network; a git forge is Infrastructure, "
                "a wiki or notes app Productivity (compose.yaml, or --category)"
            )
    else:
        log.warn(f"category:    Utilities  (no mapping for '{meta['category_raw']}')")
        review.append("category in compose.yaml (--category next time)")

    if meta["binary"]:
        log.success(
            f"daemon:      {meta['binary']} {meta['extra_args']}".rstrip()
            + f"  ({meta['binary_source']})"
        )
    elif meta["source"] == "pkg repo":
        log.warn("daemon:      unknown (no ports tree to read the rc script or plist)")
        review.append("launcher: set the daemon binary and flags")
    else:
        log.warn("daemon:      no rc script and no bin/ entries in the plist")
        review.append("launcher: set the daemon binary and flags")
    if meta.get("rc_file"):
        review.append("launcher: turn the rc flags in the comment into container flags")

    if port_from_arg:
        log.success(f"web port:    {port}  (--port)")
    elif meta["port"]:
        log.success(f"web port:    {port}  (from {meta['port_source']})")
    else:
        cands = meta["port_candidates"]
        hint = f"candidates in {meta['port_source']}: {', '.join(map(str, cands))}" if cands else "nothing in the rc script or pkg-message"
        log.warn(f"web port:    {port}  (default; {hint})")
        review.append(
            f"web port: {port} is a guess — fix cit.port and the compose ports "
            "(the Containerfile follows compose), or pass --port next time"
        )

    log.success(f"variants:    {', '.join(variants)}")
    if meta["flavors"] and not flavors:
        log.info(
            f"flavors:     {', '.join(meta['flavors'])}  "
            "(default flavor used; --flavors a,b builds one tag per flavor)"
        )
    if meta.get("sibling_packages") and len(meta["sibling_packages"]) > 1:
        log.info(f"packages with this origin: {', '.join(meta['sibling_packages'])}")

    if not meta["in_repo"]:
        log.warn(f"package '{meta['pkgname']}' is not in the pkg repo — the pkg build will fail until it is published")
    if meta["license_unmapped"] and meta["license"] == "UNKNOWN" and meta["license_raw"] not in _LICENSE_UNSET:
        log.warn(f"no SPDX mapping for license token(s): {', '.join(meta['license_unmapped'])}")
    return review


def _next_steps(base: Path, app_name: str, review: list[str], from_port: bool) -> None:
    rel = base.relative_to(Path.cwd()) if base != Path.cwd() and base.is_relative_to(Path.cwd()) else None
    log.step("Next steps")
    n = 0
    if rel:
        n += 1
        log.plain(f"  {n}. cd {rel}")
    n += 1
    log.plain(f"  {n}. edit root/daemonless/libexec/{app_name} (and init.d/20-{app_name} for dirs to create)")
    for item in review:
        log.plain(f"       - {item}")
    if not from_port:
        log.plain("       - set the daemon binary and flags")
    n += 1
    log.plain(f"  {n}. dbuild logo <url>        # .daemonless/logo.svg (or .png)")
    n += 1
    log.plain(f"  {n}. dbuild generate          # Containerfile(.pkg) + README.md from the .j2 templates")
    n += 1
    log.plain(f"  {n}. dbuild build && dbuild test")


def run(args: argparse.Namespace) -> int:
    """Scaffold a new dbuild project in the current directory."""
    port_spec = getattr(args, "freebsd_port", None)
    flavors = [f.strip() for f in (getattr(args, "flavors", None) or "").split(",") if f.strip()]
    if flavors and not port_spec:
        log.error("--flavors requires --freebsd-port (flavors are a FreeBSD ports concept)")
        return 1

    # A port scaffold is a pkg image unless told otherwise; a generic one
    # still starts from the upstream-binary template.
    variants_arg = getattr(args, "variants", None) or (_PORT_VARIANTS if port_spec else "latest")
    variants = [v.strip() for v in variants_arg.split(",") if v.strip()]
    has_pkg = any(v.startswith("pkg") for v in variants)
    wants_source = "latest" in variants or not has_pkg

    port_meta: dict[str, object] = {}
    if port_spec:
        fetched = _fetch_port_metadata(port_spec, flavors=flavors, want_run_deps=wants_source)
        if fetched is None:
            log.error("aborting: nothing scaffolded (fix the port spec, or drop --freebsd-port)")
            return 1
        port_meta = fetched

    app_name = args.name or port_meta.get("name") or Path.cwd().name
    # node_exporter -> Node Exporter; a brand's own casing is a --title job
    title = args.title or " ".join(w.capitalize() for w in app_name.split("_"))
    category_from_arg = args.category != "Apps"
    category = (
        args.category if category_from_arg
        else (port_meta.get("category") or "Utilities")
    )
    port_arg = getattr(args, "port", None)
    port = int(port_arg) if port_arg else (int(port_meta.get("port") or 0) or _DEFAULT_PORT)
    dry_run = args.dry_run
    mlock = "true" if args.type == "dotnet" else "false"
    pkgname = str(port_meta.get("pkgname") or app_name)
    packages = str(port_meta.get("packages") or app_name)

    target = getattr(args, "dir", None)
    if target:
        base = Path(target).resolve()
    elif port_spec and _looks_like_workspace_root(Path.cwd()):
        base = Path.cwd() / app_name
        log.info(f"current directory holds image repos — scaffolding into ./{app_name}/")
    else:
        base = Path.cwd()

    if port_meta:
        binary = port_meta.get("binary") or f"/usr/local/bin/{app_name}"
        extra = port_meta.get("extra_args") or ""
        run_binary = f"{binary} {extra}".rstrip()
        run_args = ""
        run_hints = _run_hints(port_meta)
    else:
        run_binary = f"/usr/local/bin/{app_name}"
        run_args = " \\\n    --config /config \\\n    --data /data"
        run_hints = ""

    context: dict[str, object] = {
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
        "community":      args.community or DEFAULT_COMMUNITY,
        "variants":       variants,
        "pkgname":        pkgname,
        "packages_arg":   "${PKG_NAME}" if packages == pkgname else packages,
        "run_deps":       port_meta.get("run_deps") or app_name,
        "license":        port_meta.get("license") or "UNKNOWN",
        "run_binary":     run_binary,
        "run_args":       run_args,
        "run_hints":      run_hints,
        "build_block":    _build_block(
            variants, port_meta.get("flavor_pkgnames") or {}, pkgname,
        ),
    }

    review: list[str] = []
    if port_meta:
        review = _port_summary(
            port_meta, title, port, bool(port_arg), variants, flavors, category_from_arg,
        )
        if wants_source and not port_meta.get("run_deps"):
            log.warn(f"no runtime deps found for '{port_meta['pkgname']}' — PACKAGES in Containerfile.j2 needs a manual list")

    log.debug(f"variants: {variants}  has_pkg: {has_pkg}  dry_run: {dry_run}  base: {base}")
    log.debug("context:\n" + "\n".join(f"  {k} = {v!r}" for k, v in context.items()))

    created = 0
    created += _scaffold("config.yaml", base / ".daemonless" / "config.yaml", context, dry_run)
    created += _scaffold("compose.yaml", base / "compose.yaml", context, dry_run)
    if wants_source:
        created += _scaffold("template-upstream.j2", base / "Containerfile.j2", context, dry_run)
    if has_pkg:
        created += _scaffold("template-pkg.j2", base / "Containerfile.pkg.j2", context, dry_run)

    # base-exec: /init runs init.d as root, then execs the launcher (CMD) as
    # bsd. Health is the base's /healthz reading HEALTHCHECK_URL.
    daemonless = base / "root" / "daemonless"
    created += _scaffold(
        "launcher.sh", daemonless / "libexec" / app_name, context, dry_run, executable=True,
    )
    created += _scaffold(
        "setup.sh", daemonless / "init.d" / f"20-{app_name}", context, dry_run, executable=True,
    )

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
        return 0
    label = "would be created" if dry_run else "created"
    log.step(f"scaffolded {created} file(s) ({label})")
    _next_steps(base, app_name, review, from_port=bool(port_meta))
    return 0
