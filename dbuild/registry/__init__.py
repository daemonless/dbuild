"""Registry backend abstraction and factory.

Use :func:`for_url` to obtain a registry instance -- never import a
backend class directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RegistryBase(ABC):
    """Abstract base class for OCI registries."""

    @abstractmethod
    def login(self, token: str, actor: str) -> None:
        """Authenticate to the registry."""

    @abstractmethod
    def push(self, image: str, tag: str) -> None:
        """Push a tagged image to the registry."""

    @abstractmethod
    def inspect(self, image_ref: str) -> dict[str, Any] | None:
        """Inspect a remote image.  Returns parsed JSON or None."""

    @abstractmethod
    def copy(self, src: str, dest: str) -> None:
        """Registry-to-registry copy via skopeo."""


def for_url(url: str, token: str | None = None) -> RegistryBase:
    """Return the appropriate registry backend for *url*.

    Parameters
    ----------
    url:
        Registry URL or prefix (e.g. ``ghcr.io/daemonless``).
    token:
        Optional authentication token.
    """
    if "ghcr.io" in url:
        from dbuild.registry.ghcr import GHCR
        return GHCR(url, token)
    if "docker.io" in url or "registry-1.docker.io" in url:
        from dbuild.registry.dockerhub import DockerHub
        return DockerHub(url, token)
    from dbuild.registry.generic import GenericRegistry
    return GenericRegistry(url, token)
