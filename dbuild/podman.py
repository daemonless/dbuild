"""Thin wrapper around podman and buildah commands.

This module has ZERO business logic.  It does not know about config,
variants, CI, or registries.  It runs commands and returns output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from dbuild import log


class PodmanError(Exception):
    """Raised when a podman/buildah command fails."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Command failed (rc={returncode}): {' '.join(cmd)}\n{stderr}"
        )


# ── Privilege escalation ─────────────────────────────────────────────

def _needs_privilege() -> bool:
    """Return True if we need privilege escalation (not root)."""
    return os.getuid() != 0


def _priv_prefix() -> list[str]:
    """Return ``["doas"]`` or ``["sudo"]`` when needed, else ``[]``."""
    if not _needs_privilege():
        return []
    if shutil.which("doas"):
        return ["doas"]
    if shutil.which("sudo"):
        return ["sudo"]
    return []


# ── Internal helpers ──────────────────────────────────────────────────

def _run(
    cmd: list[str],
    *,
    capture: bool = True,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, log it, capture output, optionally raise on failure.

    Pass ``quiet=True`` to suppress the info log line (useful for polling
    loops that would otherwise flood the output).
    """
    cmd = _priv_prefix() + cmd
    if not quiet:
        log.info(f"$ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise PodmanError(cmd, result.returncode, stderr)
    return result


# ── Podman commands ───────────────────────────────────────────────────

def build(
    containerfile: str,
    tag: str,
    *,
    build_args: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
    context_dir: str = ".",
    network: str = "host",
    extra_args: list[str] | None = None,
) -> str:
    """Run ``podman build`` and return the image ID.

    Build output is streamed to the terminal so the user can follow progress.
    """
    cmd = [
        "podman", "build",
        "-f", containerfile,
        "-t", tag,
        f"--network={network}",
    ]
    for key, val in (build_args or {}).items():
        cmd += ["--build-arg", f"{key}={val}"]
    for name, value in (secrets or {}).items():
        cmd += ["--secret", f"id={name},env={value}"]
    if extra_args:
        cmd += extra_args
    cmd.append(context_dir)

    # Stream output so the user sees build progress
    _run(cmd, capture=False)
    return tag


def run_in(image: str, cmd: list[str] | str) -> str:
    """Run *cmd* inside a disposable container and return stdout.

    Uses ``--entrypoint=""`` to bypass s6-overlay or other entrypoints
    that would prevent direct command execution.
    """
    run_cmd = ["podman", "run", "--rm", "--entrypoint", "", image]
    if isinstance(cmd, str):
        run_cmd += ["sh", "-c", cmd]
    else:
        run_cmd += cmd
    result = _run(run_cmd)
    return result.stdout.strip()


def tag(src: str, dest: str) -> None:
    """Tag an image."""
    _run(["podman", "tag", src, dest])


def login(host: str, username: str, password: str) -> None:
    """Login to a container registry via ``podman login``."""
    cmd = [
        *_priv_prefix(),
        "podman", "login", host, "-u", username, "--password-stdin",
    ]
    log.info(f"$ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        input=password,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise PodmanError(cmd, result.returncode, stderr)


def push(image_ref: str) -> None:
    """Push an image to a registry."""
    _run(["podman", "push", image_ref])


def images(filter_expr: str | None = None) -> list[dict[str, Any]]:
    """List images, optionally filtered.  Returns parsed JSON."""
    cmd = ["podman", "images", "--format", "json"]
    if filter_expr:
        cmd += ["--filter", filter_expr]
    result = _run(cmd)
    return json.loads(result.stdout) if result.stdout.strip() else []


def image_exists(ref: str) -> bool:
    """Return True if *ref* exists in local storage."""
    result = _run(
        ["podman", "image", "exists", ref],
        check=False,
    )
    return result.returncode == 0


def run_detached(
    image: str,
    *,
    name: str,
    network: str = "podman",
    annotations: dict[str, str] | None = None,
) -> str:
    """Run a container in the background.  Returns container ID."""
    cmd = ["podman", "run", "-d", "--name", name, f"--network={network}"]
    for key, val in (annotations or {}).items():
        cmd += ["--annotation", f"{key}={val}"]
    cmd.append(image)
    result = _run(cmd)
    return result.stdout.strip()


def inspect_labels(image_ref: str) -> dict[str, str]:
    """Return all labels from an image."""
    result = _run(
        ["podman", "inspect", "--format", "{{json .Config.Labels}}", image_ref],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        labels = json.loads(result.stdout.strip())
        return labels if isinstance(labels, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def inspect_ip(container_name: str) -> str:
    """Return the IP address of a running container."""
    result = _run([
        "podman", "inspect", "--format",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        container_name,
    ])
    return result.stdout.strip()


def container_running(container_name: str, *, quiet: bool = False) -> bool:
    """Return True if the named container is running."""
    result = _run(
        ["podman", "ps", "-q", "--filter", f"name={container_name}"],
        check=False,
        quiet=quiet,
    )
    return bool(result.stdout.strip())


def logs(container_name: str, *, quiet: bool = False) -> str:
    """Return current container logs (stdout + stderr merged)."""
    result = _run(
        ["podman", "logs", container_name],
        check=False,
        quiet=quiet,
    )
    # podman logs sends app stdout to stdout and app stderr to stderr
    output = result.stdout or ""
    if result.stderr:
        output += result.stderr
    return output


def exec_in(container_name: str, cmd: list[str] | str) -> subprocess.CompletedProcess[str]:
    """Exec a command in a running container."""
    exec_cmd = ["podman", "exec", container_name]
    if isinstance(cmd, str):
        exec_cmd += ["/bin/sh", "-c", cmd]
    else:
        exec_cmd += cmd
    return _run(exec_cmd, check=False)


def stop(container_name: str) -> None:
    """Stop a running container (ignores errors)."""
    _run(["podman", "stop", container_name], check=False)


def rm(container_name: str, *, force: bool = True) -> None:
    """Remove a container."""
    cmd = ["podman", "rm"]
    if force:
        cmd.append("-f")
    cmd.append(container_name)
    _run(cmd, check=False)


def compose_up(compose_file: str) -> None:
    """Start a compose stack in the background."""
    _run(["podman-compose", "-f", compose_file, "up", "-d"], capture=False)


def compose_down(compose_file: str) -> None:
    """Tear down a compose stack (ignores errors)."""
    _run(["podman-compose", "-f", compose_file, "down"], check=False)


def compose_logs(compose_file: str, tail: int = 20) -> str:
    """Return recent logs from all compose services."""
    result = _run(
        ["podman-compose", "-f", compose_file, "logs", "--tail", str(tail)],
        check=False,
    )
    output = result.stdout or ""
    if result.stderr:
        output += result.stderr
    return output


# ── Buildah commands ──────────────────────────────────────────────────

def bah_from(image: str) -> str:
    """Create a working container from *image*.  Returns container ID."""
    result = _run(["buildah", "from", "--pull=never", image])
    return result.stdout.strip()


def bah_config(container_id: str, *, labels: dict[str, str] | None = None) -> None:
    """Apply configuration to a buildah working container."""
    if not labels:
        return
    cmd = ["buildah", "config"]
    for key, val in labels.items():
        cmd += ["--label", f"{key}={val}"]
    cmd.append(container_id)
    _run(cmd)


def bah_commit(container_id: str, image: str) -> str:
    """Commit a working container as an image.  Returns image ID."""
    result = _run(["buildah", "commit", container_id, image])
    return result.stdout.strip()


def bah_rm(container_id: str) -> None:
    """Remove a buildah working container."""
    _run(["buildah", "rm", container_id])


def bah_mount(container_id: str) -> str:
    """Mount a working container's rootfs.  Returns mount path."""
    result = _run(["buildah", "mount", container_id])
    return result.stdout.strip()


def bah_umount(container_id: str) -> None:
    """Unmount a working container's rootfs."""
    _run(["buildah", "umount", container_id])
