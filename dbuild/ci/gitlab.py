"""GitLab CI backend (stub).

Provides the basic scaffolding for GitLab CI integration.  Detection
and token retrieval are implemented; other methods raise
``NotImplementedError`` with guidance on what needs to be added.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from dbuild import log
from dbuild.ci import CIBase


class GitLabCI(CIBase):
    """CI backend for GitLab CI/CD."""

    @staticmethod
    def detect() -> bool:
        """Return True when running inside a GitLab CI pipeline."""
        return os.environ.get("GITLAB_CI") == "true"

    def get_token(self) -> str | None:
        """Return the CI_JOB_TOKEN provided by GitLab.

        The job token has limited permissions.  For pushing to an
        external registry like GHCR, a custom CI/CD variable
        (e.g. ``GITHUB_TOKEN``) should be configured instead.
        """
        # Prefer an explicit GITHUB_TOKEN if the user has configured one
        # for pushing to GHCR.
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            return token
        return os.environ.get("CI_JOB_TOKEN")

    def get_actor(self) -> str | None:
        """Return the actor for registry login.

        Uses GITHUB_ACTOR if set (for GHCR pushes), otherwise falls
        back to the GitLab user that triggered the pipeline.
        """
        actor = os.environ.get("GITHUB_ACTOR")
        if actor:
            return actor
        # GITLAB_USER_LOGIN is the username of the person who triggered
        # the pipeline (available since GitLab 10.0).
        user = os.environ.get("GITLAB_USER_LOGIN")
        if user:
            return user
        return None

    def is_pr(self) -> bool:
        """Return True if this pipeline is for a merge request."""
        return os.environ.get("CI_MERGE_REQUEST_ID") is not None

    def output_matrix(self, matrix: list[dict[str, Any]]) -> None:
        """Output the build matrix.

        GitLab supports dynamic child pipelines via artifacts, but this
        is not yet implemented.  For now, the matrix is printed as JSON
        to stdout (same as the local backend).

        To fully implement GitLab matrix support, generate a child
        pipeline YAML file and save it as an artifact.  See:
        https://docs.gitlab.com/ee/ci/parent_child_pipelines.html
        """
        log.warn(
            "GitLab CI matrix output is not fully implemented; "
            "printing JSON to stdout"
        )
        json.dump({"include": matrix}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        sys.stdout.flush()

    def set_output(self, key: str, value: str) -> None:
        """Set a CI output variable.

        GitLab uses dotenv artifacts for passing variables between jobs.
        This stub writes the key=value to stdout.  A full implementation
        should write to a dotenv file specified by the job configuration.

        See: https://docs.gitlab.com/ee/ci/variables/#pass-an-environment-variable-to-another-job
        """
        log.warn(
            "GitLab CI set_output is not fully implemented; "
            "printing to stdout"
        )
        sys.stdout.write(f"{key}={value}\n")
        sys.stdout.flush()

    def get_commit_message(self) -> str:
        """Return the commit message from GitLab's CI_COMMIT_MESSAGE."""
        return os.environ.get("CI_COMMIT_MESSAGE", "")

    def event_metadata(self) -> dict[str, Any]:
        """Return metadata about the current pipeline event.

        Keys returned (when the corresponding env vars are set):
        - ``sha``: The commit SHA.
        - ``branch``: The branch being built.
        - ``repo``: The project path (e.g. ``daemonless/radarr``).
        - ``run_url``: URL to the pipeline in the GitLab UI.
        - ``event``: Inferred event type (merge_request or push).
        """
        meta: dict[str, Any] = {}

        sha = os.environ.get("CI_COMMIT_SHA")
        if sha:
            meta["sha"] = sha

        branch = os.environ.get("CI_COMMIT_BRANCH")
        if branch:
            meta["branch"] = branch

        project_path = os.environ.get("CI_PROJECT_PATH")
        if project_path:
            meta["repo"] = project_path

        pipeline_url = os.environ.get("CI_PIPELINE_URL")
        if pipeline_url:
            meta["run_url"] = pipeline_url

        # GitLab doesn't have a single "event" variable, so infer it.
        if os.environ.get("CI_MERGE_REQUEST_ID"):
            meta["event"] = "merge_request"
        else:
            meta["event"] = "push"

        return meta
