"""PEP 517 build hooks for dbuild.

FreeBSD ports install the checked-out docs/dbuild.1 file after the wheel is
built. Regenerate it during the PEP 517 build so custom PREFIX values are
reflected without requiring a port patch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from setuptools import build_meta as _setuptools


def _generate_manpage() -> None:
    from dbuild.cli import _make_parser
    from dbuild.docs import generate_manpage

    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "dbuild.1").write_text(
        generate_manpage(_make_parser()) + "\n",
        encoding="utf-8",
    )


def get_requires_for_build_wheel(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    return _setuptools.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(
    config_settings: dict[str, Any] | None = None,
) -> list[str]:
    return _setuptools.get_requires_for_build_sdist(config_settings)


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    return _setuptools.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _generate_manpage()
    return _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    _generate_manpage()
    return _setuptools.build_sdist(sdist_directory, config_settings)


if hasattr(_setuptools, "build_editable"):

    def get_requires_for_build_editable(
        config_settings: dict[str, Any] | None = None,
    ) -> list[str]:
        return _setuptools.get_requires_for_build_editable(config_settings)


    def prepare_metadata_for_build_editable(
        metadata_directory: str,
        config_settings: dict[str, Any] | None = None,
    ) -> str:
        return _setuptools.prepare_metadata_for_build_editable(
            metadata_directory,
            config_settings,
        )


    def build_editable(
        wheel_directory: str,
        config_settings: dict[str, Any] | None = None,
        metadata_directory: str | None = None,
    ) -> str:
        _generate_manpage()
        return _setuptools.build_editable(
            wheel_directory,
            config_settings,
            metadata_directory,
        )
