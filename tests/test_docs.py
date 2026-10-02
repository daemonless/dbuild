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
        "off": { label: "Off" }
        "on":  { label: "On", profile: search, env: { TODO_SEARCH: "true" } }
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


def _combo(ctx, **picks):
    """The combination answering the given choices as picked, the rest default."""
    for cb in ctx["combos"]:
        sel = {s["choice"]: s["option"] for s in cb["selections"]}
        if all(sel[k] == v for k, v in picks.items()) and all(s["default"] for s in cb["selections"] if s["choice"] not in picks):
            return cb
    raise AssertionError(f"no combination for {picks}")


class TestStackChoices(unittest.TestCase):
    def _render(self, tmp: Path):
        (tmp / "compose.yaml").write_text(CHOICES_COMPOSE)
        (tmp / "example.env").write_text(CHOICES_ENV)
        (tmp / "Containerfile.j2").write_text(CONTAINERFILE_J2)
        with _chdir(tmp):
            cfg = dbuild_config.load(tmp)
            ctx = docs._enrich_metadata(cfg, None)
            outputs, errors = docs.render_generated(cfg, argparse.Namespace(community=None), tmp)
        # Nothing but the README is written for choices; the combinations'
        # files live in the context, keyed by name here for the asserts.
        self.assertEqual([k for k in outputs if k.startswith(("compose.", "example."))], [])
        for cb in ctx["combos"]:
            outputs[cb["compose_file"]] = cb["compose_text"]
            outputs[cb["env_file"]] = cb["example_env"]
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
        pg = _combo(ctx, database="postgres")
        self.assertEqual([(p["name"], p["kind"]) for p in pg["parts"]], [("todo", "app"), ("postgres", "db")])
        self.assertEqual(pg["parts"][1]["image"], "postgres:17")
        self.assertTrue(pg["jails"][1]["name"].endswith("_postgres"), pg["jails"])  # <image>_postgres
        self.assertIn("@CONTAINER_CONFIG_ROOT@/todo/postgres", pg["folders"])
        ext = _combo(ctx, database="external")
        self.assertEqual(ext["parts"][-1]["kind"], "none")
        both = _combo(ctx, database="postgres", search="on")
        self.assertIn("  meili:", both["compose_text"], "two choices answered at once, in one file")
        self.assertIn("  postgres:", both["compose_text"])
        self.assertEqual(both["compose_file"], "compose.database-postgres.search-on.yaml")

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
        i_sql, i_pg, i_ext = (readme.index(h) for h in ("#### SQLite, Off (default)", "#### PostgreSQL, Off", "#### Your own, Off"))
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
        pg = aj[aj.index("#### PostgreSQL, Off"):aj.index("#### MariaDB, Off")]
        self.assertIn("  todo-postgres:\n    name: todo_postgres\n    priority: 10", pg, "the engine's jail starts first")
        self.assertIn("POSTGRES_PASSWORD: !ENV '${TODO_DB_PASSWORD}'", pg, "reads the app's own variables")
        self.assertIn("TODO_DB_HOST=todo_postgres", pg, "the host is the jail's name")
        self.assertIn("template: !ENV '${PWD}/postgres-template.conf'", pg)
        self.assertIn("sysvshm: new", pg, "the jail template PostgreSQL needs ships with it")
        self.assertIn("device: !ENV '${DATABASE_LOCATION}'", pg)
        self.assertNotIn("- DATABASE_LOCATION: !ENV", pg, "a device variable is not handed to the app's jail")
        sq = aj[aj.index("#### SQLite, Off (default)"):aj.index("#### PostgreSQL, Off")]
        self.assertNotIn("postgres", sq)


# ── A stack with an authored director and part choices (immich-shaped) ──

STACK_COMPOSE = """\
name: photos
x-daemonless:
  title: "Photos"
  description: "A photo stack."
  category: "Photos & Media"
  icon: ":test:"
  upstream_url: "https://example.com"
  user: "bsd"
  type: stack
  choices:
    machine_learning:
      label: Machine learning
      doc: Faces and search. Needs memory.
      default: "on"
      options:
        "on":  { label: "On" }
        "off": { label: "Off", drop: [ml], env: { ML_ENABLED: "false" } }
    public_proxy:
      label: Public sharing
      default: "off"
      options:
        "off": { label: "Off" }
        "on":  { label: "On", profile: proxy }
  docs:
    services:
      server: "Web UI and API"
      ml: "Faces and search"
      proxy: "Public links"
    env:
      DB_PASSWORD: "Database password"
  appjail:
    depends_on:
      - name: db
        template: db-template.conf
    director:
      options:
        - alias:
      services:
        server:
          name: photos_server
          priority: 100
          options:
            - from: ghcr.io/daemonless/photos-server:latest
          oci:
            environment:
              - ML_ENABLED: "!ENV '${ML_ENABLED}'"
          volumes:
            - data: /data
        ml:
          name: photos_ml
          options:
            - from: ghcr.io/daemonless/photos-ml:latest
          volumes:
            - cache: /cache
        proxy:
          name: photos_proxy
          options:
            - from: ghcr.io/daemonless/photos-proxy:latest
        db:
          name: photos_db
          options:
            - from: ghcr.io/daemonless/postgres:17
            - template: "!ENV '${PWD}/db-template.conf'"
      volumes:
        data:
          device: "!ENV '${UPLOAD_LOCATION}'"
        cache:
          device: "!ENV '${CACHE_LOCATION}'"
services:
  server:
    image: ghcr.io/daemonless/photos-server:latest
    network_mode: host
    environment:
      ML_ENABLED: ${ML_ENABLED:-true}
    depends_on:
      - db
      - ml
    volumes:
      - ${UPLOAD_LOCATION}:/data
  ml:
    image: ghcr.io/daemonless/photos-ml:latest
    network_mode: host
    volumes:
      - ${CACHE_LOCATION}:/cache
  proxy:
    image: ghcr.io/daemonless/photos-proxy:latest
    network_mode: host
    profiles: [proxy]
  db:
    image: ghcr.io/daemonless/postgres:17
    network_mode: host
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}
"""

STACK_ENV = "UPLOAD_LOCATION=/containers/photos/library\nCACHE_LOCATION=/containers/photos/cache\nDB_PASSWORD=change-me\n"


class TestStackWithAuthoredDirector(unittest.TestCase):
    def _render(self, tmp: Path):
        (tmp / "compose.yaml").write_text(STACK_COMPOSE)
        (tmp / "example.env").write_text(STACK_ENV)
        (tmp / "db-template.conf").write_text("sysvshm: new\n")
        with _chdir(tmp):
            cfg = dbuild_config.load(tmp)
            ctx = docs._enrich_metadata(cfg, None)
            outputs, errors = docs.render_generated(cfg, argparse.Namespace(community=None), tmp)
        self.assertEqual([k for k in outputs if k.startswith(("compose.", "example."))], [], "choices write nothing to disk")
        for cb in ctx["combos"]:
            outputs[cb["compose_file"]] = cb["compose_text"]
            outputs[cb["env_file"]] = cb["example_env"]
        return ctx, outputs, errors

    def test_a_part_can_be_dropped_and_depends_on_follows(self):
        with tempfile.TemporaryDirectory() as d:
            ctx, outputs, errors = self._render(Path(d))
        self.assertEqual(errors, [])
        off = outputs["compose.machine_learning-off.yaml"]
        self.assertNotIn("\n  ml:", off)
        self.assertIn("    depends_on:\n      - db\n", off, "the dropped service left the others' depends_on")
        self.assertNotIn("- ml", off)
        self.assertIn("ML_ENABLED=false", outputs["example.machine_learning-off.env"])
        on = outputs["compose.public_proxy-on.yaml"]
        self.assertIn("\n  proxy:", on)
        self.assertNotIn("profiles", on)

    def test_authored_director_is_sliced_per_option(self):
        with tempfile.TemporaryDirectory() as d:
            ctx, outputs, _ = self._render(Path(d))
        off = _combo(ctx, machine_learning="off")
        self.assertNotIn("photos_ml", off["director_text"])
        self.assertNotIn("cache:", off["director_text"], "a volume only the dropped jail used is gone too")
        self.assertIn("photos_db", off["director_text"])
        self.assertIn("ML_ENABLED=false", off["director_env_text"])
        on = _combo(ctx, machine_learning="on")
        self.assertIn("photos_ml", on["director_text"])
        both = _combo(ctx, machine_learning="off", public_proxy="on")
        self.assertEqual([j["name"] for j in both["jails"]], ["photos_server", "photos_proxy", "photos_db"])
        self.assertNotIn("photos_proxy", on["director_text"], "a profiled jail is out unless its option is picked")
        self.assertEqual([j["name"] for j in off["jails"]], ["photos_server", "photos_db"])
        self.assertIn("priority: 100", ctx["director_override_text"], "the author's director, verbatim, for the stack")
        self.assertEqual(ctx["sidecar_templates"][0]["file"], "db-template.conf")

    def test_readme_has_parts_and_the_authored_director(self):
        with tempfile.TemporaryDirectory() as d:
            ctx, outputs, _ = self._render(Path(d))
        readme = outputs["README.md"]
        self.assertIn("## Parts", readme)
        self.assertIn("| **ml** | `ghcr.io/daemonless/photos-ml:latest` | Faces and search |", readme)
        self.assertIn("| **proxy** | `ghcr.io/daemonless/photos-proxy:latest` | Public links (with proxy) |", readme)
        self.assertNotIn("## Version Tags", readme, "a stack has no tags of its own")
        aj = readme[readme.index("### AppJail Director"):]
        self.assertIn("priority: 100", aj)
        self.assertIn("**db-template.conf**", aj)
        self.assertIn("sysvshm: new", aj)

    def test_the_line_resolves_tags_and_skips_the_clock_mount(self):
        compose = STACK_COMPOSE.replace("    image: ghcr.io/daemonless/photos-server:latest\n    network_mode: host\n",
                                        "    image: ghcr.io/daemonless/photos-server:${TAG:-latest}\n    network_mode: host\n", 1)
        compose = compose.replace("      - ${UPLOAD_LOCATION}:/data\n", "      - ${UPLOAD_LOCATION}:/data\n      - /etc/localtime:/etc/localtime:ro\n")
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            (t / "compose.yaml").write_text(compose)
            (t / "example.env").write_text(STACK_ENV)
            (t / "db-template.conf").write_text("sysvshm: new\n")
            with _chdir(t):
                ctx = docs._enrich_metadata(dbuild_config.load(t), None)
        on = ctx["combos"][0]
        self.assertTrue(on["default"])
        self.assertEqual(on["parts"][0]["image"], "photos-server:latest", "the compose fallback resolves")
        self.assertEqual(on["folders"], ["@CONTAINER_CONFIG_ROOT@/photos/library", "@CONTAINER_CONFIG_ROOT@/photos/cache"])

    def test_bundle_on_disk_is_the_default_answer(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            (t / "compose.yaml").write_text(STACK_COMPOSE)
            (t / "example.env").write_text(STACK_ENV)
            (t / "db-template.conf").write_text("sysvshm: new\n")
            with _chdir(t):
                cfg = dbuild_config.load(t)
                docs.generate_appjail_files(cfg, t / "bundle")
            director = (t / "bundle" / "appjail-director.yml").read_text()
        self.assertIn("photos_ml", director, "a part on by default is in")
        self.assertNotIn("photos_proxy", director, "a part behind a profile is out of the default bundle")
        self.assertIn("photos_db", director)

    def test_choices_for_fjord(self):
        from dbuild import choices as choices_mod
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            (t / "compose.yaml").write_text(STACK_COMPOSE)
            (t / "example.env").write_text(STACK_ENV)
            with _chdir(t):
                cfg = dbuild_config.load(t)
                data = choices_mod.to_fjord(cfg.metadata.choices, cfg.compose_text, cfg.compose_data)
        ml, proxy = data["choices"]
        off = next(o for o in ml["options"] if o["id"] == "off")
        self.assertEqual(off["drop"], ["ml"])
        self.assertEqual(off["env"], {"ML_ENABLED": "false"})
        on = next(o for o in proxy["options"] if o["id"] == "on")
        self.assertTrue(on["services"].startswith("  proxy:\n    image: ghcr.io/daemonless/photos-proxy:latest\n"), on["services"])
        self.assertNotIn("profiles", on["services"], "the profile line is dbuild's business, not the consumer's")

    def test_choices_for_fjord_database_kind(self):
        from dbuild import choices as choices_mod
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            (t / "compose.yaml").write_text(CHOICES_COMPOSE)
            (t / "example.env").write_text(CHOICES_ENV)
            (t / "Containerfile.j2").write_text(CONTAINERFILE_J2)
            with _chdir(t):
                cfg = dbuild_config.load(t)
                data = choices_mod.to_fjord(cfg.metadata.choices, cfg.compose_text, cfg.compose_data)
        db = data["choices"][0]
        pg = next(o for o in db["options"] if o["id"] == "postgres")
        self.assertIn("  postgres:\n    image: ghcr.io/daemonless/postgres:17", pg["services"])
        self.assertEqual(pg["depends_on"], {"todo": ["postgres"]})
        self.assertEqual(pg["secrets"], ["TODO_DB_PASSWORD"])
        self.assertEqual(pg["defaults"]["DATABASE_LOCATION"], "{{base}}/{{stack}}/postgres", "the folder follows the stack, not the app")
        ext = next(o for o in db["options"] if o["id"] == "external")
        self.assertEqual([a["name"] for a in ext["ask"]][:2], ["TODO_DB_TYPE", "TODO_DB_HOST"])
