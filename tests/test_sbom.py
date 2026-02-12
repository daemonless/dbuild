"""Unit tests for dbuild.sbom."""

from __future__ import annotations

import unittest

from dbuild.config import Variant
from dbuild.sbom import _TRIVY_PKG_TYPES, _detect_source, _extract_trivy_packages


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


if __name__ == "__main__":
    unittest.main()
