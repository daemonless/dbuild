"""Generic OCI registry backend.

Works with any OCI-compliant registry.  Uses ``podman push`` for
pushing and ``skopeo`` for inspect and copy operations.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from dbuild import log, podman
from dbuild.registry import RegistryBase


class GenericRegistry(RegistryBase):
    """Backend for any OCI-compliant registry."""

    def __init__(self, url: str, token: str | None = None) -> None:
        self.url = url
        self.token = token

    def login(self, token: str, actor: str) -> None:
        """Login via ``podman login``."""
        host = self._registry_host()
        log.info(f"Logging in to {host} as {actor}")
        podman.login(host, actor, token)
        log.success(f"Logged in to {host}")

    def push(self, image: str, tag: str) -> None:
        """Push *image:tag* via ``podman push``."""
        ref = f"{image}:{tag}"
        log.info(f"Pushing {ref}")
        podman.push(ref)
        log.success(f"Pushed {ref}")

    def inspect(self, image_ref: str) -> dict[str, Any] | None:
        """Inspect a remote image via ``skopeo inspect``."""
        ref = f"docker://{image_ref}"
        log.info(f"Inspecting {ref}")
        cmd = [*podman._priv_prefix(), "skopeo", "inspect", ref]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            log.warn(f"skopeo inspect failed for {ref}: {proc.stderr.strip()}")
            return None
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            log.warn(f"Could not parse skopeo inspect output for {ref}")
            return None

    def copy(self, src: str, dest: str) -> None:
        """Copy between registries via ``skopeo copy``."""
        src_ref = f"docker://{src}"
        dest_ref = f"docker://{dest}"
        log.info(f"Copying {src} -> {dest}")
        cmd = [*podman._priv_prefix(), "skopeo", "copy", src_ref, dest_ref]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"skopeo copy failed: {proc.stderr.strip()}"
            )
        log.success(f"Copied {src} -> {dest}")

    def _registry_host(self) -> str:
        """Extract the registry hostname from self.url."""
        url = self.url
        # Strip protocol if present.
        for prefix in ("https://", "http://"):
            if url.startswith(prefix):
                url = url[len(prefix):]
                break
        # Return the first path component (hostname or hostname/org).
        return url.split("/")[0]
