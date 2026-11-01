"""Port protocol in generated snippets, and the root-user implementation line."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from dbuild import config as dbuild_config
from dbuild import docs

from tests.test_docs import CONTAINERFILE_J2, _chdir

COMPOSE = """\
name: dns
x-daemonless:
  title: "DNS"
  description: "A resolver."
  category: "Network"
  icon: ":test:"
  upstream_url: "https://example.com"
  user: "{user}"
services:
  dns:
    image: ghcr.io/daemonless/dns:latest
    ports:
{ports}
"""


def _render(ports: list[str], user: str = "bsd") -> tuple[dict, str, str, str]:
    """Return (cfg.ports[0], README.md, docs-site page, podman run snippet).

    The podman run snippet only reaches a page when there is no compose snippet
    (CLI images), so it is rendered straight from its macro.
    """
    port_lines = "\n".join(f"      - {p}" for p in ports)
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        (repo / "compose.yaml").write_text(COMPOSE.format(user=user, ports=port_lines))
        (repo / "Containerfile.j2").write_text(CONTAINERFILE_J2)
        args = argparse.Namespace(community=None)
        with _chdir(repo):
            cfg = dbuild_config.load(repo)
            outputs, errors = docs.render_generated(cfg, args, repo)
            context = docs._enrich_metadata(cfg, None)
            env = docs._get_jinja_env(repo)
            site = env.get_template("README.mkdocs.j2").render(context, render_mode="mkdocs")
            run = env.get_template("README.j2").make_module(
                dict(context, render_mode="github")
            ).cli_snippet()
    assert not errors, errors
    return cfg.ports[0], outputs["README.md"], site, run


class TestPortProtocol(unittest.TestCase):
    def test_dual_protocol_keeps_both_mappings_distinct(self):
        _, readme, site, run = _render(['"53:53/udp"', '"53:53/tcp"'])
        # udp keeps its suffix; tcp stays bare because it is the default
        self.assertIn('- "53:53/udp"\n      - "53:53"\n', readme)
        self.assertIn(':53/udp"\n          - "53:53"\n', site)
        self.assertIn("-p 53:53/udp \\\n  -p 53:53 \\", run)

    def test_tcp_only_image_renders_unchanged(self):
        _, readme, _, _ = _render(['"8080:8080"'])
        self.assertNotIn("/tcp", readme)

    def test_uppercase_tcp_is_still_the_default(self):
        _, readme, site, _ = _render(['"8080:8080/TCP"'])
        for page in (readme, site):
            self.assertNotIn("/TCP", page)
            self.assertNotIn("/tcp", page)
            # the AppJail snippet emits the protocol verbatim, so it must arrive lowercase
            self.assertNotIn("proto:TCP", page)
            self.assertIn("proto:tcp", page)

    def test_uppercase_udp_is_lowercased_everywhere(self):
        _, readme, _, run = _render(['"53:53/UDP"'])
        self.assertIn('- "53:53/udp"', readme)
        self.assertIn("proto:udp", readme)
        self.assertNotIn("UDP'", readme)
        self.assertIn("-p 53:53/udp", run)

    def test_single_field_short_form_strips_proto_from_published(self):
        port, readme, _, _ = _render(['"53/udp"'])
        self.assertIn('- "53:53/udp"', readme)
        self.assertNotIn("53/udp:", readme)
        self.assertEqual(port["published"], "53")
        self.assertEqual(port["target"], "53")
        self.assertEqual(port["protocol"], "udp")


class TestRootUserLine(unittest.TestCase):
    def test_root_image_does_not_claim_1000(self):
        _, readme, site, _ = _render(['"53:53/udp"'], user="root")
        for page in (readme, site):
            self.assertIn("stays root by design", page)
            self.assertNotIn("`root` (UID/GID", page)

    def test_non_root_image_keeps_puid_line(self):
        _, readme, site, _ = _render(['"8080:8080"'])
        self.assertIn("**User:** `bsd` (UID/GID via PUID/PGID", readme)
        self.assertIn("**User:** `bsd` (UID/GID set via", site)


if __name__ == "__main__":
    unittest.main()
