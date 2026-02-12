"""FreeBSD CI environment preparation.

Sets up the build environment for CI runners: configures the pkg repo,
installs required packages, installs the patched ocijail, cleans stale
container state, and configures networking (pf + IP forwarding).

Each step is idempotent and safe to re-run.  Requires root.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess

from dbuild import ci as ci_mod
from dbuild import log
from dbuild.ci.local import LocalCI

# ── Constants ────────────────────────────────────────────────────────

_OCIJAIL_URL_TEMPLATE = (
    "https://github.com/daemonless/freebsd-ports/releases/download/"
    "v0.4.0-patched/ocijail-0.4.0_2-{arch}.pkg"
)

_PKG_LIST = [
    "podman", "jq", "skopeo", "buildah", "trivy",
    "python3", "py311-yaml",
]

_COMPOSE_PKG = "podman-compose"

# ── Helpers ──────────────────────────────────────────────────────────


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, logging it first.  Raises on failure."""
    log.info(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def _detect_arch() -> str:
    """Detect the FreeBSD architecture label (amd64 / aarch64)."""
    machine = platform.machine()
    mapping = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    return mapping.get(machine, machine)


# ── Steps ────────────────────────────────────────────────────────────


def configure_pkg_repo() -> None:
    """Write /etc/pkg/FreeBSD.conf for the 'latest' package set."""
    log.step("Configuring pkg repository")
    os.makedirs("/etc/pkg", exist_ok=True)
    with open("/etc/pkg/FreeBSD.conf", "w") as f:
        f.write('FreeBSD: { url: "http://pkg.FreeBSD.org/${ABI}/latest" }\n')
    _run(["pkg", "update", "-f"])


def install_packages(*, compose: bool = False) -> None:
    """Install required packages via pkg."""
    log.step("Installing packages")
    pkgs = list(_PKG_LIST)
    if compose:
        pkgs.append(_COMPOSE_PKG)
    _run(["pkg", "install", "-y", *pkgs])


def install_ocijail(*, arch: str | None = None) -> None:
    """Fetch and install the patched ocijail package."""
    log.step("Installing patched ocijail")
    if arch is None:
        arch = _detect_arch()
    url = _OCIJAIL_URL_TEMPLATE.format(arch=arch)
    pkg_path = "/tmp/ocijail.pkg"
    _run(["fetch", "-qo", pkg_path, url])
    _run(["pkg", "install", "-fy", pkg_path])


def cleanup_containers() -> None:
    """Remove stale container storage directories."""
    log.step("Cleaning container storage")
    for path in ("/var/db/containers", "/var/lib/containers"):
        _run(["rm", "-rf", path])


def configure_networking() -> None:
    """Load pf kernel module and enable IP forwarding."""
    log.step("Configuring networking")
    _run(["kldload", "pf"])
    _run(["sysctl", "net.inet.ip.forwarding=1"])


# ── Public API ───────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    """Run the full CI environment preparation.

    Parameters
    ----------
    args:
        CLI arguments.  Recognised attributes:

        * ``arch``    -- target architecture override (optional).
        * ``compose`` -- install podman-compose (optional).

    Returns ``0`` on success, ``1`` on failure.
    """
    if os.geteuid() != 0:
        log.error("ci-prepare must run as root")
        return 1

    # Warn when running outside CI (bare metal / production host)
    backend = ci_mod.detect()
    if isinstance(backend, LocalCI):
        log.warn("No CI environment detected -- running on bare metal")
        log.warn("This will: install packages, overwrite /etc/pkg/FreeBSD.conf,")
        log.warn("           wipe container storage, load pf, enable IP forwarding")
        try:
            answer = input("[warn] Continue? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if answer.strip().lower() not in ("y", "yes"):
            log.info("Aborted")
            return 1

    arch: str | None = getattr(args, "arch", None)
    compose: bool = getattr(args, "compose", False)

    try:
        configure_pkg_repo()
        install_packages(compose=compose)
        install_ocijail(arch=arch)
        cleanup_containers()
        configure_networking()
    except subprocess.CalledProcessError as exc:
        log.error(f"Command failed: {exc}")
        return 1

    log.success("CI environment ready")
    return 0
