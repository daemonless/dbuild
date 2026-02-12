"""Shared fixtures for dbuild tests."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from dbuild.config import Config, TestConfig, Variant


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory with a Containerfile and config."""
    (tmp_path / "Containerfile").write_text("FROM scratch\n")
    config_dir = tmp_path / ".daemonless"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "build:\n"
        "  variants:\n"
        "    - tag: latest\n"
        "      containerfile: Containerfile\n"
        "      default: true\n"
    )
    return tmp_path


def make_config(**kwargs) -> Config:
    """Factory for Config dataclass with sensible defaults."""
    defaults = {
        "image": "testapp",
        "registry": "ghcr.io/daemonless",
        "type": "app",
        "variants": [make_variant()],
        "test": None,
        "architectures": ["amd64"],
    }
    defaults.update(kwargs)
    return Config(**defaults)


def make_variant(**kwargs) -> Variant:
    """Factory for Variant dataclass with sensible defaults."""
    defaults = {
        "tag": "latest",
        "containerfile": "Containerfile",
        "args": {},
        "aliases": [],
        "auto_version": False,
        "default": True,
        "pkg_name": None,
    }
    defaults.update(kwargs)
    return Variant(**defaults)


def make_test_config(**kwargs) -> TestConfig:
    """Factory for TestConfig dataclass with sensible defaults."""
    defaults = {
        "mode": "",
        "port": None,
        "health": None,
        "wait": 120,
        "ready": None,
        "screenshot_wait": None,
        "screenshot_path": None,
        "https": False,
        "compose": False,
        "annotations": [],
    }
    defaults.update(kwargs)
    return TestConfig(**defaults)


def make_args(**kwargs) -> argparse.Namespace:
    """Factory for argparse.Namespace with common defaults."""
    defaults = {
        "verbose": False,
        "variant": None,
        "arch": None,
        "registry": None,
        "push": False,
        "command": "build",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)
