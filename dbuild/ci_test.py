"""CI environment preflight checks.

Verifies that the build environment has all required tools and
configuration without modifying anything.  Safe to run on any host.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess

from dbuild import ci as ci_mod
from dbuild import log

# ── Required tools ───────────────────────────────────────────────────

_REQUIRED_TOOLS = [
    "podman",
    "buildah",
    "skopeo",
    "jq",
    "trivy",
]

_OPTIONAL_TOOLS = [
    "podman-compose",
]


# ── Checks ───────────────────────────────────────────────────────────


def _check_tool(name: str) -> bool:
    """Return True if *name* is on PATH."""
    return shutil.which(name) is not None


def _check_podman_info() -> bool:
    """Verify podman can talk to its backend."""
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "{{.Host.OCIRuntime.Name}}"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            log.error(f"podman info failed: {result.stderr.strip()}")
            return False
        runtime = result.stdout.strip()
        log.info(f"OCI runtime: {runtime}")
        if runtime == "ocijail":
            log.success("ocijail detected")
        else:
            log.warn(f"expected ocijail, got {runtime}")
        return True
    except FileNotFoundError:
        log.error("podman not found")
        return False


def _check_ip_forwarding() -> bool:
    """Check if IP forwarding is enabled."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "net.inet.ip.forwarding"],
            capture_output=True, text=True, check=False,
        )
        val = result.stdout.strip()
        if val == "1":
            return True
        log.warn(f"net.inet.ip.forwarding={val} (expected 1)")
        return False
    except FileNotFoundError:
        # Not FreeBSD — skip
        return True


def _check_pf_loaded() -> bool:
    """Check if the pf kernel module is loaded."""
    try:
        result = subprocess.run(
            ["kldstat", "-q", "-m", "pf"],
            capture_output=True, text=True, check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return True  # not FreeBSD


def _check_ocijail_annotation(annotation: str, base_image: str) -> bool:
    """Test if ocijail accepts a jail annotation by running a throwaway container.

    Returns True if the container ran successfully with the annotation.
    """
    from dbuild import podman
    cmd = [
        *podman._priv_prefix(),
        "podman", "run", "--rm",
        "--annotation", f"{annotation}=true",
        base_image,
        "/bin/echo", "ok",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode == 0 and "ok" in result.stdout


def _find_base_image() -> str | None:
    """Find a usable FreeBSD base image for annotation tests."""
    from dbuild import podman
    # Look for any local FreeBSD base image
    cmd = [
        *podman._priv_prefix(),
        "podman", "images", "--format", "{{.Repository}}:{{.Tag}}",
        "--filter", "reference=*freebsd*",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()[0]

    # Fall back to any local image
    cmd = [
        *podman._priv_prefix(),
        "podman", "images", "--format", "{{.Repository}}:{{.Tag}}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        lines = [
            line for line in result.stdout.strip().splitlines()
            if "<none>" not in line
        ]
        if lines:
            return lines[0]

    return None


# Jail annotations needed by specific app types
_JAIL_ANNOTATIONS = {
    "org.freebsd.jail.allow.mlock": ".NET apps (Radarr, Sonarr, etc.)",
    "org.freebsd.jail.allow.sysvipc": "PostgreSQL",
}


def _check_ocijail_annotations() -> tuple[int, int]:
    """Test ocijail jail annotation support.

    Returns (passed, warned) counts.
    """
    base_image = _find_base_image()
    if base_image is None:
        log.warn("No local images found -- skipping annotation tests")
        log.warn("Pull or build an image first, then re-run")
        return 0, 0

    log.info(f"Using image: {base_image}")
    passed = 0
    warned = 0
    for annotation, description in _JAIL_ANNOTATIONS.items():
        short = annotation.rsplit(".", 1)[-1]
        if _check_ocijail_annotation(annotation, base_image):
            log.success(f"{short} supported (needed by {description})")
            passed += 1
        else:
            log.warn(f"{short} NOT supported -- {description} will fail")
            log.warn("  install patched ocijail from daemonless/freebsd-ports")
            warned += 1
    return passed, warned


def _check_ci_env() -> None:
    """Detect and report the CI environment."""
    backend = ci_mod.detect()
    name = type(backend).__name__
    log.info(f"CI backend: {name}")
    if hasattr(backend, "is_pr"):
        log.info(f"PR build: {backend.is_pr()}")


# ── Public API ───────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    """Run all preflight checks.

    Returns ``0`` if all required checks pass, ``1`` otherwise.
    """
    log.step("CI Preflight Checks")
    failed = 0

    # Required tools
    log.step("Required tools")
    for tool in _REQUIRED_TOOLS:
        if _check_tool(tool):
            path = shutil.which(tool)
            log.success(f"{tool} -> {path}")
        else:
            log.error(f"{tool} not found")
            failed += 1

    # Optional tools
    log.step("Optional tools")
    for tool in _OPTIONAL_TOOLS:
        if _check_tool(tool):
            path = shutil.which(tool)
            log.success(f"{tool} -> {path}")
        else:
            log.info(f"{tool} not found (optional)")

    # Podman runtime
    log.step("Podman runtime")
    if not _check_podman_info():
        failed += 1

    # Networking
    log.step("Networking")
    if _check_pf_loaded():
        log.success("pf module loaded")
    else:
        log.error("pf module not loaded")
        failed += 1

    if _check_ip_forwarding():
        log.success("IP forwarding enabled")
    else:
        failed += 1

    # ocijail annotations
    log.step("Jail annotations (ocijail)")
    _ann_passed, ann_warned = _check_ocijail_annotations()

    # CI environment
    log.step("CI Environment")
    _check_ci_env()

    # Summary
    log.step("Summary")
    if failed:
        log.error(f"{failed} check(s) failed")
        return 1

    if ann_warned:
        log.warn(f"{ann_warned} annotation(s) unsupported -- some builds may fail")

    log.success("All checks passed -- ready to build")
    return 0
