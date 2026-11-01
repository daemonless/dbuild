"""Abstract container/jail backend for CIT.

Adding a new backend (e.g. bastille):
  1. Subclass ``ContainerBackend``
  2. Implement all abstract methods
  3. Register in ``get_backend()``
"""

from __future__ import annotations

import os
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

    def labels(self, image_ref: str) -> dict[str, str]:
        """The image's OCI labels (port, healthcheck, jail annotations)."""
        return podman.inspect_labels(image_ref)

    def run_oneshot(
        self,
        image_ref: str,
        command: list[str] | None,
        *,
        annotations: dict[str, str],
    ) -> tuple[int, str]:
        """Run the image to completion (command mode): ``(rc, output)``."""
        return podman.run_oneshot(image_ref, command, annotations=annotations)


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
        # Only allow.* annotations map to appjail template params. Value-typed
        # params (children.max, enforce_statfs, devfs_ruleset, name, ...) have
        # no equivalent here, and appjail-config hard-errors on unknown params
        # (unlike ocijail, which warns and ignores).
        jail_allow = [
            k.removeprefix("org.freebsd.jail.")
            for k, v in annotations.items()
            if k.startswith("org.freebsd.jail.allow.")
            and str(v).lower() in ("true", "1", "required")
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


class KubernetesBackend(ContainerBackend):
    """One pod per test on the cluster this node belongs to (``kubectl``).

    Tests the registry's image: the node pulls it, nothing is built here.
    """

    name = "k8s"

    def __init__(self) -> None:
        self._stacks: set[str] = set()  # cnames started as stacks (one pod, many containers)
        # cnames started as a pod per service: cname -> (main pod, host port -> container port)
        self._split: dict[str, tuple[str, dict[int, int]]] = {}
        self._split_ports: dict[str, tuple[dict[str, str], dict[str, dict[int, int]]]] = {}

    @classmethod
    def available(cls) -> bool:
        from dbuild import kube
        return kube.available()

    def start(
        self,
        cname: str,
        image_ref: str,
        *,
        annotations: dict[str, str],
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
    ) -> None:
        from dbuild import kube
        if volumes:
            raise NotImplementedError(
                "k8s backend does not support volumes "
                "(used by the --puid test); run --puid with --backend podman",
            )
        kube.ensure_namespace()
        name = kube.pod_name(cname)
        kube.delete(name)
        log.info(f"Pod: {kube.NAMESPACE}/{name}")
        kube.apply(kube.pod_spec(name, image_ref, annotations=annotations, env=env))
        kube.wait_started(name)

    def get_ip(self, cname: str) -> str:
        from dbuild import kube
        if cname in self._split:
            return kube.pod_ip(self._split[cname][0])
        return kube.pod_ip(kube.pod_name(cname))

    def logs(self, cname: str, *, quiet: bool = False) -> str:
        from dbuild import kube
        if cname in self._split:
            return kube.stack_logs_all(kube.pod_name(cname), quiet=quiet)
        if cname in self._stacks:
            return kube.stack_logs(kube.pod_name(cname), quiet=quiet)
        return kube.logs(kube.pod_name(cname), quiet=quiet)

    def exec_in(self, cname: str, cmd: list[str]) -> subprocess.CompletedProcess:
        from dbuild import kube
        if cname in self._split:
            return kube.exec_in(self._split[cname][0], cmd, first=True)
        return kube.exec_in(kube.pod_name(cname), cmd, first=cname in self._stacks)

    def running(self, cname: str, *, quiet: bool = False) -> bool:
        from dbuild import kube
        if cname in self._split:
            return kube.stack_running_all(kube.pod_name(cname))
        if cname in self._stacks:
            return kube.stack_running(kube.pod_name(cname))
        return kube.running(kube.pod_name(cname))

    def stop(self, cname: str) -> None:
        from dbuild import kube
        if cname in self._split:
            kube.delete_stack(kube.pod_name(cname))
            del self._split[cname]
            return
        kube.delete(kube.pod_name(cname))
        self._stacks.discard(cname)

    def stack_port(self, cname: str, port: int | None) -> int | None:
        """The port to probe on get_ip's pod: for a pod-per-service stack the
        config's port is a published (host) port, and the probe goes to the
        pod that publishes it, at its container port."""
        if port is None or cname not in self._split:
            return port
        return self._split[cname][1].get(port, port)

    def labels(self, image_ref: str) -> dict[str, str]:
        from dbuild import kube
        return kube.image_labels(image_ref)

    def start_stack(
        self, cname: str, repo_dir: str, compose_file: str,
        image_map: dict[str, str] | None = None,
    ) -> None:
        """A compose stack as one pod: a container per service, the stack's
        ``.env`` (else ``example.env``) for its variables, hostPath data dirs
        under the node's scratch root. *image_map* maps the compose file's
        local ``:build`` ref to the registry image under test."""
        import yaml

        from dbuild import kube
        with open(compose_file) as fh:
            compose = yaml.safe_load(fh) or {}
        env, env_files = kube.stack_env(repo_dir)
        name = kube.pod_name(cname)
        kube.ensure_namespace()
        kube.delete(name)
        kube.delete_stack(name)
        if kube.shares_host_network(compose):
            spec, images = kube.stack_spec(
                name, compose, env=env, env_files=env_files, data_root=f"/var/tmp/cit/{name}",
                image_map=image_map,
            )
            for svc, image in images.items():
                log.info(f"  {svc}: {image}")
            log.info(f"Pod: {kube.NAMESPACE}/{name} ({len(images)} containers, one network)")
            kube.apply(spec)
            kube.wait_started(name)
            self._stacks.add(cname)
            return
        specs, images, ports = kube.stack_specs_per_service(
            name, compose, env=env, env_files=env_files, data_root=f"/var/tmp/cit/{name}",
            image_map=image_map,
        )
        for svc, image in images.items():
            pub = ", ".join(f"{h}->{c}" for h, c in ports[svc].items()) or "no published port"
            log.info(f"  {svc}: {image} ({pub})")
        log.info(f"Pods: {kube.NAMESPACE}/{name}-* ({len(images)} pods, a Service per name)")
        kube.apply_all(specs)
        for pod in [sp["metadata"]["name"] for sp in specs if sp["kind"] == "Pod"]:
            kube.wait_started(pod)
        # The pod the checks talk to: the one publishing the config's port
        # (resolved in stack_port); until a port is asked for, the first.
        main = kube.pod_name(f"{name}-{next(iter(images))}")
        by_host: dict[int, int] = {}
        self._split[cname] = (main, by_host)
        self._split_ports = {cname: (images, ports)}

    def choose_main(self, cname: str, port: int | None) -> None:
        """Pick the pod that publishes *port* as the one the checks probe."""
        from dbuild import kube
        if cname not in self._split or port is None:
            return
        images, ports = self._split_ports.get(cname, ({}, {}))
        name = kube.pod_name(cname)
        for svc, mapping in ports.items():
            if port in mapping:
                self._split[cname] = (kube.pod_name(f"{name}-{svc}"), dict(mapping))
                log.info(f"Probing {svc} (publishes {port} -> {mapping[port]})")
                return
        log.warn(f"No service publishes port {port}; probing {next(iter(images), '?')}")

    def run_oneshot(
        self,
        image_ref: str,
        command: list[str] | None,
        *,
        annotations: dict[str, str],
    ) -> tuple[int, str]:
        from dbuild import kube
        kube.ensure_namespace()
        name = kube.pod_name(f"cit-{os.getpid()}-oneshot")
        kube.delete(name)
        kube.apply(kube.pod_spec(name, image_ref, annotations=annotations, args=command))
        try:
            kube.wait_started(name)
            rc = kube.wait_finished(name, timeout=600)
            output = kube.logs(name, quiet=True)
            if rc is None:
                return 124, output + "\n(still running after 600s)"
            return rc, output
        finally:
            kube.delete(name)


#: Ordered list of all available backends (podman first = default)
_BACKENDS: list[type[ContainerBackend]] = [PodmanBackend, AppJailBackend, KubernetesBackend]


def get_backend(name: str) -> ContainerBackend:
    """Return a backend instance by name.  Raises ``ValueError`` if unknown."""
    for cls in _BACKENDS:
        if cls.name == name:
            return cls()
    raise ValueError(f"Unknown backend: {name!r}")


def available_backends() -> list[str]:
    """Return names of all installed backends."""
    return [cls.name for cls in _BACKENDS if cls.available()]
