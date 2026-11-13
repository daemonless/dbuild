"""Unit tests for dbuild.init."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dbuild import init
from dbuild.init import _build_variants_block, run


def _base_args(**overrides):
    args = argparse.Namespace(
        name="testapp",
        title="TestApp",
        category="Apps",
        type="generic",
        port=8080,
        variants="latest",
        community=None,
        dry_run=False,
        woodpecker=False,
        github=False,
        freebsd_port=None,
        flavors=None,
        dir=None,
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


class TestRun(unittest.TestCase):
    """Tests for run()."""

    def test_idempotent(self):
        """Running init twice should skip all files."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = _base_args()
            rc1 = run(args)
            rc2 = run(args)
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)

    def test_returns_zero(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = _base_args()
            rc = run(args)
            self.assertEqual(rc, 0)

    def test_config_yaml_has_no_unrendered_placeholder(self):
        """Regression: variants_block must actually be substituted, not left
        as a literal `{{ variants_block }}` placeholder in the output."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = _base_args(variants="latest,pkg,pkg-latest")
            run(args)
            content = (Path(d) / ".daemonless" / "config.yaml").read_text()
        self.assertNotIn("variants_block", content)
        self.assertIn("- tag: latest", content)
        self.assertIn("- tag: pkg", content)
        self.assertIn("- tag: pkg-latest", content)
        self.assertIn('BASE_VERSION: "15.1-latest"', content)

    def test_scaffolds_base_exec_layout(self):
        """The launcher is the image's CMD on base-exec; no s6 service dir."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            run(_base_args(variants="latest,pkg"))
            root = Path(d) / "root"
            launcher = root / "daemonless" / "libexec" / "testapp"
            setup = root / "daemonless" / "init.d" / "20-testapp"
            self.assertTrue(launcher.stat().st_mode & 0o111)
            self.assertTrue(setup.stat().st_mode & 0o111)
            launcher_text = launcher.read_text()
            setup_text = setup.read_text()
            self.assertFalse((root / "etc" / "services.d").exists())
            self.assertFalse((root / "healthz").exists())
            templates = [
                (Path(d) / n).read_text() for n in ("Containerfile.j2", "Containerfile.pkg.j2")
            ]
        # /init drops privileges before the launcher; s6 tools aren't in base-exec
        self.assertNotIn("s6-", launcher_text)
        self.assertNotIn("dsu", launcher_text)
        # exec must continue onto the arg lines, not drop them
        self.assertIn("exec /usr/local/bin/testapp \\\n", launcher_text)
        self.assertIn("set -e", setup_text)
        for cf in templates:
            self.assertIn("FROM ghcr.io/daemonless/base-exec:", cf)
            self.assertIn('CMD ["/daemonless/libexec/testapp"]', cf)
            self.assertIn('ENV HEALTHCHECK_URL="${HEALTHCHECK_ENDPOINT}"', cf)
            self.assertNotIn("services.d", cf)
            self.assertNotIn("TUNNEL_TOKEN", cf)

    def test_containerfile_keeps_generator_placeholders(self):
        """compose.yaml stays the source of the labels: the scaffolded .j2
        keeps {{ title }} and friends for `dbuild generate`, while compose
        and config get the real values."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            run(_base_args(variants="pkg", type="dotnet"))
            cf = (Path(d) / "Containerfile.pkg.j2").read_text()
            compose = (Path(d) / "compose.yaml").read_text()
            config = (Path(d) / ".daemonless" / "config.yaml").read_text()
        for key in ("title", "description", "repo_url", "web_url", "category"):
            self.assertIn("{{ " + key + " }}", cf)
        self.assertIn("{%- if mlock %}", cf)
        self.assertIn("{{ ports[0].port }}", cf)
        self.assertIn('ARG PACKAGES="${PKG_NAME}"', cf)
        self.assertNotIn("{%", compose)
        self.assertIn('title: "TestApp"', compose)
        self.assertIn("mlock: true", compose)
        self.assertIn("restart: unless-stopped", compose)
        self.assertIn('- "8080:8080"', compose)
        self.assertIn("org.freebsd.jail.allow.mlock=true", config)

    def test_pkg_only_scaffold_publishes_latest(self):
        """Without a source variant the pkg tag is the default and carries
        the latest alias; the package name is set once, not per variant."""
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            run(_base_args(variants="pkg,pkg-latest"))
            config = (Path(d) / ".daemonless" / "config.yaml").read_text()
            self.assertFalse((Path(d) / "Containerfile.j2").exists())
        self.assertIn("build:\n  pkg_name: testapp\n  variants:\n", config)
        self.assertIn(
            "    - tag: pkg\n      containerfile: Containerfile.pkg\n"
            '      aliases: ["latest"]\n      default: true\n',
            config,
        )
        self.assertNotIn("pkg_name: testapp\n      ", config.split("variants:")[1])

    def test_port_lookup_failure_scaffolds_nothing(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ), patch("dbuild.init._fetch_port_metadata", return_value=None):
            rc = run(_base_args(freebsd_port="audio/nosuchport"))
            self.assertEqual(rc, 1)
            self.assertEqual(list(Path(d).iterdir()), [])


class TestFlavors(unittest.TestCase):
    """Tests for --flavors validation and wiring in run()."""

    def test_flavors_without_freebsd_port_errors(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ):
            args = _base_args(flavors="lua,wolfssl")
            rc = run(args)
        self.assertEqual(rc, 1)
        self.assertFalse((Path(d) / ".daemonless").exists())

    def test_unresolvable_flavor_aborts_without_scaffolding(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ), patch("dbuild.init._fetch_port_metadata", return_value=None):
            args = _base_args(freebsd_port="editors/joe", flavors="bogus")
            rc = run(args)
        self.assertEqual(rc, 1)
        self.assertFalse((Path(d) / ".daemonless").exists())

    def test_flavors_cross_into_variants_block(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ), patch(
            "dbuild.init._fetch_port_metadata",
            return_value={
                "name": "joe", "pkgname": "joe",
                "flavor_pkgnames": {"tiny": "joe", "x11": "joe-x11"},
            },
        ):
            args = _base_args(
                freebsd_port="editors/joe", variants="latest,pkg,pkg-latest",
                flavors="tiny,x11",
            )
            rc = run(args)
            content = (Path(d) / ".daemonless" / "config.yaml").read_text()
        self.assertEqual(rc, 0)
        self.assertIn("- tag: latest", content)
        self.assertNotIn("- tag: pkg\n", content)  # replaced by flavor tags
        self.assertIn("- tag: pkg-tiny", content)
        self.assertIn("pkg_name: joe-x11", content)
        self.assertIn("- tag: pkg-latest-tiny", content)
        self.assertIn("- tag: pkg-latest-x11", content)


class TestBuildVariantsBlock(unittest.TestCase):
    """Tests for _build_variants_block() in isolation."""

    def test_no_flavors_matches_legacy_template(self):
        block = _build_variants_block(["latest", "pkg", "pkg-latest"], {}, "latest")
        self.assertEqual(block, (
            "    - tag: latest\n"
            "      containerfile: Containerfile\n"
            "      default: true\n"
            "    - tag: pkg\n"
            "      containerfile: Containerfile.pkg\n"
            "    - tag: pkg-latest\n"
            "      containerfile: Containerfile.pkg\n"
            '      args:\n'
            '        BASE_VERSION: "15.1-latest"'
        ))

    def test_flavors_only_cross_pkg_tags(self):
        block = _build_variants_block(["latest"], {"lua": "app-lua"})
        self.assertEqual(block, (
            "    - tag: latest\n"
            "      containerfile: Containerfile"
        ))

    def test_flavors_cross_pkg_latest_suffix_preserved(self):
        block = _build_variants_block(["pkg-latest"], {"lua": "app-lua"})
        self.assertIn("- tag: pkg-latest-lua", block)
        self.assertIn("pkg_name: app-lua", block)
        self.assertIn('BASE_VERSION: "15.1-latest"', block)


def _fake_port(root: Path, origin: str, files: dict[str, str]) -> Path:
    """A port directory under a fake ports tree (root/Mk marks the tree)."""
    (root / "Mk").mkdir(exist_ok=True)
    port = root / origin
    for rel, content in {"Makefile": "PORTNAME=x\n", **files}.items():
        (port / rel).parent.mkdir(parents=True, exist_ok=True)
        (port / rel).write_text(content)
    return port


_PORT_META = {
    "origin": "net-p2p/foo", "name": "foo", "pkgname": "foo-daemon", "version": "1.0",
    "source": "ports tree", "description": "Foo daemon", "license": "MIT",
    "license_raw": "MIT", "license_unmapped": [], "web_url": "https://foo.example/",
    "upstream_url": "https://foo.example/", "category": "Downloaders",
    "category_raw": "net-p2p", "binary": "/usr/local/sbin/foo-daemon",
    "binary_source": "rc script", "binaries": ["/usr/local/sbin/foo-daemon", "/usr/local/bin/foo-cli"],
    "extra_args": "serve", "rc_file": "/usr/ports/net-p2p/foo/files/foo.in",
    "rc_hints": ["command=/usr/local/sbin/foo-daemon serve"], "port": 9091,
    "port_source": "pkg-message", "port_candidates": [9091], "flavor_pkgnames": {},
    "flavors": [], "in_repo": True, "packages": "foo-daemon", "run_deps": "",
    "freshports_url": "https://www.freshports.org/net-p2p/foo/",
}


class TestLicense(unittest.TestCase):
    """_to_spdx maps real FreeBSD LICENSE tokens, never near misses."""

    def test_single(self):
        self.assertEqual(init._to_spdx("GPLv3+"), ("GPL-3.0-or-later", []))
        self.assertEqual(init._to_spdx("ISCL"), ("ISC", []))
        self.assertEqual(init._to_spdx("AGPLv3"), ("AGPL-3.0-only", []))

    def test_multi_and_dual(self):
        self.assertEqual(init._to_spdx("GPLv3+ LGPL3+", "multi"), ("GPL-3.0-or-later AND LGPL-3.0-or-later", []))
        self.assertEqual(init._to_spdx("MIT APACHE20", "dual"), ("MIT OR Apache-2.0", []))

    def test_creative_commons_passthrough(self):
        self.assertEqual(init._to_spdx("CC-BY-SA-4.0"), ("CC-BY-SA-4.0", []))

    def test_unset_and_unmapped_are_unknown(self):
        self.assertEqual(init._to_spdx("NA"), ("UNKNOWN", ["NA"]))
        self.assertEqual(init._to_spdx(""), ("UNKNOWN", []))
        # one unmapped token taints the whole expression: no partial answers
        self.assertEqual(init._to_spdx("MIT PD", "multi"), ("UNKNOWN", ["PD"]))


class TestPortSpec(unittest.TestCase):
    """_resolve_port_spec accepts origins, bare names, URLs and tree paths."""

    def test_origin_and_url_forms(self):
        self.assertEqual(init._resolve_port_spec("net-p2p/transmission", None), "net-p2p/transmission")
        self.assertEqual(init._resolve_port_spec("net-p2p/transmission/", None), "net-p2p/transmission")
        self.assertEqual(
            init._resolve_port_spec("https://www.freshports.org/net-p2p/transmission/", None),
            "net-p2p/transmission",
        )

    def test_bare_name_in_tree_and_tree_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _fake_port(root, "www/gitea", {})
            self.assertEqual(init._resolve_port_spec("gitea", root), "www/gitea")
            self.assertEqual(init._resolve_port_spec(f"{root}/www/gitea", root), "www/gitea")

    def test_bare_name_ambiguous_or_missing(self):
        with tempfile.TemporaryDirectory() as d, patch("dbuild.init._pkg_rquery", return_value=""):
            root = Path(d)
            _fake_port(root, "www/foo", {})
            _fake_port(root, "net/foo", {})
            self.assertIsNone(init._resolve_port_spec("foo", root))
            self.assertIsNone(init._resolve_port_spec("nope", root))
            self.assertIsNone(init._resolve_port_spec("a/b/c", root))

    def test_bare_name_falls_back_to_pkg_origin(self):
        with patch("dbuild.init._pkg_rquery", return_value="net-p2p/transmission-components"):
            self.assertEqual(init._resolve_port_spec("transmission-daemon", None), "net-p2p/transmission-components")


class TestRcScript(unittest.TestCase):
    """_rc_script_info finds the daemon through rc.d conventions."""

    def test_direct_command_with_extra_args_and_name(self):
        rc = (
            "name=gitea\n"
            ': ${gitea_enable:="NO"}\n'
            ': ${gitea_custom:="%%PREFIX%%/etc/${name}"}\n'
            'command="%%PREFIX%%/sbin/${name} web"\n'
        )
        with tempfile.TemporaryDirectory() as d:
            port = _fake_port(Path(d), "www/gitea", {"files/gitea.in": rc})
            info = init._rc_script_info(port, ["gitea"])
        self.assertEqual(info["binary"], "/usr/local/sbin/gitea")
        self.assertEqual(info["extra_args"], "web")
        self.assertIn('command="/usr/local/sbin/gitea web"', info["hints"])
        self.assertIn(': ${gitea_custom:="/usr/local/etc/gitea"}', info["hints"])
        self.assertNotIn(': ${gitea_enable:="NO"}', info["hints"])

    def test_daemon_wrapper_procname_port_and_continuations(self):
        rc = (
            "name=foo\n"
            ': ${foo_port:=8384}\n'
            'procname="%%LOCALBASE%%/bin/foo"\n'
            'command="/usr/sbin/daemon"\n'
            'foo_flags=" \\\n'
            "\t-g ${foo_conf_dir} \\\n"
            '\t--no-browser"\n'
        )
        with tempfile.TemporaryDirectory() as d:
            port = _fake_port(Path(d), "net/foo", {"files/foo.in": rc})
            info = init._rc_script_info(port, ["foo"])
        self.assertEqual(info["binary"], "/usr/local/bin/foo")
        self.assertEqual(info["ports"], [8384])
        self.assertEqual(
            [h for h in info["hints"] if "foo_flags" in h or h.startswith("    -")],
            ['foo_flags=" \\', "    -g ${foo_conf_dir} \\", '    --no-browser"'],
        )

    def test_no_rc_script(self):
        with tempfile.TemporaryDirectory() as d:
            port = _fake_port(Path(d), "net/foo", {})
            self.assertEqual(init._rc_script_info(port, []), {})
            self.assertEqual(init._rc_script_info(port, ["foo"]), {})

    def test_continued_command_args_and_placeholders(self):
        """adguardhome: the binary sits on the line after command_args=\"... \\"."""
        rc = (
            'name="adguardhome"\n'
            ': ${adguardhome_user:="%%AGHUSER%%"}\n'
            'command="/usr/sbin/daemon"\n'
            'command_args="-o ${logfile} -t ${name} -P ${pidfile} \\\n'
            "\t%%PREFIX%%/bin/adguardhome -c %%ETCDIR%%/AdGuardHome.yaml --no-check-update\"\n"
        )
        with tempfile.TemporaryDirectory() as d:
            port = _fake_port(Path(d), "www/adguardhome", {"files/adguardhome.in": rc})
            info = init._rc_script_info(port, ["adguardhome"], {"ETCDIR": "/usr/local/etc/adguardhome"})
        self.assertEqual(info["binary"], "/usr/local/bin/adguardhome")
        self.assertIn(': ${adguardhome_user:="<AGHUSER>"}', info["hints"])
        # the hints keep the continuation as two lines
        self.assertIn("    /usr/local/bin/adguardhome -c /usr/local/etc/adguardhome/AdGuardHome.yaml --no-check-update\"", info["hints"])


class TestPortProbes(unittest.TestCase):
    def test_wrong_category_suggests_the_right_origin(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init._portsdir", return_value=Path(d)
        ), patch("dbuild.init._pkg_rquery", return_value="www/adguardhome"), patch(
            "dbuild.init.log.error"
        ) as err:
            _fake_port(Path(d), "www/adguardhome", {})
            self.assertIsNone(init._fetch_port_metadata("dns/adguardhome"))
        self.assertIn("did you mean: www/adguardhome", [c.args[0] for c in err.call_args_list])

    def test_plist_binaries(self):
        with tempfile.TemporaryDirectory() as d:
            port = _fake_port(Path(d), "net/foo", {
                "pkg-plist": "bin/foo\n%%NOX%%bin/foo-nox\nlib/libfoo.so\n@sample etc/foo.conf.sample\n",
            })
            bins = init._plist_binaries(port, '"@dir /var/db/foo" sbin/food /usr/local/bin/foo')
        self.assertEqual(bins, ["/usr/local/sbin/food", "/usr/local/bin/foo", "/usr/local/bin/foo-nox"])

    def test_message_ports(self):
        with tempfile.TemporaryDirectory() as d:
            port = _fake_port(Path(d), "net/foo", {
                "pkg-message": "Browse to http://localhost:9091/web (not 127.0.0.1:9091).\nVersion 1.2.3\n",
            })
            self.assertEqual(init._message_ports(port), [9091])

    def test_pick_binary(self):
        self.assertEqual(init._pick_binary("/usr/local/bin/x", ["/usr/local/bin/y"], ["y"]), ("/usr/local/bin/x", "rc script"))
        self.assertEqual(init._pick_binary("", ["/usr/local/bin/y"], []), ("/usr/local/bin/y", "plist"))
        bins = ["/usr/local/bin/foo", "/usr/local/bin/foo-nox"]
        self.assertEqual(init._pick_binary("", bins, ["foo-nox", "foo"]), ("/usr/local/bin/foo-nox", "plist"))
        self.assertEqual(init._pick_binary("", bins, ["zzz"]), ("/usr/local/bin/foo", "plist (first of several)"))
        self.assertEqual(init._pick_binary("", [], ["foo"]), ("", ""))


class TestPortScaffold(unittest.TestCase):
    """run() with --freebsd-port: port-aware defaults and the run script."""

    def _scaffold(self, d: Path, **overrides):
        with patch("dbuild.init.Path.cwd", return_value=d), patch(
            "dbuild.init._fetch_port_metadata", return_value=dict(_PORT_META)
        ):
            fields = dict(name=None, title=None, port=None, variants=None,
                          freebsd_port="net-p2p/foo")
            fields.update(overrides)
            args = _base_args(**fields)
            self.assertEqual(run(args), 0)

    def test_port_defaults_and_run_script(self):
        with tempfile.TemporaryDirectory() as d:
            self._scaffold(Path(d))
            root = Path(d)
            config = (root / ".daemonless" / "config.yaml").read_text()
            launcher = (root / "root" / "daemonless" / "libexec" / "foo").read_text()
            compose = (root / "compose.yaml").read_text()
            self.assertFalse((root / "Containerfile.j2").exists())
            self.assertTrue((root / "Containerfile.pkg.j2").exists())
            self.assertFalse((root / "root" / "healthz").exists())
        self.assertIn("  pkg_name: foo-daemon\n", config)
        self.assertIn("- tag: pkg\n", config)
        self.assertIn("- tag: pkg-latest", config)
        self.assertNotIn("- tag: latest", config)
        self.assertIn("port: 9091", config)
        self.assertIn('- "9091:9091"', compose)
        self.assertIn('description: "Foo daemon"', compose)
        self.assertIn("exec /usr/local/sbin/foo-daemon serve\n", launcher)
        self.assertIn("#   command=/usr/local/sbin/foo-daemon serve", launcher)
        self.assertIn("# Other programs in the package: foo-cli", launcher)
        self.assertNotIn("--config /config", launcher)

    def test_title_and_www_review(self):
        meta = dict(_PORT_META, name="node_exporter", category="Network", category_raw="www")
        with tempfile.TemporaryDirectory() as d, patch(
            "dbuild.init.Path.cwd", return_value=Path(d)
        ), patch("dbuild.init._fetch_port_metadata", return_value=meta), patch(
            "dbuild.init.log.plain"
        ) as plain:
            run(_base_args(name=None, title=None, port=None, variants=None, freebsd_port="www/node_exporter"))
            compose = (Path(d) / "compose.yaml").read_text()
        self.assertIn('title: "Node Exporter"', compose)
        self.assertIn('category: "Network"', compose)
        lines = [c.args[0] for c in plain.call_args_list]
        self.assertTrue(any("www/ ports land in Network" in ln for ln in lines), lines)
        self.assertTrue(any("edit root/daemonless/libexec/node_exporter" in ln for ln in lines), lines)

    def test_explicit_flags_win(self):
        with tempfile.TemporaryDirectory() as d:
            self._scaffold(Path(d), port=3000, variants="latest,pkg", title="FooBar")
            root = Path(d)
            config = (root / ".daemonless" / "config.yaml").read_text()
            compose = (root / "compose.yaml").read_text()
            self.assertTrue((root / "Containerfile.j2").exists())
        self.assertIn("port: 3000", config)
        self.assertIn("- tag: latest", config)
        self.assertIn('title: "FooBar"', compose)

    def test_workspace_root_scaffolds_into_subdir(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ("a", "b"):
                (root / name / ".daemonless").mkdir(parents=True)
                (root / name / ".daemonless" / "config.yaml").write_text("")
            self.assertTrue(init._looks_like_workspace_root(root))
            self._scaffold(root)
            self.assertTrue((root / "foo" / "compose.yaml").exists())
            self.assertFalse((root / "compose.yaml").exists())
            self.assertFalse(init._looks_like_workspace_root(root / "foo"))

    def test_dir_flag(self):
        with tempfile.TemporaryDirectory() as d:
            self._scaffold(Path(d), dir=str(Path(d) / "sub" / "foo"))
            self.assertTrue((Path(d) / "sub" / "foo" / "compose.yaml").exists())


class TestMlockRendering(unittest.TestCase):
    """The mlock conditional must not leave doubled blank lines or junk whitespace."""

    def _render(self, mlock: str) -> tuple[str, str, str]:
        ctx = {k: "x" for k in (
            "name", "title", "category", "description", "upstream_url", "web_url",
            "freshports_url", "community", "repo_url", "license", "pkgname", "packages",
        )}
        ctx.update(port="8080", mlock=mlock, mlock_bool=mlock, packages_arg="${PKG_NAME}",
                   build_block="  variants:\n    - tag: pkg\n      containerfile: Containerfile.pkg")
        return (
            init._render_template("compose.yaml", ctx),
            init._render_template("config.yaml", ctx),
            init._render_template("template-pkg.j2", ctx, generator=True),
        )

    def test_off(self):
        compose, config, cf = self._render("false")
        self.assertNotIn("\n\n\n", compose)
        self.assertNotIn("annotations", compose)
        self.assertNotIn("annotations", config)
        self.assertNotRegex(config, r"\n[ \t]+\n")
        self.assertNotRegex(compose, r"\n[ \t]+\n")
        # the Containerfile's mlock block is the generator's, from compose.yaml
        self.assertIn('{%- if mlock %}\n      org.freebsd.jail.allow.mlock="required" \\\n{%- endif %}', cf)

    def test_on(self):
        compose, config, _ = self._render("true")
        self.assertNotIn("\n\n\n", compose)
        self.assertIn('restart: unless-stopped\n\n    annotations:\n      org.freebsd.jail.allow.mlock: "true"\n\n    environment:', compose)
        self.assertIn('  # ready: "listening on"\n  annotations:\n    - "org.freebsd.jail.allow.mlock=true"\n', config)
        self.assertNotRegex(config, r"\n[ \t]+\n")


if __name__ == "__main__":
    unittest.main()
