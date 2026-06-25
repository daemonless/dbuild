"""Unit tests for dbuild.docs generation and the lint --check-generated drift check.

These exercise the shared render path (docs.render_generated) that both
`dbuild generate` (writes files) and `dbuild lint --check-generated` (compares
files) rely on, plus the registry gating for README drift.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import tempfile
import unittest
from pathlib import Path

from dbuild import config as dbuild_config
from dbuild import docs
from dbuild.docs import check_repo

# A Containerfile template whose output is registry-independent (literal FROM),
# so it can be drift-checked anywhere, including off-saturn with no git remote.
CONTAINERFILE_J2 = (
    "FROM ghcr.io/daemonless/base:${BASE_VERSION}\n"
    'LABEL org.opencontainers.image.title="{{ title }}"\n'
)

COMPOSE_YAML = """\
name: {name}
x-daemonless:
  title: "TestApp"
  description: "A test app."
  category: "Utilities"
  icon: ":test:"
  upstream_url: "https://example.com"
  user: "bsd"
  docs: manual
services:
  {name}:
    image: ghcr.io/daemonless/{name}:latest
    environment:
      - PUID=1000
"""


@contextlib.contextmanager
def _chdir(path: Path):
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def _make_repo(root: Path, name: str = "testapp", *, readme_j2: str | None = None) -> Path:
    """Create a minimal image repo and run generation into it."""
    (root / "compose.yaml").write_text(COMPOSE_YAML.format(name=name))
    (root / "Containerfile.j2").write_text(CONTAINERFILE_J2)
    if readme_j2 is not None:
        (root / "README.j2").write_text(readme_j2)
    args = argparse.Namespace(community=None)
    with _chdir(root):
        cfg = dbuild_config.load(root)
        rc = docs.run(cfg, args)
    assert rc == 0
    return root


class TestCheckGeneratedContainerfile(unittest.TestCase):
    """Containerfile drift is detected regardless of registry resolution."""

    def test_freshly_generated_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            stale, notes = check_repo(repo)
        self.assertEqual(stale, [], f"unexpected stale findings: {stale}")
        self.assertEqual(notes, [])

    def test_edited_containerfile_is_stale(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            cf = repo / "Containerfile"
            cf.write_text(cf.read_text() + "# hand-edited drift\n")
            stale, _ = check_repo(repo)
        self.assertTrue(
            any("Containerfile" in s and "out of date" in s for s in stale),
            f"expected stale Containerfile, got: {stale}",
        )

    def test_missing_containerfile_is_stale(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            (repo / "Containerfile").unlink()
            stale, _ = check_repo(repo)
        self.assertTrue(
            any("Containerfile" in s and "missing" in s for s in stale),
            f"expected missing Containerfile, got: {stale}",
        )

    def test_no_templates_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "compose.yaml").write_text(COMPOSE_YAML.format(name="x"))
            # no Containerfile.j2 and no README.j2
            stale, notes = check_repo(repo)
        self.assertEqual(stale, [])
        self.assertEqual(notes, [])

    def test_cwd_restored_after_check(self):
        before = Path.cwd()
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            check_repo(repo)
        self.assertEqual(Path.cwd(), before)


class TestCheckGeneratedReadmeGating(unittest.TestCase):
    """README embeds the registry, so its check is gated on registry confidence."""

    README_J2 = "# {{ title }}\n\nPull: `{{ registry }}/{{ name }}`\n"

    def _make_readme_repo(self, root: Path) -> Path:
        # docs: manual + a local README.j2 -> README.md is still generated.
        compose = COMPOSE_YAML.format(name="testapp")
        (root / "compose.yaml").write_text(compose)
        (root / "Containerfile.j2").write_text(CONTAINERFILE_J2)
        (root / "README.j2").write_text(self.README_J2)
        args = argparse.Namespace(community=None)
        with _chdir(root):
            cfg = dbuild_config.load(root)
            self.assertEqual(docs.run(cfg, args), 0)
        return root

    def test_readme_skipped_without_registry(self):
        with tempfile.TemporaryDirectory() as d, _no_env("DBUILD_REGISTRY"):
            repo = self._make_readme_repo(Path(d))
            self.assertTrue((repo / "README.md").exists())
            stale, notes = check_repo(repo)
        self.assertEqual(stale, [], f"README should be skipped, not stale: {stale}")
        self.assertTrue(
            any("README" in n and "skipped" in n for n in notes),
            f"expected README skip note, got: {notes}",
        )

    def test_readme_checked_with_registry(self):
        with tempfile.TemporaryDirectory() as d, _set_env("DBUILD_REGISTRY", "ghcr.io/daemonless"):
            repo = self._make_readme_repo(Path(d))
            stale, notes = check_repo(repo)
        self.assertEqual(stale, [], f"freshly generated README should be clean: {stale}")
        self.assertFalse(any("README" in n for n in notes), notes)

    def test_readme_drift_detected_with_registry(self):
        with tempfile.TemporaryDirectory() as d, _set_env("DBUILD_REGISTRY", "ghcr.io/daemonless"):
            repo = self._make_readme_repo(Path(d))
            (repo / "README.md").write_text("stale hand-edited readme\n")
            stale, _ = check_repo(repo)
        self.assertTrue(
            any("README.md" in s and "out of date" in s for s in stale),
            f"expected README drift, got: {stale}",
        )


@contextlib.contextmanager
def _set_env(key: str, value: str):
    prev = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


@contextlib.contextmanager
def _no_env(key: str):
    prev = os.environ.get(key)
    os.environ.pop(key, None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ[key] = prev


if __name__ == "__main__":
    unittest.main()
