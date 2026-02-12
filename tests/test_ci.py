"""Unit tests for dbuild.ci and CI backends."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from dbuild.ci import _SKIP_RE, CIBase, detect

# ── should_skip tests (on a concrete subclass) ──────────────────────


class _StubCI(CIBase):
    """Minimal concrete CI for testing should_skip()."""

    def __init__(self, message: str = "") -> None:
        self._message = message

    @staticmethod
    def detect() -> bool:
        return True

    def get_token(self):
        return None

    def get_actor(self):
        return None

    def is_pr(self):
        return False

    def output_matrix(self, matrix):
        pass

    def set_output(self, key, value):
        pass

    def event_metadata(self):
        return {}

    def get_commit_message(self):
        return self._message


class TestShouldSkip(unittest.TestCase):
    """Tests for CIBase.should_skip() parsing."""

    def test_no_skip(self):
        ci = _StubCI("normal commit message")
        self.assertFalse(ci.should_skip("test"))
        self.assertFalse(ci.should_skip("push"))

    def test_exact_match(self):
        ci = _StubCI("fix: something [skip test]")
        self.assertTrue(ci.should_skip("test"))
        self.assertFalse(ci.should_skip("push"))

    def test_parent_match(self):
        ci = _StubCI("[skip push]")
        self.assertTrue(ci.should_skip("push"))
        self.assertTrue(ci.should_skip("push:dockerhub"))
        self.assertTrue(ci.should_skip("push:ghcr"))

    def test_subtarget_does_not_skip_parent(self):
        ci = _StubCI("[skip push:dockerhub]")
        self.assertTrue(ci.should_skip("push:dockerhub"))
        self.assertFalse(ci.should_skip("push"))
        self.assertFalse(ci.should_skip("push:ghcr"))

    def test_case_insensitive(self):
        ci = _StubCI("[Skip TEST]")
        self.assertTrue(ci.should_skip("test"))
        self.assertTrue(ci.should_skip("TEST"))

    def test_multiple_skips(self):
        ci = _StubCI("[skip test] [skip push]")
        self.assertTrue(ci.should_skip("test"))
        self.assertTrue(ci.should_skip("push"))

    def test_empty_message(self):
        ci = _StubCI("")
        self.assertFalse(ci.should_skip("test"))

    def test_skip_sbom(self):
        ci = _StubCI("[skip sbom]")
        self.assertTrue(ci.should_skip("sbom"))
        self.assertFalse(ci.should_skip("test"))


class TestSkipRegex(unittest.TestCase):
    """Tests for the _SKIP_RE pattern."""

    def test_simple(self):
        m = _SKIP_RE.search("[skip test]")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "test")

    def test_with_colon(self):
        m = _SKIP_RE.search("[skip push:dockerhub]")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "push:dockerhub")

    def test_extra_spaces(self):
        m = _SKIP_RE.search("[skip  test ]")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "test")


# ── detect() factory ────────────────────────────────────────────────


class TestDetect(unittest.TestCase):
    """Tests for ci.detect() factory."""

    @patch.dict("os.environ", {}, clear=True)
    def test_local_fallback(self):
        from dbuild.ci.local import LocalCI
        ci = detect()
        self.assertIsInstance(ci, LocalCI)

    @patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}, clear=True)
    def test_github(self):
        from dbuild.ci.github import GitHubCI
        ci = detect()
        self.assertIsInstance(ci, GitHubCI)

    @patch.dict("os.environ", {"CI_PIPELINE_ID": "123"}, clear=True)
    def test_woodpecker(self):
        from dbuild.ci.woodpecker import WoodpeckerCI
        ci = detect()
        self.assertIsInstance(ci, WoodpeckerCI)

    @patch.dict("os.environ", {"GITLAB_CI": "true"}, clear=True)
    def test_gitlab(self):
        from dbuild.ci.gitlab import GitLabCI
        ci = detect()
        self.assertIsInstance(ci, GitLabCI)


# ── Per-backend tests ────────────────────────────────────────────────


class TestGitHubCI(unittest.TestCase):
    """Tests for GitHubCI backend."""

    @patch.dict("os.environ", {"GITHUB_ACTIONS": "true"})
    def test_detect(self):
        from dbuild.ci.github import GitHubCI
        self.assertTrue(GitHubCI.detect())

    @patch.dict("os.environ", {}, clear=True)
    def test_detect_false(self):
        from dbuild.ci.github import GitHubCI
        self.assertFalse(GitHubCI.detect())

    @patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_abc123"})
    def test_get_token(self):
        from dbuild.ci.github import GitHubCI
        ci = GitHubCI()
        self.assertEqual(ci.get_token(), "ghp_abc123")

    @patch.dict("os.environ", {"GITHUB_EVENT_NAME": "pull_request"})
    def test_is_pr(self):
        from dbuild.ci.github import GitHubCI
        ci = GitHubCI()
        self.assertTrue(ci.is_pr())

    @patch.dict("os.environ", {"GITHUB_EVENT_NAME": "push"})
    def test_is_not_pr(self):
        from dbuild.ci.github import GitHubCI
        ci = GitHubCI()
        self.assertFalse(ci.is_pr())

    @patch.dict("os.environ", {"DBUILD_COMMIT_MESSAGE": "test msg [skip push]"})
    def test_get_commit_message(self):
        from dbuild.ci.github import GitHubCI
        ci = GitHubCI()
        self.assertEqual(ci.get_commit_message(), "test msg [skip push]")

    def test_set_output(self):
        import os
        import tempfile

        from dbuild.ci.github import GitHubCI
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            output_file = f.name
        try:
            with patch.dict("os.environ", {"GITHUB_OUTPUT": output_file}):
                ci = GitHubCI()
                ci.set_output("mykey", "myvalue")
            with open(output_file) as f:
                content = f.read()
            self.assertIn("mykey=myvalue", content)
        finally:
            os.unlink(output_file)

    def test_set_output_multiline(self):
        import os
        import tempfile

        from dbuild.ci.github import GitHubCI
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            output_file = f.name
        try:
            with patch.dict("os.environ", {"GITHUB_OUTPUT": output_file}):
                ci = GitHubCI()
                ci.set_output("mykey", "line1\nline2")
            with open(output_file) as f:
                content = f.read()
            self.assertIn("mykey<<DBUILD_EOF", content)
            self.assertIn("line1\nline2", content)
        finally:
            os.unlink(output_file)


class TestWoodpeckerCI(unittest.TestCase):
    """Tests for WoodpeckerCI backend."""

    @patch.dict("os.environ", {"CI_PIPELINE_ID": "42"})
    def test_detect(self):
        from dbuild.ci.woodpecker import WoodpeckerCI
        self.assertTrue(WoodpeckerCI.detect())

    @patch.dict("os.environ", {}, clear=True)
    def test_detect_false(self):
        from dbuild.ci.woodpecker import WoodpeckerCI
        self.assertFalse(WoodpeckerCI.detect())

    @patch.dict("os.environ", {"CI_COMMIT_MESSAGE": "fix: stuff [skip test]"})
    def test_get_commit_message(self):
        from dbuild.ci.woodpecker import WoodpeckerCI
        ci = WoodpeckerCI()
        self.assertEqual(ci.get_commit_message(), "fix: stuff [skip test]")

    @patch.dict("os.environ", {"CI_PIPELINE_EVENT": "pull_request"})
    def test_is_pr(self):
        from dbuild.ci.woodpecker import WoodpeckerCI
        ci = WoodpeckerCI()
        self.assertTrue(ci.is_pr())

    @patch.dict("os.environ", {"CI_PIPELINE_EVENT": "push"})
    def test_is_not_pr(self):
        from dbuild.ci.woodpecker import WoodpeckerCI
        ci = WoodpeckerCI()
        self.assertFalse(ci.is_pr())


class TestGitLabCI(unittest.TestCase):
    """Tests for GitLabCI backend."""

    @patch.dict("os.environ", {"GITLAB_CI": "true"})
    def test_detect(self):
        from dbuild.ci.gitlab import GitLabCI
        self.assertTrue(GitLabCI.detect())

    @patch.dict("os.environ", {}, clear=True)
    def test_detect_false(self):
        from dbuild.ci.gitlab import GitLabCI
        self.assertFalse(GitLabCI.detect())

    @patch.dict("os.environ", {"CI_COMMIT_MESSAGE": "update [skip sbom]"})
    def test_get_commit_message(self):
        from dbuild.ci.gitlab import GitLabCI
        ci = GitLabCI()
        self.assertEqual(ci.get_commit_message(), "update [skip sbom]")

    @patch.dict("os.environ", {"CI_MERGE_REQUEST_ID": "99"})
    def test_is_pr(self):
        from dbuild.ci.gitlab import GitLabCI
        ci = GitLabCI()
        self.assertTrue(ci.is_pr())

    @patch.dict("os.environ", {}, clear=True)
    def test_is_not_pr(self):
        from dbuild.ci.gitlab import GitLabCI
        ci = GitLabCI()
        self.assertFalse(ci.is_pr())


class TestLocalCI(unittest.TestCase):
    """Tests for LocalCI backend."""

    def test_detect_always_true(self):
        from dbuild.ci.local import LocalCI
        self.assertTrue(LocalCI.detect())

    @patch.dict("os.environ", {}, clear=True)
    def test_no_token(self):
        from dbuild.ci.local import LocalCI
        ci = LocalCI()
        self.assertIsNone(ci.get_token())

    @patch.dict("os.environ", {"GITHUB_TOKEN": "tok123"})
    def test_token_from_env(self):
        from dbuild.ci.local import LocalCI
        ci = LocalCI()
        self.assertEqual(ci.get_token(), "tok123")

    def test_is_not_pr(self):
        from dbuild.ci.local import LocalCI
        ci = LocalCI()
        self.assertFalse(ci.is_pr())


if __name__ == "__main__":
    unittest.main()
