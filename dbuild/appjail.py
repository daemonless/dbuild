"""Thin wrapper around appjail commands.

This module has ZERO business logic.  It runs commands and returns output.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile

from dbuild import log


class AppJailError(Exception):
    """Raised when an appjail command fails."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Command failed (rc={returncode}): {' '.join(cmd)}\n{stderr}"
        )


# ── Privilege escalation ─────────────────────────────────────────────

def _priv_prefix() -> list[str]:
    """Return ``["doas"]`` or ``["sudo"]`` when not root, else ``[]``."""
    if os.getuid() == 0:
        return []
    if shutil.which("doas"):
        return ["doas"]
    if shutil.which("sudo"):
        return ["sudo"]
    return []


# ── Internal helper ───────────────────────────────────────────────────

def _run(
    cmd: list[str],
    *,
    capture: bool = True,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = _priv_prefix() + cmd
    if not quiet:
        log.info(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise AppJailError(cmd, result.returncode, stderr)
    return result


# ── AppJail jail commands ─────────────────────────────────────────────

def jail_running(jail_name: str) -> bool:
    """Return True if *jail_name* is running."""
    result = _run(["appjail", "status", "-q", jail_name], check=False, quiet=True)
    return result.returncode == 0


def get_ip(jail_name: str) -> str:
    """Return the first IPv4 address assigned to *jail_name*."""
    result = _run(
        ["appjail", "jail", "get", "-I", jail_name, "network_ip4"],
        check=False,
    )
    raw = result.stdout.strip()
    if not raw or raw == "-":
        return ""
    return raw.split()[0]


def exec_in(jail_name: str, cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Exec *cmd* inside *jail_name* via ``appjail cmd jexec``."""
    return _run(["appjail", "cmd", "jexec", jail_name, *cmd], check=False)


def logs(jail_name: str, *, quiet: bool = False) -> str:
    """Return recent log output for *jail_name* (best-effort).

    Checks ``jails/<name>/container`` (OCI run) then ``jails/<name>/console``
    (classic jail), returning whatever is found first.
    """
    for log_path in (f"jails/{jail_name}/container", f"jails/{jail_name}/console"):
        list_result = _run(
            ["appjail", "logs", "list", "-H", "-p", log_path],
            check=False,
            quiet=True,
        )
        if list_result.returncode != 0 or not list_result.stdout.strip():
            continue
        log_name = list_result.stdout.strip().splitlines()[-1].strip()
        if not log_name:
            continue
        read_result = _run(
            ["appjail", "logs", "read", f"{log_path}/{log_name}"],
            check=False,
            quiet=quiet,
        )
        return (read_result.stdout or "") + (read_result.stderr or "")
    return ""


# ── OCI run / stop / destroy ─────────────────────────────────────────

def oci_run(jail_name: str, image_ref: str, *, allow: list[str] | None = None) -> None:
    """Start *jail_name* from *image_ref* via ``appjail oci run -d``.

    *image_ref* is a fully-qualified image reference from the local
    podman/buildah store (e.g. ``ghcr.io/daemonless/app:tag``).
    The ``containers-storage:`` transport prefix is added automatically.

    *allow* is a list of ``allow.*`` jail parameters to enable
    (e.g. ``["allow.mlock"]``).  ``allow.socket_af`` and
    ``allow.reserved_ports`` are always enabled so jails can bind sockets.

    A temporary template file is written to pass these parameters because
    ``appjail oci run -o`` only accepts high-level "quick" options, not raw
    jail parameters.
    """
    # Build the template lines
    base_allows = {"allow.socket_af", "allow.reserved_ports"}
    extra = {f"allow.{a.removeprefix('allow.')}" for a in (allow or [])}
    all_allows = base_allows | extra

    template_lines = [
        'exec.start: "/bin/sh /etc/rc"',
        'exec.stop: "/bin/sh /etc/rc.shutdown jail"',
        "mount.devfs",
        "persist",
        "ip4: inherit",   # allow socket binding (required for services)
        "ip6: inherit",
        *sorted(all_allows),
    ]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".conf", prefix="dbuild-cit-", delete=False
    ) as tf:
        tf.write("\n".join(template_lines) + "\n")
        template_path = tf.name

    try:
        cmd = ["appjail", "oci", "run", "-d",
               "-o", f"template={template_path}",
               "-o", "tzdata=UTC",
               f"containers-storage:{image_ref}", jail_name]
        _run(cmd, capture=False)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(template_path)


def jail_stop(jail_name: str) -> None:
    """Stop *jail_name* (ignores errors)."""
    _run(["appjail", "stop", jail_name], check=False)


def jail_destroy(jail_name: str) -> None:
    """Force-destroy *jail_name* and all its data (ignores errors)."""
    _run(["appjail", "jail", "destroy", "-fR", jail_name], check=False)


def list_jails() -> list[str]:
    """Return a list of all jail names."""
    result = _run(["appjail", "jail", "list"], check=False, quiet=True)
    if result.returncode != 0 or not result.stdout.strip():
        return []

    jails = []
    for line in result.stdout.strip().splitlines()[1:]:  # Skip header
        parts = line.split()
        if parts:
            jails.append(parts[1])
    return jails


def jail_exists(jail_name: str) -> bool:
    """Return True if *jail_name* exists."""
    result = _run(["appjail", "jail", "get", "-I", jail_name, "name"], check=False, quiet=True)
    return result.returncode == 0
