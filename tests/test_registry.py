"""Unit tests for dbuild.registry."""

from __future__ import annotations

import unittest

from dbuild.registry import for_url
from dbuild.registry.dockerhub import DockerHub
from dbuild.registry.generic import GenericRegistry
from dbuild.registry.ghcr import GHCR


class TestForUrl(unittest.TestCase):
    """Tests for registry.for_url() factory."""

    def test_ghcr(self):
        reg = for_url("ghcr.io/daemonless")
        self.assertIsInstance(reg, GHCR)

    def test_ghcr_with_token(self):
        reg = for_url("ghcr.io/daemonless", token="ghp_abc123")
        self.assertIsInstance(reg, GHCR)

    def test_dockerhub(self):
        reg = for_url("docker.io/library")
        self.assertIsInstance(reg, DockerHub)

    def test_dockerhub_registry_url(self):
        reg = for_url("registry-1.docker.io")
        self.assertIsInstance(reg, DockerHub)

    def test_generic(self):
        reg = for_url("my-registry.example.com")
        self.assertIsInstance(reg, GenericRegistry)

    def test_generic_not_ghcr_or_docker(self):
        reg = for_url("quay.io/myorg")
        self.assertIsInstance(reg, GenericRegistry)
        self.assertNotIsInstance(reg, GHCR)
        self.assertNotIsInstance(reg, DockerHub)


class TestGenericRegistry(unittest.TestCase):
    """Tests for GenericRegistry."""

    def test_registry_host_simple(self):
        reg = GenericRegistry("ghcr.io/daemonless")
        self.assertEqual(reg._registry_host(), "ghcr.io")

    def test_registry_host_with_https(self):
        reg = GenericRegistry("https://my-registry.example.com/org")
        self.assertEqual(reg._registry_host(), "my-registry.example.com")

    def test_registry_host_with_http(self):
        reg = GenericRegistry("http://localhost:5000/myorg")
        self.assertEqual(reg._registry_host(), "localhost:5000")

    def test_url_stored(self):
        reg = GenericRegistry("ghcr.io/daemonless", token="tok")
        self.assertEqual(reg.url, "ghcr.io/daemonless")
        self.assertEqual(reg.token, "tok")


if __name__ == "__main__":
    unittest.main()
