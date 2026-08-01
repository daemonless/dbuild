"""Unit tests for dbuild.sbom."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from dbuild.config import Variant
from dbuild.sbom import (
    _TRIVY_PKG_TYPES,
    _detect_source,
    _extract_trivy_packages,
    _generate_cyclonedx,
    _generate_spdx,
    _parse_formats,
    _parse_freebsd_query,
)

_SCHEMA_DIR = Path(__file__).parent / "schemas"


def _sample_sbom() -> dict:
    """A representative internal SBOM dict as _generate_sbom would build."""
    return {
        "image": "radarr",
        "tag": "15",
        "arch": "amd64",
        "app_version": "5.14.0",
        "source": "pkg",
        "generated": "2026-07-31T12:00:00Z",
        "packages": {
            "freebsd": [
                {"name": "pkgconf", "version": "2.4.3_1,1",
                 "licenses": ["ISCL"]},
                {"name": "curl", "version": "8.9.0"},  # no license reported
            ],
            "node": [
                {"name": "express", "version": "4.18.0",
                 "purl": "pkg:npm/express@4.18.0"},
            ],
            "go": [
                {"name": "stdlib", "version": "v1.25.11",
                 "purl": "pkg:golang/stdlib@v1.25.11"},
            ],
            "dotnet": [], "java": [], "php": [], "python": [],
            "ruby": [], "rust": [],
        },
        "summary": {"freebsd": 2, "node": 1, "go": 1, "total": 4},
    }


class TestDetectSource(unittest.TestCase):
    """Tests for _detect_source()."""

    def test_default_containerfile(self):
        v = Variant(tag="latest", containerfile="Containerfile")
        self.assertEqual(_detect_source(v), "upstream")

    def test_containerfile_with_suffix(self):
        v = Variant(tag="pkg", containerfile="Containerfile.pkg")
        self.assertEqual(_detect_source(v), "pkg")

    def test_custom_suffix(self):
        v = Variant(tag="dev", containerfile="Containerfile.dev")
        self.assertEqual(_detect_source(v), "dev")

    def test_multi_dot_suffix(self):
        v = Variant(tag="foo", containerfile="Containerfile.foo.bar")
        self.assertEqual(_detect_source(v), "foo.bar")


class TestExtractTrivyPackages(unittest.TestCase):
    """Tests for _extract_trivy_packages()."""

    def test_empty_data(self):
        result = _extract_trivy_packages({})
        for category in _TRIVY_PKG_TYPES:
            self.assertEqual(result[category], [])

    def test_no_results(self):
        result = _extract_trivy_packages({"Results": []})
        for category in _TRIVY_PKG_TYPES:
            self.assertEqual(result[category], [])

    def test_node_packages(self):
        trivy_data = {
            "Results": [
                {
                    "Type": "node-pkg",
                    "Packages": [
                        {"Name": "express", "Version": "4.18.0"},
                        {"Name": "lodash", "Version": "4.17.21"},
                    ],
                }
            ]
        }
        result = _extract_trivy_packages(trivy_data)
        self.assertEqual(len(result["node"]), 2)
        names = [p["name"] for p in result["node"]]
        self.assertIn("express", names)
        self.assertIn("lodash", names)

    def test_dotnet_packages(self):
        trivy_data = {
            "Results": [
                {
                    "Type": "dotnet-core",
                    "Packages": [
                        {"Name": "Newtonsoft.Json", "Version": "13.0.1"},
                    ],
                }
            ]
        }
        result = _extract_trivy_packages(trivy_data)
        self.assertEqual(len(result["dotnet"]), 1)
        self.assertEqual(result["dotnet"][0]["name"], "Newtonsoft.Json")

    def test_purl_carried_through(self):
        trivy_data = {
            "Results": [
                {
                    "Type": "gobinary",
                    "Packages": [
                        {
                            "Name": "stdlib",
                            "Version": "v1.25.11",
                            "PkgIdentifier": {"PURL": "pkg:golang/stdlib@v1.25.11"},
                        },
                    ],
                }
            ]
        }
        result = _extract_trivy_packages(trivy_data)
        self.assertEqual(result["go"][0]["purl"], "pkg:golang/stdlib@v1.25.11")

    def test_purl_absent_defaults_empty(self):
        trivy_data = {
            "Results": [
                {
                    "Type": "node-pkg",
                    "Packages": [{"Name": "express", "Version": "4.18.0"}],
                }
            ]
        }
        result = _extract_trivy_packages(trivy_data)
        self.assertEqual(result["node"][0]["purl"], "")

    def test_dedup_within_category(self):
        trivy_data = {
            "Results": [
                {
                    "Type": "gobinary",
                    "Packages": [
                        {"Name": "github.com/foo/bar", "Version": "1.0"},
                    ],
                },
                {
                    "Type": "gomod",
                    "Packages": [
                        {"Name": "github.com/foo/bar", "Version": "1.0"},
                    ],
                },
            ]
        }
        result = _extract_trivy_packages(trivy_data)
        self.assertEqual(len(result["go"]), 1)

    def test_multiple_categories(self):
        trivy_data = {
            "Results": [
                {
                    "Type": "node-pkg",
                    "Packages": [{"Name": "react", "Version": "18.0"}],
                },
                {
                    "Type": "python-pkg",
                    "Packages": [{"Name": "flask", "Version": "2.0"}],
                },
            ]
        }
        result = _extract_trivy_packages(trivy_data)
        self.assertEqual(len(result["node"]), 1)
        self.assertEqual(len(result["python"]), 1)

    def test_unknown_type_ignored(self):
        trivy_data = {
            "Results": [
                {
                    "Type": "unknown-scanner",
                    "Packages": [{"Name": "foo", "Version": "1.0"}],
                }
            ]
        }
        result = _extract_trivy_packages(trivy_data)
        total = sum(len(pkgs) for pkgs in result.values())
        self.assertEqual(total, 0)

    def test_all_categories_present(self):
        result = _extract_trivy_packages({})
        for category in _TRIVY_PKG_TYPES:
            self.assertIn(category, result)


class TestParseFreebsdQuery(unittest.TestCase):
    """Tests for _parse_freebsd_query() -- output shape verified on saturn."""

    def test_multi_license_comma_space(self):
        # Exactly what `pkg query "%n\t%v\t%L" zstd` returned on saturn.
        out = "zstd\t1.5.7_2\tGPLv2, BSD3CLAUSE\n"
        self.assertEqual(
            _parse_freebsd_query(out),
            [{"name": "zstd", "version": "1.5.7_2",
              "licenses": ["GPLv2", "BSD3CLAUSE"]}],
        )

    def test_single_license(self):
        out = "pkgconf\t2.4.3_1,1\tISCL\n"
        self.assertEqual(
            _parse_freebsd_query(out)[0]["licenses"], ["ISCL"]
        )

    def test_no_license_field_omitted(self):
        out = "curl\t8.9.0\t\n"
        entry = _parse_freebsd_query(out)[0]
        self.assertEqual(entry, {"name": "curl", "version": "8.9.0"})
        self.assertNotIn("licenses", entry)

    def test_version_revision_preserved(self):
        # The _N port revision must never be stripped (bare %v).
        out = "zstd\t1.5.7_2\tGPLv2\n"
        self.assertEqual(_parse_freebsd_query(out)[0]["version"], "1.5.7_2")

    def test_control_chars_stripped_tabs_kept(self):
        out = "a\x02\t1.0\tMIT\n"  # STX control char in name
        entry = _parse_freebsd_query(out)[0]
        self.assertEqual(entry["name"], "a")
        self.assertEqual(entry["licenses"], ["MIT"])

    def test_blank_lines_skipped(self):
        out = "\nfoo\t1.0\tMIT\n\n"
        self.assertEqual(len(_parse_freebsd_query(out)), 1)


class TestGenerateCycloneDX(unittest.TestCase):
    """Tests for _generate_cyclonedx()."""

    def test_validates_against_cyclonedx_1_6_schema(self):
        """The real correctness gate: output must be a valid CycloneDX 1.6 doc."""
        try:
            import jsonschema
            from referencing import Registry, Resource
        except ImportError:
            self.skipTest("jsonschema/referencing not installed")

        doc = _generate_cyclonedx(_sample_sbom())

        # CycloneDX 1.6 $refs spdx.schema.json and jsf-0.82.schema.json.
        registry = Registry()
        for name in ("spdx.schema.json", "jsf-0.82.schema.json",
                     "bom-1.6.schema.json"):
            data = json.loads((_SCHEMA_DIR / name).read_text())
            registry = registry.with_resource(
                name, Resource.from_contents(data)
            )
        schema = json.loads((_SCHEMA_DIR / "bom-1.6.schema.json").read_text())
        jsonschema.Draft7Validator(schema, registry=registry).validate(doc)

    def test_required_top_level_fields(self):
        doc = _generate_cyclonedx(_sample_sbom())
        self.assertEqual(doc["bomFormat"], "CycloneDX")
        self.assertEqual(doc["specVersion"], "1.6")
        self.assertIsInstance(doc["version"], int)
        self.assertEqual(doc["metadata"]["component"]["name"], "radarr")

    def test_all_packages_present_as_components(self):
        doc = _generate_cyclonedx(_sample_sbom())
        # 2 freebsd + 1 node + 1 go = 4
        self.assertEqual(len(doc["components"]), 4)
        names = {c["name"] for c in doc["components"]}
        self.assertEqual(names, {"pkgconf", "curl", "express", "stdlib"})

    def test_trivy_purl_carried_freebsd_generic(self):
        doc = _generate_cyclonedx(_sample_sbom())
        by_name = {c["name"]: c for c in doc["components"]}
        self.assertEqual(by_name["express"]["purl"], "pkg:npm/express@4.18.0")
        self.assertTrue(
            by_name["pkgconf"]["purl"].startswith("pkg:generic/pkgconf@")
        )
        self.assertIn("os=freebsd", by_name["pkgconf"]["purl"])
        self.assertIn("arch=amd64", by_name["pkgconf"]["purl"])

    def test_bom_refs_unique(self):
        doc = _generate_cyclonedx(_sample_sbom())
        refs = [c["bom-ref"] for c in doc["components"]]
        self.assertEqual(len(refs), len(set(refs)))

    def test_bom_refs_unique_with_duplicate_names(self):
        data = _sample_sbom()
        # Same name/version with no purl in two categories -> ref collision risk.
        data["packages"]["python"] = [{"name": "dup", "version": "1.0", "purl": ""}]
        data["packages"]["ruby"] = [{"name": "dup", "version": "1.0", "purl": ""}]
        doc = _generate_cyclonedx(data)
        refs = [c["bom-ref"] for c in doc["components"]]
        self.assertEqual(len(refs), len(set(refs)))

    def test_cve_caveat_in_metadata(self):
        doc = _generate_cyclonedx(_sample_sbom())
        props = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
        self.assertIn("dbuild:freebsd-cve-caveat", props)

    def test_license_as_free_text_name(self):
        doc = _generate_cyclonedx(_sample_sbom())
        by_name = {c["name"]: c for c in doc["components"]}
        self.assertEqual(
            by_name["pkgconf"]["licenses"], [{"license": {"name": "ISCL"}}]
        )
        # Package with no reported license carries no licenses key.
        self.assertNotIn("licenses", by_name["curl"])


class TestGenerateSpdx(unittest.TestCase):
    """Tests for _generate_spdx()."""

    def test_validates_against_spdx_2_3_schema(self):
        """Correctness gate: output must be a valid SPDX 2.3 document."""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")

        doc = _generate_spdx(_sample_sbom())
        schema = json.loads(
            (_SCHEMA_DIR / "spdx-2.3.schema.json").read_text()
        )
        jsonschema.Draft7Validator(schema).validate(doc)

    def test_required_top_level_fields(self):
        doc = _generate_spdx(_sample_sbom())
        self.assertEqual(doc["spdxVersion"], "SPDX-2.3")
        self.assertEqual(doc["dataLicense"], "CC0-1.0")
        self.assertEqual(doc["SPDXID"], "SPDXRef-DOCUMENT")
        self.assertEqual(doc["creationInfo"]["creators"][0][:6], "Tool: ")

    def test_image_is_described(self):
        doc = _generate_spdx(_sample_sbom())
        describes = [
            r for r in doc["relationships"]
            if r["relationshipType"] == "DESCRIBES"
        ]
        self.assertEqual(len(describes), 1)
        self.assertEqual(describes[0]["relatedSpdxElement"], "SPDXRef-Package-image")

    def test_all_packages_present(self):
        doc = _generate_spdx(_sample_sbom())
        # 1 root image + 2 freebsd + 1 node + 1 go = 5
        self.assertEqual(len(doc["packages"]), 5)

    def test_purls_in_external_refs(self):
        doc = _generate_spdx(_sample_sbom())
        locators = [
            ref["referenceLocator"]
            for p in doc["packages"]
            for ref in p.get("externalRefs", [])
        ]
        self.assertIn("pkg:npm/express@4.18.0", locators)
        self.assertTrue(
            any(loc.startswith("pkg:generic/pkgconf@") for loc in locators)
        )

    def test_spdxids_unique(self):
        doc = _generate_spdx(_sample_sbom())
        ids = [p["SPDXID"] for p in doc["packages"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_files_analyzed_false_on_every_package(self):
        # Semantic SPDX rule (not caught by JSON schema): omitting
        # filesAnalyzed defaults it to true, which then *requires* a
        # packageVerificationCode per package. We inventory packages only,
        # so every package must set filesAnalyzed: false.
        doc = _generate_spdx(_sample_sbom())
        self.assertTrue(
            all(p.get("filesAnalyzed") is False for p in doc["packages"])
        )

    def test_cve_caveat_in_comment(self):
        doc = _generate_spdx(_sample_sbom())
        self.assertIn("will not auto-match CVEs", doc["comment"])

    def test_license_in_license_comments_not_declared(self):
        doc = _generate_spdx(_sample_sbom())
        by_name = {p["name"]: p for p in doc["packages"]}
        self.assertIn("ISCL", by_name["pkgconf"]["licenseComments"])
        # Never a (guessed) SPDX expression in licenseDeclared.
        self.assertNotIn("licenseDeclared", by_name["pkgconf"])
        self.assertNotIn("licenseComments", by_name["curl"])


class TestEmbedSbom(unittest.TestCase):
    """Tests for _embed_sbom() (buildah bake step)."""

    def test_copies_each_file_and_commits(self):
        from unittest import mock

        from dbuild import sbom as sbom_mod
        files = [Path("out/radarr-pkg-cyclonedx.json"),
                 Path("out/radarr-pkg-spdx.json")]
        with mock.patch.object(sbom_mod, "podman") as pod:
            pod.bah_from.return_value = "ctr123"
            sbom_mod._embed_sbom("img:build-pkg", files)

        pod.bah_from.assert_called_once_with("img:build-pkg")
        self.assertEqual(pod.bah_copy.call_count, 2)
        pod.bah_copy.assert_any_call(
            "ctr123", "out/radarr-pkg-spdx.json",
            "/usr/share/sbom/radarr-pkg-spdx.json",
        )
        pod.bah_commit.assert_called_once_with("ctr123", "img:build-pkg")
        pod.bah_rm.assert_called_once_with("ctr123")

    def test_no_files_is_noop(self):
        from unittest import mock

        from dbuild import sbom as sbom_mod
        with mock.patch.object(sbom_mod, "podman") as pod:
            sbom_mod._embed_sbom("img:build-pkg", [])
        pod.bah_from.assert_not_called()

    def test_container_removed_on_error(self):
        from unittest import mock

        from dbuild import sbom as sbom_mod
        with mock.patch.object(sbom_mod, "podman") as pod:
            pod.bah_from.return_value = "ctr123"
            pod.bah_copy.side_effect = RuntimeError("boom")
            with self.assertRaises(RuntimeError):
                sbom_mod._embed_sbom("img:build-pkg", [Path("x-cyclonedx.json")])
        pod.bah_rm.assert_called_once_with("ctr123")


class TestParseFormats(unittest.TestCase):
    """Tests for _parse_formats()."""

    def test_default_when_empty(self):
        self.assertEqual(_parse_formats(None), ["daemonless", "cyclonedx"])
        self.assertEqual(_parse_formats(""), ["daemonless", "cyclonedx"])

    def test_single(self):
        self.assertEqual(_parse_formats("cyclonedx"), ["cyclonedx"])

    def test_all_expands(self):
        self.assertEqual(
            _parse_formats("all"), ["daemonless", "cyclonedx", "spdx"]
        )

    def test_spdx_valid(self):
        self.assertEqual(_parse_formats("spdx"), ["spdx"])

    def test_order_normalized(self):
        # Output order follows _KNOWN_FORMATS regardless of input order.
        self.assertEqual(
            _parse_formats("cyclonedx,daemonless"), ["daemonless", "cyclonedx"]
        )

    def test_dedup_and_whitespace(self):
        self.assertEqual(
            _parse_formats(" cyclonedx , cyclonedx "), ["cyclonedx"]
        )

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            _parse_formats("bogus")


if __name__ == "__main__":
    unittest.main()
