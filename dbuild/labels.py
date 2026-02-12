"""OCI label injection via buildah.

Uses the buildah from / config / commit / rm pattern to apply
labels to an already-built image without changing its layers.
"""

from __future__ import annotations

import datetime
import subprocess

from dbuild import log, podman


def build_labels(
    version: str | None = None,
    variant_tag: str | None = None,
) -> dict[str, str]:
    """Generate standard OCI labels.

    Returns a dict suitable for passing to :func:`apply`.
    """
    labels: dict[str, str] = {
        "org.opencontainers.image.created": datetime.datetime.now(
            tz=datetime.UTC
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Git revision -- best effort, not fatal if git is unavailable.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            labels["org.opencontainers.image.revision"] = result.stdout.strip()
    except FileNotFoundError:
        pass

    if version:
        labels["org.opencontainers.image.version"] = version

    if variant_tag:
        labels["io.daemonless.variant"] = variant_tag

    return labels


def apply(image_ref: str, labels: dict[str, str]) -> None:
    """Apply *labels* to *image_ref* using buildah.

    This creates a temporary working container, sets the labels via
    ``buildah config``, commits back to the same image reference, and
    cleans up the working container.
    """
    if not labels:
        return

    log.info(f"Applying {len(labels)} label(s) to {image_ref}")
    container_id = podman.bah_from(image_ref)
    try:
        podman.bah_config(container_id, labels=labels)
        podman.bah_commit(container_id, image_ref)
    finally:
        podman.bah_rm(container_id)
