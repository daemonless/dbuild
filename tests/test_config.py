"""Unit tests for dbuild.config."""

from __future__ import annotations

import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

from dbuild.config import (
    _GLOBAL_CONFIG_PATH,
    _IGNORE_SUFFIXES,
    Config,
    _auto_detect_variants,
    _find_config_file,
    _git_remote_org,
    _global_extra_variants,
    _load_global_config,
    _parse_test_config,
    _parse_variants,
    load,
)


class TestFindConfigFile(unittest.TestCase):
    """Tests for _find_config_file()."""

    def test_no_config(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_find_config_file(Path(d)))

    def test_dbuild_yaml(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".dbuild.yaml"
            p.write_text("build: {}\n")
            self.assertEqual(_find_config_file(Path(d)), p)

    def test_daemonless_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config_dir = Path(d) / ".daemonless"
            config_dir.mkdir()
            p = config_dir / "config.yaml"
            p.write_text("build: {}\n")
            self.assertEqual(_find_config_file(Path(d)), p)

    def test_dbuild_yaml_preferred(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / ".dbuild.yaml"
            p1.write_text("build: {}\n")
            config_dir = Path(d) / ".daemonless"
            config_dir.mkdir()
            p2 = config_dir / "config.yaml"
            p2.write_text("build: {}\n")
            self.assertEqual(_find_config_file(Path(d)), p1)


class TestAutoDetectVariants(unittest.TestCase):
    """Tests for _auto_detect_variants()."""

    def test_no_containerfiles(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            result = _auto_detect_variants(Path(d))
            self.assertEqual(result, [])

    def test_only_containerfile(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile").touch()
            result = _auto_detect_variants(Path(d))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].tag, "latest")
            self.assertTrue(result[0].default)

    def test_only_pkg(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile.pkg").touch()
            result = _auto_detect_variants(Path(d))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].tag, "pkg")
            self.assertEqual(result[0].containerfile, "Containerfile.pkg")

    def test_both_containerfiles(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile").touch()
            (Path(d) / "Containerfile.pkg").touch()
            result = _auto_detect_variants(Path(d))
            self.assertEqual(len(result), 2)
            tags = [v.tag for v in result]
            self.assertEqual(tags, ["latest", "pkg"])

    def test_multiple_suffixes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile").touch()
            (Path(d) / "Containerfile.pkg").touch()
            (Path(d) / "Containerfile.dev").touch()
            result = _auto_detect_variants(Path(d))
            self.assertEqual(len(result), 3)
            tags = [v.tag for v in result]
            self.assertEqual(tags, ["latest", "dev", "pkg"])

    def test_no_hardcoded_args(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile.pkg").touch()
            result = _auto_detect_variants(Path(d))
            self.assertEqual(result[0].args, {})

    def test_pkg_name_propagated(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile").touch()
            result = _auto_detect_variants(Path(d), pkg_name="myapp")
            self.assertEqual(result[0].pkg_name, "myapp")

    def test_auto_version_propagated(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile").touch()
            result = _auto_detect_variants(Path(d), auto_version=True)
            self.assertTrue(result[0].auto_version)

    def test_j2_excluded(self):
        """Containerfile.j2 should NOT be detected as a variant."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile").touch()
            (Path(d) / "Containerfile.j2").touch()
            (Path(d) / "Containerfile.pkg").touch()
            result = _auto_detect_variants(Path(d))
            tags = [v.tag for v in result]
            self.assertEqual(tags, ["latest", "pkg"])
            self.assertNotIn("j2", tags)

    def test_all_ignore_suffixes_excluded(self):
        """All built-in ignore suffixes should be skipped."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile").touch()
            for suffix in _IGNORE_SUFFIXES:
                (Path(d) / f"Containerfile{suffix}").touch()
            result = _auto_detect_variants(Path(d))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].tag, "latest")

    def test_config_driven_ignore(self):
        """build.ignore should exclude additional files by name."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile").touch()
            (Path(d) / "Containerfile.pkg").touch()
            (Path(d) / "Containerfile.dev").touch()
            result = _auto_detect_variants(
                Path(d), ignore=["Containerfile.dev"],
            )
            tags = [v.tag for v in result]
            self.assertEqual(tags, ["latest", "pkg"])
            self.assertNotIn("dev", tags)

    def test_config_ignore_combined_with_suffix(self):
        """Config ignore and suffix ignore should both apply."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Containerfile").touch()
            (Path(d) / "Containerfile.j2").touch()
            (Path(d) / "Containerfile.dev").touch()
            (Path(d) / "Containerfile.pkg").touch()
            result = _auto_detect_variants(
                Path(d), ignore=["Containerfile.dev"],
            )
            tags = [v.tag for v in result]
            self.assertEqual(tags, ["latest", "pkg"])


class TestParseTestConfig(unittest.TestCase):
    """Tests for _parse_test_config()."""

    def test_no_cit(self):
        self.assertIsNone(_parse_test_config({}))

    def test_empty_cit(self):
        self.assertIsNone(_parse_test_config({"cit": {}}))

    def test_full_cit(self):
        data = {
            "cit": {
                "mode": "health",
                "port": 8080,
                "health": "/api",
                "wait": 60,
                "ready": "started",
                "screenshot_wait": 5,
                "screenshot": "/path/to/screenshot",
                "https": True,
                "compose": True,
                "annotations": ["org.freebsd.jail.allow.mlock=true"],
            }
        }
        result = _parse_test_config(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.mode, "health")
        self.assertEqual(result.port, 8080)
        self.assertEqual(result.health, "/api")
        self.assertEqual(result.wait, 60)
        self.assertEqual(result.ready, "started")
        self.assertEqual(result.screenshot_wait, 5)
        self.assertEqual(result.screenshot_path, "/path/to/screenshot")
        self.assertTrue(result.https)
        self.assertTrue(result.compose)
        self.assertEqual(result.annotations, ["org.freebsd.jail.allow.mlock=true"])

    def test_defaults(self):
        data = {"cit": {"mode": "port"}}
        result = _parse_test_config(data)
        self.assertEqual(result.wait, 120)
        self.assertFalse(result.https)
        self.assertFalse(result.compose)
        self.assertEqual(result.annotations, [])


class TestParseVariants(unittest.TestCase):
    """Tests for _parse_variants()."""

    def test_empty_data(self):
        self.assertEqual(_parse_variants({}), [])

    def test_no_variants(self):
        self.assertEqual(_parse_variants({"build": {}}), [])

    def test_single_variant(self):
        data = {
            "build": {
                "variants": [
                    {"tag": "latest", "containerfile": "Containerfile", "default": True}
                ]
            }
        }
        result = _parse_variants(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].tag, "latest")
        self.assertTrue(result[0].default)

    def test_variant_with_args(self):
        data = {
            "build": {
                "variants": [
                    {
                        "tag": "pkg",
                        "containerfile": "Containerfile.pkg",
                        "args": {"BASE_VERSION": "15-quarterly"},
                    }
                ]
            }
        }
        result = _parse_variants(data)
        self.assertEqual(result[0].args, {"BASE_VERSION": "15-quarterly"})

    def test_variant_with_aliases(self):
        data = {
            "build": {
                "variants": [
                    {"tag": "latest", "aliases": ["stable", "15"]}
                ]
            }
        }
        result = _parse_variants(data)
        self.assertEqual(result[0].aliases, ["stable", "15"])

    def test_build_auto_version_propagated(self):
        data = {
            "build": {
                "auto_version": True,
                "variants": [
                    {"tag": "latest"},
                    {"tag": "pkg", "auto_version": False},
                ]
            }
        }
        result = _parse_variants(data)
        self.assertTrue(result[0].auto_version)
        self.assertFalse(result[1].auto_version)


class TestLoad(unittest.TestCase):
    """Tests for load()."""

    @patch("dbuild.config._git_remote_org", return_value="myorg")
    def test_auto_detect(self, _mock_org):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            cfg = load(base)
            self.assertEqual(cfg.image, base.name)
            self.assertEqual(cfg.registry, "ghcr.io/myorg")
            self.assertEqual(len(cfg.variants), 1)
            self.assertEqual(cfg.variants[0].tag, "latest")

    @patch("dbuild.config._git_remote_org", return_value=None)
    def test_registry_fallback_localhost(self, _mock_org):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            cfg = load(base)
            self.assertEqual(cfg.registry, "localhost")

    @patch.dict("os.environ", {"DBUILD_REGISTRY": "myregistry.io/org"})
    def test_registry_env_override(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            cfg = load(base)
            self.assertEqual(cfg.registry, "myregistry.io/org")

    def test_from_yaml(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            config_dir = base / ".daemonless"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text(
                "type: base\n"
                "build:\n"
                "  architectures: [amd64, aarch64]\n"
                "  variants:\n"
                "    - tag: latest\n"
                "      containerfile: Containerfile\n"
                "      default: true\n"
                "cit:\n"
                "  mode: health\n"
                "  port: 5432\n"
            )
            cfg = load(base)
            self.assertEqual(cfg.type, "base")
            self.assertEqual(cfg.architectures, ["amd64", "aarch64"])
            self.assertEqual(len(cfg.variants), 1)
            self.assertIsNotNone(cfg.test)
            self.assertEqual(cfg.test.mode, "health")
            self.assertEqual(cfg.test.port, 5432)

    def test_yaml_fallback_to_auto_detect(self):
        """When config exists but has no variants, auto-detect kicks in."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            config_dir = base / ".daemonless"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text("build: {}\n")
            cfg = load(base)
            self.assertEqual(len(cfg.variants), 1)
            self.assertEqual(cfg.variants[0].tag, "latest")

    def test_yaml_build_ignore(self):
        """build.ignore in config should exclude files from auto-detection."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            (base / "Containerfile.dev").touch()
            (base / "Containerfile.pkg").touch()
            (base / ".dbuild.yaml").write_text(
                "build:\n"
                "  ignore:\n"
                "    - Containerfile.dev\n"
            )
            cfg = load(base)
            tags = [v.tag for v in cfg.variants]
            self.assertIn("latest", tags)
            self.assertIn("pkg", tags)
            self.assertNotIn("dev", tags)

    @patch("dbuild.config.yaml", None)
    def test_no_yaml_module_warns(self):
        """When PyYAML is missing, falls back to auto-detect with warning."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            config_dir = base / ".daemonless"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text("build: {}\n")
            cfg = load(base)
            self.assertEqual(len(cfg.variants), 1)

    def test_full_image_property(self):
        cfg = Config(image="radarr", registry="ghcr.io/daemonless")
        self.assertEqual(cfg.full_image, "ghcr.io/daemonless/radarr")


class TestGitRemoteOrg(unittest.TestCase):
    """Tests for _git_remote_org()."""

    @patch("subprocess.run")
    def test_https_url(self, mock_run):
        mock_run.return_value = unittest.mock.MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n"
        )
        self.assertEqual(_git_remote_org(), "myorg")

    @patch("subprocess.run")
    def test_ssh_url(self, mock_run):
        mock_run.return_value = unittest.mock.MagicMock(
            returncode=0, stdout="git@github.com:myorg/myrepo.git\n"
        )
        self.assertEqual(_git_remote_org(), "myorg")

    @patch("subprocess.run")
    def test_no_remote(self, mock_run):
        mock_run.return_value = unittest.mock.MagicMock(
            returncode=1, stdout=""
        )
        self.assertIsNone(_git_remote_org())

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_no_git(self, _mock_run):
        self.assertIsNone(_git_remote_org())


class TestLoadGlobalConfig(unittest.TestCase):
    """Tests for _load_global_config()."""

    def test_missing_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            result = _load_global_config(Path(d) / "nonexistent.yaml")
            self.assertEqual(result, {})

    def test_reads_yaml(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "daemonless.yaml"
            p.write_text(
                "build:\n"
                "  variants:\n"
                "    - tag: pkg-latest\n"
                "      containerfile: Containerfile.pkg\n"
            )
            result = _load_global_config(p)
            self.assertIn("build", result)
            self.assertEqual(len(result["build"]["variants"]), 1)

    @patch("dbuild.config.yaml", None)
    def test_no_yaml_module(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "daemonless.yaml"
            p.write_text("build: {}\n")
            result = _load_global_config(p)
            self.assertEqual(result, {})


class TestGlobalExtraVariants(unittest.TestCase):
    """Tests for _global_extra_variants()."""

    def test_filtered_by_containerfile(self):
        """Global variant skipped if its Containerfile doesn't exist."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            # No Containerfile.pkg → pkg-latest should be filtered out
            global_data = {
                "build": {
                    "variants": [
                        {"tag": "pkg-latest", "containerfile": "Containerfile.pkg",
                         "args": {"BASE_VERSION": "15-latest"}},
                    ]
                }
            }
            result = _global_extra_variants(base, global_data)
            self.assertEqual(result, [])

    def test_variant_included_when_containerfile_exists(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile.pkg").touch()
            global_data = {
                "build": {
                    "variants": [
                        {"tag": "pkg-latest", "containerfile": "Containerfile.pkg",
                         "args": {"BASE_VERSION": "15-latest"}},
                    ]
                }
            }
            result = _global_extra_variants(base, global_data)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].tag, "pkg-latest")
            self.assertEqual(result[0].args, {"BASE_VERSION": "15-latest"})

    def test_empty_global_data(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            result = _global_extra_variants(Path(d), {})
            self.assertEqual(result, [])


class TestLoadWithGlobalConfig(unittest.TestCase):
    """Tests for load() with global config integration."""

    @patch("dbuild.config._git_remote_org", return_value="myorg")
    def test_global_appended_to_auto_detect(self, _mock_org):
        """Global pkg-latest is appended when Containerfile.pkg exists."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            (base / "Containerfile.pkg").touch()
            global_path = base / "global.yaml"
            global_path.write_text(
                "build:\n"
                "  variants:\n"
                "    - tag: pkg-latest\n"
                "      containerfile: Containerfile.pkg\n"
                "      args:\n"
                "        BASE_VERSION: '15-latest'\n"
            )
            with patch("dbuild.config._GLOBAL_CONFIG_PATH", global_path):
                cfg = load(base)
            tags = [v.tag for v in cfg.variants]
            self.assertEqual(tags, ["latest", "pkg", "pkg-latest"])
            # Verify pkg-latest has the right args
            pkg_latest = [v for v in cfg.variants if v.tag == "pkg-latest"][0]
            self.assertEqual(pkg_latest.args, {"BASE_VERSION": "15-latest"})

    @patch("dbuild.config._git_remote_org", return_value="myorg")
    def test_local_variants_override_global(self, _mock_org):
        """When local config defines build.variants, global is not appended."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            (base / "Containerfile.pkg").touch()
            config_dir = base / ".daemonless"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text(
                "build:\n"
                "  variants:\n"
                "    - tag: latest\n"
                "      containerfile: Containerfile\n"
                "      default: true\n"
            )
            global_path = base / "global.yaml"
            global_path.write_text(
                "build:\n"
                "  variants:\n"
                "    - tag: pkg-latest\n"
                "      containerfile: Containerfile.pkg\n"
            )
            with patch("dbuild.config._GLOBAL_CONFIG_PATH", global_path):
                cfg = load(base)
            tags = [v.tag for v in cfg.variants]
            self.assertEqual(tags, ["latest"])

    @patch("dbuild.config._git_remote_org", return_value="myorg")
    def test_global_filtered_by_containerfile(self, _mock_org):
        """Global variant skipped when its Containerfile doesn't exist in repo."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            # No Containerfile.pkg
            global_path = base / "global.yaml"
            global_path.write_text(
                "build:\n"
                "  variants:\n"
                "    - tag: pkg-latest\n"
                "      containerfile: Containerfile.pkg\n"
            )
            with patch("dbuild.config._GLOBAL_CONFIG_PATH", global_path):
                cfg = load(base)
            tags = [v.tag for v in cfg.variants]
            self.assertEqual(tags, ["latest"])

    @patch("dbuild.config._git_remote_org", return_value="myorg")
    def test_no_global_config(self, _mock_org):
        """Works fine without a global config file."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            (base / "Containerfile.pkg").touch()
            with patch("dbuild.config._GLOBAL_CONFIG_PATH",
                        Path(d) / "nonexistent.yaml"):
                cfg = load(base)
            tags = [v.tag for v in cfg.variants]
            self.assertEqual(tags, ["latest", "pkg"])

    @patch("dbuild.config._git_remote_org", return_value="myorg")
    def test_global_duplicate_tag_skipped(self, _mock_org):
        """Global variant with same tag as auto-detected is skipped."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            (base / "Containerfile.pkg").touch()
            global_path = base / "global.yaml"
            global_path.write_text(
                "build:\n"
                "  variants:\n"
                "    - tag: pkg\n"
                "      containerfile: Containerfile.pkg\n"
                "      args:\n"
                "        EXTRA: 'yes'\n"
            )
            with patch("dbuild.config._GLOBAL_CONFIG_PATH", global_path):
                cfg = load(base)
            tags = [v.tag for v in cfg.variants]
            self.assertEqual(tags, ["latest", "pkg"])
            # Auto-detected pkg should NOT have the global args
            pkg = [v for v in cfg.variants if v.tag == "pkg"][0]
            self.assertEqual(pkg.args, {})

    @patch("dbuild.config._git_remote_org", return_value="myorg")
    def test_global_architectures(self, _mock_org):
        """Global architectures used when local doesn't set them."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            global_path = base / "global.yaml"
            global_path.write_text(
                "build:\n"
                "  architectures: [amd64, aarch64]\n"
            )
            with patch("dbuild.config._GLOBAL_CONFIG_PATH", global_path):
                cfg = load(base)
            self.assertEqual(cfg.architectures, ["amd64", "aarch64"])

    @patch("dbuild.config._git_remote_org", return_value="myorg")
    def test_local_architectures_override_global(self, _mock_org):
        """Local architectures take priority over global."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "Containerfile").touch()
            (base / ".dbuild.yaml").write_text(
                "build:\n"
                "  architectures: [amd64]\n"
            )
            global_path = base / "global.yaml"
            global_path.write_text(
                "build:\n"
                "  architectures: [amd64, aarch64]\n"
            )
            with patch("dbuild.config._GLOBAL_CONFIG_PATH", global_path):
                cfg = load(base)
            self.assertEqual(cfg.architectures, ["amd64"])


if __name__ == "__main__":
    unittest.main()
