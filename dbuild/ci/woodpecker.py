"""Woodpecker CI backend.

Reads configuration from the CI_* environment variables that Woodpecker
injects into pipeline steps.  Woodpecker does not have a native matrix
output mechanism, so ``output_matrix`` prints JSON to stdout for
consumption by downstream steps or external tooling.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from dbuild import log
from dbuild.ci import CIBase


class WoodpeckerCI(CIBase):
    """CI backend for Woodpecker CI."""

    @staticmethod
    def detect() -> bool:
        """Return True when running inside a Woodpecker CI pipeline."""
        return os.environ.get("CI_PIPELINE_ID") is not None

    def get_token(self) -> str | None:
        """Return the GITHUB_TOKEN from the environment.

        Woodpecker passes secrets as plain environment variables, so the
        token is typically injected via the ``secrets:`` stanza in
        ``.woodpecker.yaml``.
        """
        return os.environ.get("GITHUB_TOKEN")

    def get_actor(self) -> str | None:
        """Return the actor for registry login.

        Checks GITHUB_ACTOR first (if the secret is forwarded), then
        falls back to the Woodpecker commit author.
        """
        actor = os.environ.get("GITHUB_ACTOR")
        if actor:
            return actor
        # Woodpecker provides CI_COMMIT_AUTHOR as a fallback.
        author = os.environ.get("CI_COMMIT_AUTHOR")
        if author:
            return author
        return None

    def is_pr(self) -> bool:
        """Return True if this pipeline was triggered by a pull request."""
        return os.environ.get("CI_PIPELINE_EVENT") == "pull_request"

    def output_matrix(self, matrix: list[dict[str, Any]]) -> None:
        """Print the build matrix as JSON to stdout.

        Woodpecker does not have a native matrix output mechanism like
        GitHub Actions' ``$GITHUB_OUTPUT``.  The JSON is printed to
        stdout for consumption by wrapper scripts or external tooling.
        """
        json.dump({"include": matrix}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        sys.stdout.flush()
        log.info(f"printed matrix with {len(matrix)} entries to stdout")

    def set_output(self, key: str, value: str) -> None:
        """Print a key=value pair to stdout.

        Woodpecker does not have a structured output mechanism, so this
        is best-effort for logging and downstream script consumption.
        """
        sys.stdout.write(f"{key}={value}\n")
        sys.stdout.flush()

    def get_commit_message(self) -> str:
        """Return the commit message from Woodpecker's CI_COMMIT_MESSAGE."""
        return os.environ.get("CI_COMMIT_MESSAGE", "")

    def event_metadata(self) -> dict[str, Any]:
        """Return metadata about the current pipeline event.

        Keys returned (when the corresponding env vars are set):
        - ``sha``: The commit SHA.
        - ``branch``: The branch being built.
        - ``repo``: The repository identifier (e.g. ``daemonless/radarr``).
        - ``run_url``: URL to the Woodpecker pipeline run.
        - ``event``: The pipeline event type (push, pull_request, etc.).
        """
        meta: dict[str, Any] = {}

        sha = os.environ.get("CI_COMMIT_SHA")
        if sha:
            meta["sha"] = sha

        branch = os.environ.get("CI_COMMIT_BRANCH")
        if branch:
            meta["branch"] = branch

        repo = os.environ.get("CI_REPO")
        if repo:
            meta["repo"] = repo

        pipeline_url = os.environ.get("CI_PIPELINE_URL")
        if pipeline_url:
            meta["run_url"] = pipeline_url

        event = os.environ.get("CI_PIPELINE_EVENT")
        if event:
            meta["event"] = event

        return meta
