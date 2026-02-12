"""Version extraction from built container images.

Runs commands inside containers via podman to determine the
application or base-image version.
"""

from __future__ import annotations

from dbuild import log, podman


def extract_app_version(image_ref: str) -> str | None:
    """Extract application version by reading ``/app/version`` inside the image."""
    try:
        version = podman.run_in(image_ref, ["cat", "/app/version"])
        return version if version else None
    except podman.PodmanError:
        log.warn(f"Could not read /app/version from {image_ref}")
        return None


def extract_base_version(image_ref: str) -> str | None:
    """Extract FreeBSD base version by running ``freebsd-version`` inside the image."""
    try:
        version = podman.run_in(image_ref, ["freebsd-version"])
        return version if version else None
    except podman.PodmanError:
        log.warn(f"Could not run freebsd-version in {image_ref}")
        return None


def extract_version(image_ref: str, image_type: str = "app") -> str | None:
    """Extract version from *image_ref* based on *image_type*.

    Parameters
    ----------
    image_ref:
        Full image reference (e.g. ``localhost/radarr:latest``).
    image_type:
        Either ``"app"`` or ``"base"``.
    """
    if image_type == "base":
        return extract_base_version(image_ref)
    return extract_app_version(image_ref)
