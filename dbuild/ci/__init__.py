"""CI backend abstraction and auto-detection.

The ``detect()`` function inspects environment variables and returns the
appropriate CI backend instance.  All other code should interact with CI
through the :class:`CIBase` interface -- never import a backend directly.
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from typing import Any

# Pattern: [skip test], [skip push], [skip push:dockerhub], etc.
_SKIP_RE = re.compile(r"\[skip\s+([^\]]+)\]", re.IGNORECASE)


class CIBase(ABC):
    """Abstract base class for CI backends."""

    @staticmethod
    @abstractmethod
    def detect() -> bool:
        """Return True if running inside this CI environment."""

    @abstractmethod
    def get_token(self) -> str | None:
        """Return auth token for registry operations."""

    @abstractmethod
    def get_actor(self) -> str | None:
        """Return username/actor for registry login."""

    @abstractmethod
    def is_pr(self) -> bool:
        """Return True if this is a pull-request / merge-request build."""

    @abstractmethod
    def output_matrix(self, matrix: list[dict[str, Any]]) -> None:
        """Output a build matrix in the CI system's native format."""

    @abstractmethod
    def set_output(self, key: str, value: str) -> None:
        """Set a CI output variable."""

    @abstractmethod
    def event_metadata(self) -> dict[str, Any]:
        """Return event metadata (commit SHA, branch, repo URL, etc.)."""

    @abstractmethod
    def get_commit_message(self) -> str:
        """Return the commit message that triggered this build."""

    def should_skip(self, step: str) -> bool:
        """Check if *step* should be skipped based on commit message.

        Parses ``[skip <step>]`` directives from the commit message.
        Supports sub-targets: ``[skip push:dockerhub]`` skips only
        Docker Hub pushes, while ``[skip push]`` skips all pushes.

        Examples::

            [skip test]           → should_skip("test") == True
            [skip push]           → should_skip("push") == True
                                    should_skip("push:dockerhub") == True
            [skip push:dockerhub] → should_skip("push:dockerhub") == True
                                    should_skip("push") == False
        """
        message = self.get_commit_message()
        if not message:
            return False

        directives: set[str] = set()
        for match in _SKIP_RE.finditer(message):
            directives.add(match.group(1).strip().lower())

        # Exact match
        if step.lower() in directives:
            return True

        # Parent match: [skip push] also skips push:dockerhub
        if ":" in step:
            parent = step.split(":")[0]
            if parent.lower() in directives:
                return True

        return False


def detect() -> CIBase:
    """Auto-detect the current CI environment and return a backend instance.

    Checks environment variables in order of specificity.  Falls back to
    :class:`~dbuild.ci.local.LocalCI` when no CI system is detected.
    """
    if os.environ.get("GITHUB_ACTIONS"):
        from dbuild.ci.github import GitHubCI
        return GitHubCI()

    if os.environ.get("CI_PIPELINE_ID"):
        from dbuild.ci.woodpecker import WoodpeckerCI
        return WoodpeckerCI()

    if os.environ.get("GITLAB_CI"):
        from dbuild.ci.gitlab import GitLabCI
        return GitLabCI()

    from dbuild.ci.local import LocalCI
    return LocalCI()
