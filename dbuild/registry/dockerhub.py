"""Docker Hub registry backend.

Inherits from :class:`~dbuild.registry.generic.GenericRegistry` and adds
support for Docker Hub mirroring (via ``skopeo copy``) and repository
description updates.
"""

from __future__ import annotations

import json
import subprocess

from dbuild import log, podman
from dbuild.registry.generic import GenericRegistry


class DockerHub(GenericRegistry):
    """Backend for Docker Hub (docker.io)."""

    def login(self, token: str, actor: str) -> None:
        """Login to Docker Hub via ``podman login``."""
        log.info(f"Logging in to docker.io as {actor}")
        podman.login("docker.io", actor, token)
        log.success("Logged in to docker.io")

    def mirror_from(self, src: str, dest: str) -> None:
        """Mirror an image from another registry to Docker Hub via skopeo.

        This is a convenience wrapper around :meth:`copy` that logs the
        operation as a mirror.
        """
        log.info(f"Mirroring {src} -> {dest} (Docker Hub)")
        self.copy(src, dest)

    def update_description(
        self,
        repo: str,
        description: str,
        *,
        username: str,
        password: str,
    ) -> None:
        """Update the Docker Hub repository description.

        Uses the Docker Hub API v2 to PATCH the repository description.
        Requires Docker Hub credentials (not a GHCR token).
        """
        # Obtain a JWT from Docker Hub.
        login_payload = json.dumps({
            "username": username,
            "password": password,
        })
        login_proc = subprocess.run(
            [
                "fetch", "-qo", "-",
                "--method", "POST",
                "--header", "Content-Type: application/json",
                "--body", login_payload,
                "https://hub.docker.com/v2/users/login/",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if login_proc.returncode != 0:
            log.warn(f"Docker Hub login for description update failed: {login_proc.stderr.strip()}")
            return
        try:
            jwt = json.loads(login_proc.stdout).get("token")
        except (json.JSONDecodeError, AttributeError):
            log.warn("Could not parse Docker Hub login response")
            return
        if not jwt:
            log.warn("No token in Docker Hub login response")
            return

        # PATCH the description.
        patch_payload = json.dumps({"description": description})
        patch_proc = subprocess.run(
            [
                "fetch", "-qo", "-",
                "--method", "PATCH",
                "--header", "Content-Type: application/json",
                "--header", f"Authorization: JWT {jwt}",
                "--body", patch_payload,
                f"https://hub.docker.com/v2/repositories/{repo}/",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if patch_proc.returncode != 0:
            log.warn(f"Docker Hub description update failed: {patch_proc.stderr.strip()}")
        else:
            log.success(f"Updated Docker Hub description for {repo}")
