"""Docker Hub registry backend.

Inherits from :class:`~dbuild.registry.generic.GenericRegistry` and adds
support for Docker Hub mirroring via ``skopeo copy``.
"""

from __future__ import annotations

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
