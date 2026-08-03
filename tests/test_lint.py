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


class TestVersionStripSed(unittest.TestCase):
    """Lint errors when a Containerfile version line seds away the _N
    port revision (causes permanent false 'outdated image' alerts)."""

    def _repo_with_containerfile(self, root: Path, text: str, name="Containerfile") -> Path:
        repo = _make_repo(root, "cit:\n  port: 8080\n")
        (repo / name).write_text(text)
        return repo

    def _version_errors(self, text: str, name="Containerfile") -> list[str]:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_containerfile(Path(d), text, name)
            errors, _ = lint_repo(repo)
        return [e for e in errors if "port revision" in e]

    def test_revision_strip_errors(self):
        errs = self._version_errors(
            "FROM scratch\n"
            "RUN pkg query '%v' playwright-core | sed 's/_[0-9]*$//' > /app/version\n"
        )
        self.assertTrue(errs, "Expected version-strip error")
        self.assertIn("Containerfile:2", errs[0])

    def test_extended_regex_strip_errors(self):
        errs = self._version_errors(
            "RUN pkg query '%v' foo | sed -E 's/_[0-9]+$//' > /app/version\n"
        )
        self.assertTrue(errs, "Expected version-strip error")

    def test_j2_template_also_checked(self):
        errs = self._version_errors(
            "RUN pkg query '%v' foo | sed 's/_[0-9]*$//' > /app/version\n",
            name="Containerfile.pkg.j2",
        )
        self.assertTrue(errs, "Expected version-strip error in .j2 template")

    def test_bare_pkg_query_ok(self):
        errs = self._version_errors(
            "RUN pkg query '%v' headscale > /app/version\n"
        )
        self.assertEqual(errs, [])

    def test_legit_version_seds_ok(self):
        # Real fleet patterns that must NOT be flagged
        errs = self._version_errors(
            "RUN echo \"${VERSION}\" | sed -n 's/^v\\(.*\\)$/\\1/p' > /app/version\n"
            "RUN pkg info authelia | sed -n 's/.*Version.*: *//p' > /app/version\n"
            "RUN plex --version | tr -d 'v' > /app/version\n"
            "RUN pkg info gohugo | sed -n 's/.*Version.*: *//p' | tr ',' '_' > /app/version\n"
        )
        self.assertEqual(errs, [])

    def test_strip_sed_elsewhere_ok(self):
        # Config-rewriting seds not touching /app/version are fine
        errs = self._version_errors(
            "RUN sed -i '' 's/_[0-9]*$//' /app/config.ini\n"
            "RUN echo 1.2.3 > /app/version\n"
        )
        self.assertEqual(errs, [])

    def test_commented_line_ok(self):
        errs = self._version_errors(
            "# RUN pkg query '%v' foo | sed 's/_[0-9]*$//' > /app/version\n"
        )
        self.assertEqual(errs, [])


class TestStaleBaseline(unittest.TestCase):
    """Lint warns when a baseline-<tag>.png has no matching build variant."""

    def _repo_with_baselines(self, root: Path, config_yaml: str, filenames: list[str],
                              in_subdir: bool = False) -> Path:
        repo = _make_repo(root, config_yaml)
        target = repo / ".daemonless"
        if in_subdir:
            target = target / "baselines"
            target.mkdir()
        for name in filenames:
            (target / name).write_bytes(b"")
        return repo

    def test_stale_baseline_warns(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_baselines(
                Path(d),
                "build:\n  variants:\n    - tag: 15\n",
                ["baseline-14-pkg-latest.png"],
            )
            _, warnings = lint_repo(repo)
        self.assertTrue(
            any("baseline-14-pkg-latest.png" in w and "stale" in w for w in warnings),
            f"Expected stale baseline warning, got: {warnings}",
        )

    def test_current_tag_baseline_no_warn(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_baselines(
                Path(d),
                "build:\n  variants:\n    - tag: 15\n",
                ["baseline-15.png"],
            )
            _, warnings = lint_repo(repo)
        baseline_warns = [w for w in warnings if "baseline-15.png" in w]
        self.assertEqual(baseline_warns, [])

    def test_alias_tag_baseline_no_warn(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_baselines(
                Path(d),
                "build:\n  variants:\n    - tag: 15\n      aliases: [\"lts\"]\n",
                ["baseline-lts.png"],
            )
            _, warnings = lint_repo(repo)
        baseline_warns = [w for w in warnings if "baseline-lts.png" in w]
        self.assertEqual(baseline_warns, [])

    def test_baselines_subdir_checked(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo_with_baselines(
                Path(d),
                "build:\n  variants:\n    - tag: 15\n",
                ["baseline-14.png"],
                in_subdir=True,
            )
            _, warnings = lint_repo(repo)
        self.assertTrue(
            any("baselines/baseline-14.png" in w for w in warnings),
            f"Expected stale baseline warning for subdir, got: {warnings}",
        )

    def test_no_config_no_crash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "Containerfile").write_text("FROM scratch\n")
            daemonless = repo / ".daemonless"
            daemonless.mkdir()
            (daemonless / "baseline-anything.png").write_bytes(b"")
            # no config.yaml and no compose.yaml -> lint_repo returns early
            errors, warnings = lint_repo(repo)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
