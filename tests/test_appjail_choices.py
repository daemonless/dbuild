"""Each choice option's AppJail form: what fjord adds to the bundle's director
so a non-default pick runs on AppJail too."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dbuild import appjail_choices
from dbuild import config as dbuild_config
from tests.test_docs import (
    CHOICES_COMPOSE,
    CHOICES_ENV,
    CONTAINERFILE_J2,
    STACK_COMPOSE,
    STACK_ENV,
    _chdir,
)


def _forms(compose: str, env: str, containerfile: str | None = None):
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        (t / "compose.yaml").write_text(compose)
        (t / "example.env").write_text(env)
        if containerfile:
            (t / "Containerfile.j2").write_text(containerfile)
        with _chdir(t):
            return appjail_choices.option_forms(dbuild_config.load(t))


class TestOptionForms(unittest.TestCase):
    def test_a_database_option_brings_its_jail(self):
        forms = _forms(CHOICES_COMPOSE, CHOICES_ENV, CONTAINERFILE_J2)
        pg = forms["database"]["postgres"]
        self.assertIn("from: ghcr.io/daemonless/postgres:17", pg["director"])
        self.assertIn("!ENV '${TODO_DB_PASSWORD}'", pg["director"], "values come from .env, as the tag")
        self.assertNotIn("'!ENV", pg["director"], "the tag, not a quoted string")
        self.assertIn("device: !ENV '${DATABASE_LOCATION}'", pg["director"])
        # The host variable names the jail by its director service.
        (svc, var), = pg["hostnames"].items()
        self.assertEqual(var, "TODO_DB_HOST")
        self.assertIn(f"  {svc}:\n", pg["director"])
        self.assertNotIn("sqlite", forms.get("database", {}), "nothing to add, no form")

    def test_a_part_option_brings_its_authored_jail(self):
        forms = _forms(STACK_COMPOSE, STACK_ENV)
        on = forms["public_proxy"]["on"]
        self.assertIn("proxy", on["director"])
        self.assertNotIn("ml:", on["director"], "only what the option adds, not the default jails")


if __name__ == "__main__":
    unittest.main()
