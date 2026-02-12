"""Local (no-CI) backend.

Used as the fallback when dbuild is running outside any CI system,
e.g. during local development on a FreeBSD workstation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from dbuild.ci import CIBase


class LocalCI(CIBase):
    """Fallback CI backend for local builds."""

    @staticmethod
    def detect() -> bool:
        # LocalCI is the fallback; it always "matches".
        return True

    def get_token(self) -> str | None:
        """Read GITHUB_TOKEN from the environment (if set)."""
        return os.environ.get("GITHUB_TOKEN")

    def get_actor(self) -> str | None:
        """Try to determine a username for registry login.

        Checks GITHUB_ACTOR, then falls back to the OS username.
        """
        actor = os.environ.get("GITHUB_ACTOR")
        if actor:
            return actor
        try:
            result = subprocess.run(
                ["whoami"], capture_output=True, text=True, check=False
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except FileNotFoundError:
            pass
        return None

    def is_pr(self) -> bool:
        return False

    def output_matrix(self, matrix: list[dict[str, Any]]) -> None:
        """Print matrix as JSON to stdout."""
        json.dump({"include": matrix}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        sys.stdout.flush()

    def set_output(self, key: str, value: str) -> None:
        """Print the output to stdout (no CI system to receive it)."""
        sys.stdout.write(f"{key}={value}\n")
        sys.stdout.flush()

    def get_commit_message(self) -> str:
        """Return the latest commit message from the local git repo."""
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%B"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except FileNotFoundError:
            pass
        return ""

    def event_metadata(self) -> dict[str, Any]:
        """Gather what we can from the local git repo."""
        meta: dict[str, Any] = {}
        for key, cmd in [
            ("sha", ["git", "rev-parse", "HEAD"]),
            ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
            ("repo", ["git", "remote", "get-url", "origin"]),
        ]:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, check=False
                )
                if result.returncode == 0:
                    meta[key] = result.stdout.strip()
            except FileNotFoundError:
                pass
        return meta
