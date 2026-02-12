"""GitHub Actions CI backend.

Reads configuration from the GITHUB_* environment variables that GitHub
Actions injects into every workflow run.  Outputs are written to the
``$GITHUB_OUTPUT`` file using the ``key=value`` or multi-line delimiter
protocol.
"""

from __future__ import annotations

import json
import os
from typing import Any

from dbuild import log
from dbuild.ci import CIBase


class GitHubCI(CIBase):
    """CI backend for GitHub Actions."""

    @staticmethod
    def detect() -> bool:
        """Return True when running inside a GitHub Actions workflow."""
        return os.environ.get("GITHUB_ACTIONS") == "true"

    def get_token(self) -> str | None:
        """Return the GITHUB_TOKEN from the environment."""
        return os.environ.get("GITHUB_TOKEN")

    def get_actor(self) -> str | None:
        """Return the GITHUB_ACTOR (the user or app that triggered the workflow)."""
        return os.environ.get("GITHUB_ACTOR")

    def is_pr(self) -> bool:
        """Return True if this workflow was triggered by a pull_request event."""
        return os.environ.get("GITHUB_EVENT_NAME") == "pull_request"

    def output_matrix(self, matrix: list[dict[str, Any]]) -> None:
        """Write the build matrix to ``$GITHUB_OUTPUT``.

        The matrix is written as a JSON object under the ``matrix`` key,
        compatible with ``fromJson()`` in a GitHub Actions workflow::

            matrix={"include": [...]}
        """
        payload = json.dumps({"include": matrix}, separators=(",", ":"))
        self.set_output("matrix", payload)
        log.info(f"wrote matrix with {len(matrix)} entries to GITHUB_OUTPUT")

    def set_output(self, key: str, value: str) -> None:
        """Append a ``key=value`` pair to the ``$GITHUB_OUTPUT`` file.

        For multi-line values, uses the heredoc delimiter protocol::

            key<<EOF
            value
            EOF
        """
        output_file = os.environ.get("GITHUB_OUTPUT")
        if not output_file:
            log.warn(
                "GITHUB_OUTPUT is not set; cannot write output "
                f"(key={key!r})"
            )
            return

        try:
            with open(output_file, "a") as fh:
                if "\n" in value:
                    # Multi-line value: use delimiter protocol
                    fh.write(f"{key}<<DBUILD_EOF\n")
                    fh.write(value)
                    if not value.endswith("\n"):
                        fh.write("\n")
                    fh.write("DBUILD_EOF\n")
                else:
                    fh.write(f"{key}={value}\n")
        except OSError as exc:
            log.error(f"failed to write to GITHUB_OUTPUT ({output_file}): {exc}")

    def get_commit_message(self) -> str:
        """Return the commit message from the environment.

        GitHub Actions does not directly expose the commit message as an
        env var.  We check ``DBUILD_COMMIT_MESSAGE`` (which can be set
        by the workflow) first, then fall back to ``git log``.
        """
        msg = os.environ.get("DBUILD_COMMIT_MESSAGE")
        if msg:
            return msg
        # Fall back to git log (the repo is checked out in the workspace)
        try:
            import subprocess
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
        """Return metadata about the current workflow event.

        Keys returned (when the corresponding env vars are set):
        - ``sha``: The commit SHA that triggered the workflow.
        - ``branch``: The branch or tag ref (short name).
        - ``repo``: The owner/repo string (e.g. ``daemonless/radarr``).
        - ``run_url``: Full URL to this workflow run in the GitHub UI.
        - ``event``: The event name (push, pull_request, etc.).
        """
        meta: dict[str, Any] = {}

        sha = os.environ.get("GITHUB_SHA")
        if sha:
            meta["sha"] = sha

        ref = os.environ.get("GITHUB_REF_NAME")
        if ref:
            meta["branch"] = ref

        repo = os.environ.get("GITHUB_REPOSITORY")
        if repo:
            meta["repo"] = repo

        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        run_id = os.environ.get("GITHUB_RUN_ID")
        if repo and run_id:
            meta["run_url"] = f"{server}/{repo}/actions/runs/{run_id}"

        event = os.environ.get("GITHUB_EVENT_NAME")
        if event:
            meta["event"] = event

        return meta
