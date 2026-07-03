"""Unit tests for dbuild.lint."""

from __future__ import annotations

import unittest
from pathlib import Path

from dbuild.lint import lint_repo


def _make_repo(tmp_path: Path, config_yaml: str) -> Path:
    (tmp_path / "Containerfile").write_text("FROM scratch\n")
    daemonless = tmp_path / ".daemonless"
    daemonless.mkdir()
    (daemonless / "config.yaml").write_text(config_yaml)
    return tmp_path


class TestPkgNameRedundancy(unittest.TestCase):
    """Lint warns when pkg_name and args.PKG_NAME are both set."""

    def test_redundant_same_value_warns(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d), (
                "build:\n"
                "  variants:\n"
                "    - tag: lts\n"
                "      pkg_name: forgejo-lts\n"
                "      args:\n"
                "        PKG_NAME: forgejo-lts\n"
            ))
            _, warnings = lint_repo(repo)
        self.assertTrue(
            any("redundant" in w for w in warnings),
            f"Expected redundancy warning, got: {warnings}",
        )

    def test_conflicting_values_warns(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d), (
                "build:\n"
                "  variants:\n"
                "    - tag: lts\n"
                "      pkg_name: forgejo-lts\n"
                "      args:\n"
                "        PKG_NAME: wrong-package\n"
            ))
            _, warnings = lint_repo(repo)
        self.assertTrue(
            any("differs" in w for w in warnings),
            f"Expected conflict warning, got: {warnings}",
        )

    def test_only_pkg_name_no_warn(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d), (
                "build:\n"
                "  variants:\n"
                "    - tag: lts\n"
                "      pkg_name: forgejo-lts\n"
            ))
            _, warnings = lint_repo(repo)
        pkg_warns = [w for w in warnings if "PKG_NAME" in w or "pkg_name" in w]
        self.assertEqual(pkg_warns, [])

    def test_only_arg_no_warn(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d), (
                "build:\n"
                "  variants:\n"
                "    - tag: latest\n"
                "      args:\n"
                "        PKG_NAME: forgejo\n"
            ))
            _, warnings = lint_repo(repo)
        pkg_warns = [w for w in warnings if "redundant" in w or "differs" in w]
        self.assertEqual(pkg_warns, [])


class TestReadySignal(unittest.TestCase):
    """Lint errors when s6-ready-when is used without a notification-fd."""

    def _repo_with_service(self, root: Path, run_text: str, fd_content=None) -> Path:
        repo = _make_repo(root, "cit:\n  port: 8080\n")
        svc = repo / "root" / "etc" / "services.d" / "myapp"
        svc.mkdir(parents=True)
        (svc / "run").write_text(run_text)
        if fd_content is not None:
            (svc / "notification-fd").write_text(fd_content)
        return repo

    def test_missing_notification_fd_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_service(
                Path(d), "#!/bin/sh\ns6-ready-when 'nc -z 127.0.0.1 8080'\nexec myapp\n"
            )
            errors, _ = lint_repo(repo)
        self.assertTrue(
            any("notification-fd is missing" in e for e in errors),
            f"Expected notification-fd error, got: {errors}",
        )

    def test_correct_notification_fd_ok(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_service(
                Path(d), "#!/bin/sh\ns6-ready-when 'nc -z 127.0.0.1 8080'\nexec myapp\n",
                fd_content="3\n",
            )
            errors, _ = lint_repo(repo)
        ready_errors = [e for e in errors if "notification-fd" in e]
        self.assertEqual(ready_errors, [])

    def test_wrong_content_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_service(
                Path(d), "#!/bin/sh\ns6-ready-when 'true'\nexec myapp\n",
                fd_content="1\n",
            )
            errors, _ = lint_repo(repo)
        self.assertTrue(
            any("must contain '3'" in e for e in errors),
            f"Expected wrong-content error, got: {errors}",
        )

    def test_no_ready_when_no_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_service(Path(d), "#!/bin/sh\nexec myapp\n")
            errors, _ = lint_repo(repo)
        ready_errors = [e for e in errors if "notification-fd" in e]
        self.assertEqual(ready_errors, [])

    def test_commented_ready_when_no_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_service(
                Path(d), "#!/bin/sh\n# s6-ready-when 'true'\nexec myapp\n"
            )
            errors, _ = lint_repo(repo)
        ready_errors = [e for e in errors if "notification-fd" in e]
        self.assertEqual(ready_errors, [])


if __name__ == "__main__":
    unittest.main()
