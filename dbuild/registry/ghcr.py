"""GitHub Container Registry (ghcr.io) backend.

Inherits from :class:`~dbuild.registry.generic.GenericRegistry` and adds
ghcr.io-specific login behavior.
"""

from __future__ import annotations

from dbuild.registry.generic import GenericRegistry


class GHCR(GenericRegistry):
    """Backend for GitHub Container Registry (ghcr.io).

    Inherits login/push/inspect from GenericRegistry which routes
    through podman.py for proper privilege escalation.
    """
