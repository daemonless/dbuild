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


class TestReadmeCliDeployment(unittest.TestCase):
    """CLI images should show their run-and-exit Podman usage."""

    def test_cli_image_renders_podman_cli_usage(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".daemonless").mkdir()
            (repo / "Containerfile.j2").write_text(CONTAINERFILE_J2)
            (repo / ".daemonless" / "config.yaml").write_text(
                "x-daemonless:\n"
                "  class: cli\n"
                "build:\n"
                "  variants:\n"
                "    - tag: latest\n"
                "      containerfile: Containerfile\n"
            )
            args = argparse.Namespace(community=None)
            with _chdir(repo):
                cfg = dbuild_config.load(repo)
                self.assertEqual(docs.run(cfg, args), 0)

            readme = (repo / "README.md").read_text()
            # A tool that runs and exits keeps the one-liner; nothing else.
            self.assertIn("### Podman CLI", readme)
            self.assertIn("podman run --rm", readme)
            self.assertIn("ghcr.io/daemonless/", readme)
            for gone in ("### Podman Compose", "### Ansible", "### AppJail", "ansible-playbook"):
                self.assertNotIn(gone, readme)

    def test_service_image_gets_one_way_per_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "Containerfile.j2").write_text(CONTAINERFILE_J2)
            (repo / "compose.yaml").write_text(COMPOSE_YAML.format(name="svc").replace("  docs: manual\n", ""))
            with _chdir(repo):
                cfg = dbuild_config.load(repo)
                self.assertEqual(docs.run(cfg, argparse.Namespace(community=None)), 0)
            readme = (repo / "README.md").read_text()
            self.assertIn("### Podman Compose", readme)
            self.assertIn("podman-compose up -d", readme)
            for gone in ("### Podman CLI", "### Ansible", "podman run --rm", "ansible-playbook", "### AppJail\n"):
                self.assertNotIn(gone, readme)


class TestReadmeWithSidecar(unittest.TestCase):
    """An app shipped with its database is documented as its real compose.

    The one-service snippet dropped the sidecar (librenms' MariaDB,
    paperless-ngx's redis) and listed .env-only variables as the app's
    environment; the one-container recipes started the app without it.
    """

    COMPOSE = (
        "name: app\n"
        "x-daemonless:\n"
        '  title: "App"\n'
        '  description: "An app and its database."\n'
        '  category: "Monitoring"\n'
        "  docs:\n"
        "    env:\n"
        '      CONFIG_LOCATION: "Host folder for app data"\n'
        "services:\n"
        "  app:\n"
        "    image: ghcr.io/daemonless/app:latest\n"
        "    environment:\n"
        "      # found over localhost on host networking\n"
        "      - DB_HOST=127.0.0.1\n"
        "      - DB_PASSWORD=${DB_PASSWORD}\n"
        "    volumes:\n"
        "      - ${CONFIG_LOCATION}:/config\n"
        "  app-mariadb:\n"
        "    image: ghcr.io/daemonless/mariadb:latest\n"
        "    environment:\n"
        "      - MYSQL_PASSWORD=${DB_PASSWORD}\n"
    )

    def test_real_compose_and_env_shown(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "compose.yaml").write_text(self.COMPOSE)
            (repo / "example.env").write_text("CONFIG_LOCATION=/containers/app\nDB_PASSWORD=\n")
            (repo / "Containerfile.j2").write_text(CONTAINERFILE_J2)
            with _chdir(repo):
                cfg = dbuild_config.load(repo)
                self.assertEqual(docs.run(cfg, argparse.Namespace(community=None)), 0)
            readme = (repo / "README.md").read_text()
            compose = readme.split("### Podman Compose", 1)[1]
            self.assertIn("app-mariadb:", compose)  # the sidecar is there
            self.assertIn("# found over localhost on host networking", compose)  # comments kept
            self.assertIn("DB_PASSWORD=\n", compose)  # example.env as the .env
            self.assertNotIn("x-daemonless", compose)
            self.assertNotIn("CONFIG_LOCATION=  #", compose)  # no .env-only var as app env
            for section in ("### Podman CLI", "### Bastille", "### Ansible", "### AppJail Director"):
                self.assertNotIn(section, readme)

    def test_single_service_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _make_repo(Path(d))
            self.assertEqual(dbuild_config.load(repo).compose_text, "")


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


class TestNormalizeBaseVersion(unittest.TestCase):
    """_normalize_base_version turns a BASE_VERSION arg (possibly a pkg tag)
    into the FreeBSD release shown as '**Base:** FreeBSD <x>'."""

    def test_strips_pkg_and_latest_suffixes(self):
        cases = {
            "15-pkg": "15",
            "15.1-latest": "15.1",
            "15.1-pkg-latest": "15.1",
            "15.1": "15.1",  # plain release unchanged
            '"15.1"': "15.1",  # quoted arg
        }
        for raw, expected in cases.items():
            self.assertEqual(docs._normalize_base_version(raw), expected, raw)

    def test_bare_latest_and_empty_fall_back(self):
        self.assertEqual(docs._normalize_base_version("latest"), "15.1")
        self.assertEqual(docs._normalize_base_version(""), "15.1")
        self.assertEqual(docs._normalize_base_version("  "), "15.1")


if __name__ == "__main__":
    unittest.main()


class TestSitePlaceholders:
    def test_repo_compose_and_env_get_site_placeholders(self):
        from dbuild.docs import _site_placeholders
        compose = "      - PUID=1000\n      - PGID=1000\n    volumes:\n      - /containers/app/config:/config\n"
        assert _site_placeholders(compose) == (
            "      - PUID=@PUID@\n      - PGID=@PGID@\n    volumes:\n"
            "      - @CONTAINER_CONFIG_ROOT@/app/config:/config\n")
        env = "CONFIG_LOCATION=/containers/app\n# TZ=UTC\n"
        assert _site_placeholders(env, env_file=True) == (
            "CONFIG_LOCATION=@CONTAINER_CONFIG_ROOT@/app\nTZ=@TZ@\n")
        assert _site_placeholders("") == ""


# ── Stack choices ──────────────────────────────────────────────────────

CHOICES_COMPOSE = """\
name: todo
x-daemonless:
  title: "Todo"
  description: "A to-do app."
  category: "Utilities"
  icon: ":test:"
  upstream_url: "https://example.com"
  user: "bsd"
  choices:
    database:
      kind: database
      offers: [sqlite, postgres, mariadb, external]
      default: sqlite
      env:
        type: TODO_DB_TYPE
        host: TODO_DB_HOST
        user: TODO_DB_USER
        password: TODO_DB_PASSWORD
        name: TODO_DB_NAME
      types: { mariadb: mysql }
    search:
      label: Search
      default: "off"
      options:
        "off": { label: Off }
        "on":  { label: On, profile: search, env: { TODO_SEARCH: "true" } }
services:
  todo:
    image: ghcr.io/daemonless/todo:latest
    environment:
      - PUID=1000
      - TODO_DB_TYPE=${TODO_DB_TYPE:-sqlite}
      - TODO_DB_HOST=${TODO_DB_HOST:-}
      - TODO_DB_USER=${TODO_DB_USER:-}
      - TODO_DB_PASSWORD=${TODO_DB_PASSWORD:-}
      - TODO_DB_NAME=${TODO_DB_NAME:-}
      - TODO_SEARCH=${TODO_SEARCH:-false}
    ports:
      - "3456:3456"
  meili:
    # search, only when asked for
    image: ghcr.io/daemonless/meilisearch:latest
    profiles: [search]
"""

CHOICES_ENV = "TZ=UTC\nTODO_DB_TYPE=sqlite\n"


class TestStackChoices(unittest.TestCase):
    def _render(self, tmp: Path):
        (tmp / "compose.yaml").write_text(CHOICES_COMPOSE)
        (tmp / "example.env").write_text(CHOICES_ENV)
        (tmp / "Containerfile.j2").write_text(CONTAINERFILE_J2)
        with _chdir(tmp):
            cfg = dbuild_config.load(tmp)
            outputs, errors = docs.render_generated(cfg, argparse.Namespace(community=None), tmp)
        return outputs, errors

    def test_database_services_come_from_dbuild(self):
        with tempfile.TemporaryDirectory() as d:
            outputs, errors = self._render(Path(d))
        self.assertEqual(errors, [])
        pg = outputs["compose.database-postgres.yaml"]
        self.assertIn("  postgres:\n    image: ghcr.io/daemonless/postgres:17", pg)
        self.assertIn("POSTGRES_PASSWORD=${TODO_DB_PASSWORD}", pg, "the supplied service reads the app's own variables")
        self.assertIn('"${DATABASE_LOCATION}:/var/lib/postgresql/data"', pg)
        self.assertIn("  todo:\n    depends_on: [postgres]", pg, "the app waits for the database")
        self.assertNotIn("meili", pg, "another choice's profiled service stays out")
        self.assertNotIn("profiles", pg)
        mdb = outputs["compose.database-mariadb.yaml"]
        self.assertIn("MYSQL_ROOT_PASSWORD=${TODO_DB_PASSWORD}", mdb)
        self.assertNotIn("compose.database-sqlite.yaml", outputs, "the default is the compose as it stands")
        ext = outputs["compose.database-external.yaml"]
        self.assertNotIn("postgres:", ext.split("services:")[1])

    def test_env_applied_in_the_apps_words(self):
        with tempfile.TemporaryDirectory() as d:
            outputs, _ = self._render(Path(d))
        pg = outputs["example.database-postgres.env"]
        self.assertIn("TODO_DB_TYPE=postgres", pg)
        self.assertNotIn("TODO_DB_TYPE=sqlite", pg, "a key already there is replaced, not doubled")
        self.assertIn("TODO_DB_HOST=postgres", pg)
        self.assertIn("TODO_DB_PASSWORD=  # set one", pg)
        self.assertIn("DATABASE_LOCATION=/containers/todo/postgres", pg)
        self.assertIn("TODO_DB_TYPE=mysql", outputs["example.database-mariadb.env"], "types: maps the engine to the app's word")
        ext = outputs["example.database-external.env"]
        self.assertIn("TODO_DB_TYPE=  # Kind: postgres | mysql", ext)
        self.assertIn("TODO_DB_PASSWORD=  # Password", ext)

    def test_you_get_names_what_runs_and_where_data_lands(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            (t / "compose.yaml").write_text(CHOICES_COMPOSE)
            (t / "example.env").write_text(CHOICES_ENV)
            (t / "Containerfile.j2").write_text(CONTAINERFILE_J2)
            with _chdir(t):
                ctx = docs._enrich_metadata(dbuild_config.load(t), None)
        db = next(c for c in ctx["choices"] if c["id"] == "database")
        pg = next(o for o in db["options"] if o["id"] == "postgres")
        self.assertEqual([(p["name"], p["kind"]) for p in pg["parts"]], [("todo", "app"), ("postgres", "db")])
        self.assertEqual(pg["parts"][1]["image"], "postgres:17")
        self.assertTrue(pg["jails"][1]["name"].endswith("_postgres"), pg["jails"])  # <image>_postgres
        self.assertIn("@CONTAINER_CONFIG_ROOT@/todo/postgres", pg["folders"])
        ext = next(o for o in db["options"] if o["id"] == "external")
        self.assertEqual(ext["parts"][-1]["kind"], "none")

    def test_a_part_choice_switches_a_profile(self):
        with tempfile.TemporaryDirectory() as d:
            outputs, _ = self._render(Path(d))
        on = outputs["compose.search-on.yaml"]
        self.assertIn("  meili:", on)
        self.assertIn("# search, only when asked for", on, "comments survive flattening")
        self.assertNotIn("profiles", on)
        self.assertIn("TODO_SEARCH=true", outputs["example.search-on.env"])

    def test_readme_has_a_section_per_option_default_first(self):
        with tempfile.TemporaryDirectory() as d:
            outputs, _ = self._render(Path(d))
        readme = outputs["README.md"]
        i_sql, i_pg, i_ext = (readme.index(h) for h in ("#### SQLite (default)", "#### PostgreSQL", "#### Your own"))
        self.assertTrue(i_sql < i_pg < i_ext)
        self.assertIn("fill in Kind, Host, User, Password, Database", readme)

    def test_lint_catches_the_wiring_mistakes(self):
        import yaml as _yaml

        from dbuild import choices as choices_mod
        data = _yaml.safe_load(CHOICES_COMPOSE)
        ch = data["x-daemonless"]["choices"]
        ch["database"]["default"] = "postgres"
        ch["database"]["offers"].append("oracle")
        ch["database"]["env"]["port"] = "TODO_DB_PORT"      # not in the app's environment
        ch["search"]["options"]["on"]["profile"] = "nothing"
        errs = choices_mod.validate(choices_mod.parse(data["x-daemonless"], data), data)
        self.assertTrue(any("switches a service on" in e for e in errs), errs)
        self.assertTrue(any("offers 'oracle'" in e for e in errs), errs)
        self.assertTrue(any("TODO_DB_PORT is not in todo's environment" in e for e in errs), errs)
        self.assertTrue(any("no service carries profiles: [nothing]" in e for e in errs), errs)

    def test_director_per_option_from_the_same_engine_table(self):
        with tempfile.TemporaryDirectory() as d:
            # appjail on, so the README carries a Director per option
            (Path(d) / ".daemonless").mkdir()
            compose = CHOICES_COMPOSE.replace('  user: "bsd"\n', '  user: "bsd"\n  appjail: {}\n')
            (Path(d) / "compose.yaml").write_text(compose)
            (Path(d) / "example.env").write_text(CHOICES_ENV)
            (Path(d) / "Containerfile.j2").write_text(CONTAINERFILE_J2)
            with _chdir(Path(d)):
                cfg = dbuild_config.load(Path(d))
                outputs, errors = docs.render_generated(cfg, argparse.Namespace(community=None), Path(d))
        self.assertEqual(errors, [])
        readme = outputs["README.md"]
        aj = readme[readme.index("### AppJail Director"):]
        pg = aj[aj.index("#### PostgreSQL"):aj.index("#### MariaDB")]
        self.assertIn("  todo-postgres:\n    name: todo_postgres\n    priority: 10", pg, "the engine's jail starts first")
        self.assertIn("POSTGRES_PASSWORD: !ENV '${TODO_DB_PASSWORD}'", pg, "reads the app's own variables")
        self.assertIn("TODO_DB_HOST=todo_postgres", pg, "the host is the jail's name")
        self.assertIn("template: !ENV '${PWD}/postgres-template.conf'", pg)
        self.assertIn("sysvshm: new", pg, "the jail template PostgreSQL needs ships with it")
        self.assertIn("device: !ENV '${DATABASE_LOCATION}'", pg)
        self.assertNotIn("- DATABASE_LOCATION: !ENV", pg, "a device variable is not handed to the app's jail")
        sq = aj[aj.index("#### SQLite (default)"):aj.index("#### PostgreSQL")]
        self.assertNotIn("postgres", sq)
