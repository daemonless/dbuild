"""Abstract container/jail backend for CIT.

Adding a new backend (e.g. bastille):
  1. Subclass ``ContainerBackend``
  2. Implement all abstract methods
  3. Register in ``get_backend()``
"""

from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod

from dbuild import log, podman


class ContainerBackend(ABC):
    """Unified interface for starting/stopping containers or jails."""

    #: Short name used in CLI choices and log output
    name: str

    @classmethod
    @abstractmethod
    def available(cls) -> bool:
        """Return True if the required system tool is installed."""

    @abstractmethod
    def start(
        self,
        cname: str,
        image_ref: str,
        *,
        annotations: dict[str, str],
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
    ) -> None:
        """Start the container/jail.  Raises on failure.

        *env* and *volumes*, when supported, set environment variables and
        bind/named-volume mounts.  Backends that cannot honour them must
        raise rather than silently ignore.
        """

    @abstractmethod
    def get_ip(self, cname: str) -> str:
        """Return the IP to connect to.  Empty string if unavailable."""

    @abstractmethod
    def logs(self, cname: str, *, quiet: bool = False) -> str:
        """Return recent log output."""

    @abstractmethod
    def exec_in(self, cname: str, cmd: list[str]) -> subprocess.CompletedProcess:
        """Run *cmd* inside the container/jail."""

    @abstractmethod
    def running(self, cname: str, *, quiet: bool = False) -> bool:
        """Return True if the container/jail is currently running."""

    @abstractmethod
    def stop(self, cname: str) -> None:
        """Stop and remove the container/jail."""


class PodmanBackend(ContainerBackend):
    name = "podman"

    @classmethod
    def available(cls) -> bool:
        return shutil.which("podman") is not None

    def start(
        self,
        cname: str,
        image_ref: str,
        *,
        annotations: dict[str, str],
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
    ) -> None:
        cid = podman.run_detached(
            image_ref, name=cname, annotations=annotations, env=env, volumes=volumes,
        )
        log.info(f"Started: {cid}")

    def get_ip(self, cname: str) -> str:
        return podman.inspect_ip(cname) or ""

    def logs(self, cname: str, *, quiet: bool = False) -> str:
        return podman.logs(cname, quiet=quiet)

    def exec_in(self, cname: str, cmd: list[str]) -> subprocess.CompletedProcess:
        return podman.exec_in(cname, cmd)

    def running(self, cname: str, *, quiet: bool = False) -> bool:
        return podman.container_running(cname, quiet=quiet)

    def stop(self, cname: str) -> None:
        podman.stop(cname)
        podman.rm(cname)


class AppJailBackend(ContainerBackend):
    name = "appjail"

    @classmethod
    def available(cls) -> bool:
        return shutil.which("appjail") is not None

    def start(
        self,
        cname: str,
        image_ref: str,
        *,
        annotations: dict[str, str],
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
    ) -> None:
        from dbuild import appjail as aj
        if env or volumes:
            raise NotImplementedError(
                "appjail backend does not support env/volumes "
                "(used by the --puid test); run --puid with --backend podman",
            )
        jail_allow = [
            k.replace("org.freebsd.jail.", "")
            for k in annotations
            if k.startswith("org.freebsd.jail.")
        ]
        log.info(f"AppJail jail: {cname}")
        aj.oci_run(cname, image_ref, allow=jail_allow or None)

    def get_ip(self, cname: str) -> str:
        from dbuild import appjail as aj
        # OCI jails share the host network stack
        return aj.get_ip(cname) or "127.0.0.1"

    def logs(self, cname: str, *, quiet: bool = False) -> str:
        from dbuild import appjail as aj
        return aj.logs(cname, quiet=quiet)

    def exec_in(self, cname: str, cmd: list[str]) -> subprocess.CompletedProcess:
        from dbuild import appjail as aj
        return aj.exec_in(cname, cmd)

    def running(self, cname: str, *, quiet: bool = False) -> bool:
        from dbuild import appjail as aj
        return aj.jail_running(cname)

    def stop(self, cname: str) -> None:
        from dbuild import appjail as aj
        aj.jail_stop(cname)
        aj.jail_destroy(cname)


#: Ordered list of all available backends (podman first = default)
_BACKENDS: list[type[ContainerBackend]] = [PodmanBackend, AppJailBackend]


def get_backend(name: str) -> ContainerBackend:
    """Return a backend instance by name.  Raises ``ValueError`` if unknown."""
    for cls in _BACKENDS:
        if cls.name == name:
            return cls()
    raise ValueError(f"Unknown backend: {name!r}")


def available_backends() -> list[str]:
    """Return names of all installed backends."""
    return [cls.name for cls in _BACKENDS if cls.available()]
