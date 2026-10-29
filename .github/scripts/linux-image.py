#!/usr/bin/env python3
"""Build, test and push an image's Linux half, for the build-linux job only.

Not part of dbuild's command line on purpose: Linux images are built in
this workflow and nowhere else (yet). Run from the image repo's root with
dbuild on PYTHONPATH:

    linux-image.py <arch> [--push]

Builds Containerfile.linux, stamps the version from /app/version as the
image's org.opencontainers.image.version label (as FreeBSD builds are),
checks it comes up the way .daemonless/config.yaml's cit section says, and
with --push pushes <registry>/<image>:<tag>-linux-<arch> (and to Docker Hub
when its secrets are set). dbuild manifest adds it to <tag> when its
version is the FreeBSD images'.
"""
from __future__ import annotations

import os
import re
import ssl
import subprocess
import sys
import time
import urllib.request

from dbuild import config as config_mod

LOCAL = "localhost/linux-image:build"


def sh(*cmd: str, check: bool = True, capture: bool = False) -> str:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, check=check, text=True, capture_output=capture)
    return (r.stdout or "").strip() if capture else ""


def default_tag(cfg) -> str:
    for v in cfg.variants:
        if v.default:
            return v.tag
    return cfg.variants[0].tag if cfg.variants else "latest"


def build() -> str:
    sh("podman", "build", "--pull=newer", "-f", "Containerfile.linux", "-t", LOCAL + "-raw", ".")
    version = sh("podman", "run", "--rm", "--entrypoint", "cat", LOCAL + "-raw", "/app/version",
                 capture=True)
    if not version:
        sys.exit("Containerfile.linux must write the app's version to /app/version")
    # The label the manifest compares with the FreeBSD images'.
    stamp = f'FROM {LOCAL}-raw\nLABEL org.opencontainers.image.version="{version}"\n'
    subprocess.run(["podman", "build", "-f", "-", "-t", LOCAL], input=stamp, text=True, check=True)
    print(f"version: {version}", flush=True)
    return version


def answers(url: str) -> bool:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, timeout=3, context=ctx) as r:
            return r.status < 400
    except Exception:
        return False


def test(cfg) -> None:
    t = cfg.test
    if t is None or not t.port:
        sys.exit("the Linux test needs cit.port in .daemonless/config.yaml")
    run = ["podman", "run", "-d", "--name", "linux-cit", "-p", f"127.0.0.1:18000:{t.port}"]
    if t.puid:
        run += ["-e", "PUID=1000", "-e", "PGID=1000"]
    sh(*run, LOCAL)
    scheme = "https" if t.https else "http"
    url = f"{scheme}://127.0.0.1:18000{t.health or '/'}"
    ready = re.compile(t.ready) if t.ready else None
    ok = False
    deadline = time.time() + (t.wait or 120)
    while time.time() < deadline:
        state = sh("podman", "inspect", "-f", "{{.State.Status}}", "linux-cit", capture=True)
        if state != "running":
            print(f"container {state}", flush=True)
            break
        logs = sh("podman", "logs", "linux-cit", check=False, capture=True)
        if (ready is None or ready.search(logs)) and answers(url):
            ok = True
            break
        time.sleep(3)
    print("---- container log ----", flush=True)
    subprocess.run(["podman", "logs", "linux-cit"], check=False)
    subprocess.run(["podman", "rm", "-f", "linux-cit"], check=False, capture_output=True)
    if not ok:
        sys.exit(f"not up within {t.wait or 120}s: {url}" + (f" and log /{t.ready}/" if ready else ""))
    print(f"up: {url}", flush=True)


def push(cfg, arch: str) -> None:
    tag = f"{default_tag(cfg)}-linux-{arch}"
    token, actor = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_ACTOR")
    host = cfg.registry.split("/", 1)[0]
    subprocess.run(["podman", "login", host, "-u", actor or "", "--password-stdin"],
                   input=token or "", text=True, check=True)
    sh("podman", "push", LOCAL, f"docker://{cfg.full_image}:{tag}")
    dh_user, dh_token = os.environ.get("DOCKERHUB_USERNAME"), os.environ.get("DOCKERHUB_TOKEN")
    if dh_user and dh_token:
        parts = cfg.registry.split("/", 1)
        org = dh_user if len(parts) < 2 else parts[1]
        subprocess.run(["podman", "login", "docker.io", "-u", dh_user, "--password-stdin"],
                       input=dh_token, text=True, check=True)
        sh("podman", "push", LOCAL, f"docker://docker.io/{org}/{cfg.image}:{tag}")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("amd64", "arm64"):
        sys.exit("usage: linux-image.py amd64|arm64 [--push]")
    arch = sys.argv[1]
    cfg = config_mod.load()
    build()
    test(cfg)
    if "--push" in sys.argv[2:]:
        push(cfg, arch)


if __name__ == "__main__":
    main()
