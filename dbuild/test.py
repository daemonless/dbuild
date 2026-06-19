"""Container Integration Test (CIT) -- native Python implementation.

Test modes (cumulative -- each includes all below):
    screenshot  →  health + capture screenshot + visual verify
    health      →  port + HTTP health endpoint check
    port        →  shell + TCP port is listening
    shell       →  container starts, can exec into it

Auto-detection priority: CLI/config overrides > OCI labels > defaults.
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import ssl
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dbuild import log, podman
from dbuild.config import AppTestConfig, Config, Variant
from dbuild.container_backend import AppJailBackend, ContainerBackend, PodmanBackend

# ── Cleanup registry (survives SIGTERM) ───────────────────────────────

@dataclass
class _CleanupTarget:
    """A container or compose stack to tear down on exit or SIGTERM."""

    compose_file: str | None = None
    backend: ContainerBackend | None = None
    cname: str | None = None

    def cleanup(self) -> None:
        if self.compose_file:
            podman.compose_down(self.compose_file)
        elif self.backend and self.cname:
            self.backend.stop(self.cname)


_cleanup_targets: list[_CleanupTarget] = []
"""Stack of targets to clean up on exit."""

_volume_cleanup: list[str] = []
"""Named volumes to remove on exit (used by the --puid test)."""


def _emergency_cleanup(*_args) -> None:
    """Remove all registered containers/stacks/volumes, then exit."""
    for target in _cleanup_targets:
        with contextlib.suppress(Exception):
            target.cleanup()
    _cleanup_targets.clear()
    for vol in _volume_cleanup:
        with contextlib.suppress(Exception):
            podman.volume_rm(vol)
    _volume_cleanup.clear()


# Register for SIGTERM (sent by TaskStop / kill) and normal exit
signal.signal(signal.SIGTERM, lambda *a: (_emergency_cleanup(), exit(130)))
atexit.register(_emergency_cleanup)

# ── Default ready patterns ────────────────────────────────────────────

_DEFAULT_READY_PATTERNS = (
    r"Warmup complete"
    r"|services\.d.*done"
    r"|Application started"
    r"|Startup complete"
    r"|listening on"
    r"|is ready"
)


# ── Label reading ─────────────────────────────────────────────────────

def _read_labels(image_ref: str) -> dict:
    """Read OCI labels from an image and extract CIT-relevant values."""
    labels = podman.inspect_labels(image_ref)
    port_str = labels.get("io.daemonless.port")
    health_raw = labels.get("io.daemonless.healthcheck-url")

    # Extract path from health URL (strip scheme+host if present)
    health = None
    if health_raw and health_raw != "<no value>":
        health = re.sub(r"^https?://[^/]*", "", health_raw)
        if not health:
            health = "/"

    port = None
    if port_str and port_str != "<no value>":
        with contextlib.suppress(ValueError):
            port = int(port_str)

    jail_annotations = {
        k: "true"
        for k, v in labels.items()
        if k.startswith("org.freebsd.jail.") and v in ("required", "true")
    }

    return {
        "port": port,
        "health": health,
        "jail_annotations": jail_annotations,
    }


# ── Mode auto-detection ──────────────────────────────────────────────

def _find_baseline(repo_dir: Path, tag: str | None = None) -> Path | None:
    """Find a baseline.png for screenshot comparison."""
    candidates: list[Path] = []
    if tag:
        candidates += [
            repo_dir / ".daemonless" / f"baseline-{tag}.png",
            repo_dir / ".daemonless" / "baselines" / f"baseline-{tag}.png",
        ]
    candidates += [
        repo_dir / ".daemonless" / "baseline.png",
        repo_dir / ".daemonless" / "baselines" / "baseline.png",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _find_compose_file(repo_dir: Path) -> Path | None:
    """Find the compose file for a compose-mode test."""
    for name in ("compose.yaml", "compose.yml"):
        candidate = repo_dir / ".daemonless" / name
        if candidate.is_file():
            return candidate
    return None


def _check_screenshot_deps() -> list[str]:
    """Return a list of missing screenshot dependencies."""
    missing: list[str] = []

    # Python packages
    try:
        import selenium  # noqa: F401
    except ImportError:
        missing.append("py311-selenium (python package)")
    try:
        import skimage  # noqa: F401
    except ImportError:
        missing.append("py311-scikit-image (python package)")

    # System binaries
    chrome_bin = os.environ.get("CHROME_BIN", "/usr/local/bin/chrome")
    chromedriver_bin = os.environ.get("CHROMEDRIVER_BIN", "/usr/local/bin/chromedriver")
    if not Path(chrome_bin).is_file():
        missing.append(f"chromium ({chrome_bin})")
    if not Path(chromedriver_bin).is_file():
        missing.append(f"chromedriver ({chromedriver_bin})")

    return missing


def _downgrade_mode(
    mode: str,
    *,
    port: int | None,
    health: str | None,
) -> str:
    """Downgrade a mode to the next level that doesn't need extra deps.

    screenshot → health → port → shell
    """
    if mode == "screenshot":
        if health:
            return "health"
        if port:
            return "port"
        return "shell"
    # health/port/shell need no special deps
    return mode


def _resolve_mode(
    mode: str,
    *,
    port: int | None,
    health: str | None,
    baseline: Path | None,
) -> str:
    """Determine effective test mode, downgrading if deps are missing.

    If mode is empty, auto-detect first.  Then verify deps and
    downgrade with a warning if anything is missing.
    """
    # Auto-detect if not explicitly set
    if not mode:
        if baseline:
            mode = "screenshot"
        elif health:
            mode = "health"
        elif port:
            mode = "port"
        else:
            mode = "shell"

    # Check deps for screenshot mode
    if mode == "screenshot":
        missing = _check_screenshot_deps()
        if missing:
            fallback = _downgrade_mode(mode, port=port, health=health)
            log.warn("Screenshot mode requires missing dependencies:")
            for dep in missing:
                log.warn(f"  - {dep}")
            log.warn(f"Downgrading: screenshot -> {fallback}")
            return fallback

    return mode


# ── Ready-pattern log waiting ────────────────────────────────────────

def _wait_for_ready(
    cname: str,
    patterns: str,
    timeout: int,
    *,
    backend: ContainerBackend,
) -> bool:
    """Poll container logs for ready patterns.

    Returns True if a ready pattern was found, False on timeout.
    Also checks that the container is still running.
    """
    compiled = re.compile(patterns)
    poll_interval = 3
    elapsed = 0
    while elapsed < timeout:
        if not backend.running(cname, quiet=True):
            log.error("Container exited during ready wait")
            for line in backend.logs(cname).splitlines()[-20:]:
                log.info(f"  {line}")
            return False

        if compiled.search(backend.logs(cname, quiet=True)):
            log.info(f"Ready signal after {elapsed}s")
            time.sleep(2)
            return True

        time.sleep(poll_interval)
        elapsed += poll_interval

    log.info(f"No ready signal after {timeout}s (continuing anyway)")
    return True  # timeout is not fatal -- the port/health check will catch failures


# ── Individual test implementations ──────────────────────────────────

def _test_shell(cname: str, *, backend: ContainerBackend) -> bool:
    """Verify the container is running and we can exec into it."""
    time.sleep(2)

    if not backend.running(cname):
        log.error("Container exited immediately")
        for line in backend.logs(cname).splitlines()[-20:]:
            log.info(f"  {line}")
        return False

    result = backend.exec_in(cname, ["/bin/sh", "-c", "echo ok"])
    if result.returncode != 0:
        log.error("Cannot exec into container")
        return False

    log.success("Shell test passed")
    return True


def _test_command(
    image_ref: str,
    test: AppTestConfig,
    *,
    annotations: dict[str, str],
) -> bool:
    """Run a one-shot image to completion; check exit code (+ optional regex).

    For CLI/tool images whose entrypoint runs once and exits — there is no
    long-lived process for shell/port/health/screenshot to probe.
    """
    desc = " ".join(test.command) if test.command else "(image default CMD)"
    log.info(f"Command mode: running entrypoint with {desc}")
    rc, output = podman.run_oneshot(image_ref, test.command, annotations=annotations)
    for line in output.splitlines()[-20:]:
        log.info(f"  {line}")
    log.info(f"Exit code: {rc} (expected {test.expect_exit})")
    if rc != test.expect_exit:
        log.error(f"Exit code {rc} != expected {test.expect_exit}")
        return False
    if test.expect_output and not re.search(test.expect_output, output):
        log.error(f"Output did not match /{test.expect_output}/")
        return False
    if test.expect_output:
        log.info(f"Output matched /{test.expect_output}/")
    log.success("Command test passed")
    return True


def _test_port(ip: str, port: int, timeout: int) -> bool:
    """Wait for a TCP port to be listening using stdlib socket."""
    log.info(f"Waiting for {ip}:{port} (timeout: {timeout}s)")
    elapsed = 0
    while elapsed < timeout:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        try:
            result = sock.connect_ex((ip, port))
            if result == 0:
                log.info(f"Port ready after {elapsed}s")
                return True
        finally:
            sock.close()
        time.sleep(1)
        elapsed += 1

    log.error(f"Port {port} not listening after {timeout}s")
    return False


def _test_health(
    ip: str,
    port: int,
    path: str,
    timeout: int,
    https: bool = False,
) -> bool:
    """Wait for an HTTP endpoint to respond with a non-error status.

    Accepts any response (2xx, 4xx) as healthy.  Only connection
    failures, 502, and 503 are treated as not-ready.
    """
    scheme = "https" if https else "http"
    url = f"{scheme}://{ip}:{port}{path}"
    log.info(f"Health check: {url} (timeout: {timeout}s)")

    ctx: ssl.SSLContext | None = None
    if https:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    elapsed = 0
    while elapsed < timeout:
        try:
            if https:
                conn = http.client.HTTPSConnection(ip, port, timeout=5, context=ctx)
            else:
                conn = http.client.HTTPConnection(ip, port, timeout=5)
            conn.request("GET", path)
            resp = conn.getresponse()
            code = resp.status
            conn.close()

            if code not in (502, 503):
                log.info(f"Health ready after {elapsed}s (HTTP {code})")
                return True
        except (ConnectionRefusedError, ConnectionResetError, OSError, http.client.HTTPException):
            pass

        time.sleep(2)
        elapsed += 2

    log.error(f"Health check failed after {timeout}s")
    return False


def _test_screenshot(
    ip: str,
    port: int,
    *,
    https: bool = False,
    screenshot_path: str | None = None,
    screenshot_wait: int = 0,
    baseline: Path | None = None,
    save_to: str | None = None,
    ssim_threshold: float | None = None,
    edge_threshold: float | None = None,
) -> tuple[bool, str]:
    """Capture and verify a screenshot.

    Returns ``(passed, message)``.
    """
    try:
        from dbuild.screenshot import capture
    except ImportError as e:
        return False, f"Screenshot dependencies not installed: {e}"

    try:
        from dbuild.verify import verify
    except ImportError as e:
        return False, f"Verify dependencies not installed: {e}"

    scheme = "https" if https else "http"
    url_path = screenshot_path or "/"
    url = f"{scheme}://{ip}:{port}{url_path}"
    log.info(f"Screenshot: {url}")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        screenshot_file = tmp.name

    try:
        if not capture(url, screenshot_file, timeout=30, min_wait=screenshot_wait):
            return False, "Screenshot capture failed"

        # Basic verification
        passed, msg = verify(screenshot_file, edge_threshold=edge_threshold)
        if not passed:
            if save_to:
                _copy_file(screenshot_file, save_to)
            return False, f"Screenshot verification failed: {msg}"

        # Baseline comparison
        if baseline and baseline.is_file():
            log.info(f"Comparing to baseline: {baseline}")
            passed, msg = verify(
                screenshot_file, str(baseline),
                threshold=ssim_threshold, edge_threshold=edge_threshold,
            )
            if not passed:
                if save_to:
                    _copy_file(screenshot_file, save_to)
                return False, f"Baseline comparison failed: {msg}"

        if save_to:
            _copy_file(screenshot_file, save_to)

        return True, "Screenshot verified"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(screenshot_file)


def _copy_file(src: str, dest: str) -> None:
    """Copy a file (avoiding shutil for simplicity)."""
    with open(src, "rb") as f:
        data = f.read()
    with open(dest, "wb") as f:
        f.write(data)


# ── Result tracking ──────────────────────────────────────────────────

def _json_output_path(base: str, *, backend: str | None, tag: str | None) -> str:
    """Derive a per-run JSON path by suffixing backend/tag before the extension.

    Used when one ``dbuild test`` invocation produces multiple results
    (several variants and/or backends) so each run gets its own file
    instead of overwriting *base*::

        _json_output_path("cit-result.json", backend=None, tag="pkg")
            -> "cit-result-pkg.json"
        _json_output_path("cit-result.json", backend="appjail", tag="pkg")
            -> "cit-result-appjail-pkg.json"
    """
    parts = [p for p in (backend, tag) if p]
    if not parts:
        return base
    path = Path(base)
    return str(path.with_name(f"{path.stem}-{'-'.join(parts)}{path.suffix}"))


def _write_json_result(
    path: str,
    image: str,
    mode: str,
    results: dict[str, str],
    passed: bool,
) -> None:
    """Write a JSON result file for CI consumption."""
    data = {
        "image": image,
        "mode": mode,
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **results,
        "result": "pass" if passed else "fail",
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    log.info(f"Wrote result to {path}")


# ── Main test orchestration ──────────────────────────────────────────

def _dump_logs(
    compose_mode: bool,
    compose_file: Path | None,
    backend: ContainerBackend,
    cname: str,
) -> None:
    """Print the last 10 lines of container/compose logs."""
    if compose_mode:
        assert compose_file is not None
        output = podman.compose_logs(str(compose_file))
    else:
        output = backend.logs(cname)
    for line in output.splitlines()[-10:]:
        log.info(f"  {line}")


def _functional_checks(
    *,
    backend: ContainerBackend,
    cname: str,
    mode: str,
    port: int | None,
    health: str | None,
    https: bool,
    ready_patterns: str,
    test: AppTestConfig,
    variant: Variant,
    baseline: Path | None,
    results: dict[str, str],
    compose_mode: bool,
    compose_file: Path | None,
) -> int:
    """Run shell/port/health/screenshot against an already-started container.

    Does not manage container lifecycle.  Returns 0 on success, 1 on failure.
    """
    # Compose stacks have no single container to exec into, so shell mode
    # cannot prove anything — require at least a port to test against.
    if compose_mode and mode == "shell":
        log.error(
            "compose: true needs a port or health endpoint to test "
            "(shell mode is not supported with compose)"
        )
        return 1

    # === SHELL TEST ===
    if not compose_mode:
        if not _test_shell(cname, backend=backend):
            results["shell"] = "fail"
            return 1
        results["shell"] = "pass"
        if mode == "shell":
            log.success(f":{variant.tag} passed CIT (shell)")
            return 0

    # Resolve IP
    if compose_mode:
        ip = "127.0.0.1"
    else:
        ip = backend.get_ip(cname)
        if not ip:
            log.error("Could not get container IP")
            return 1
        log.info(f"Container IP: {ip}")
        if mode in ("health", "screenshot"):
            _wait_for_ready(cname, ready_patterns, test.wait, backend=backend)

    # === PORT TEST ===
    if port is None:
        log.error(
            f"Mode '{mode}' needs a port -- set cit: port: in config "
            "or the io.daemonless.port label"
        )
        return 1
    if not _test_port(ip, port, test.wait):
        results["port"] = "fail"
        _dump_logs(compose_mode, compose_file, backend, cname)
        return 1
    results["port"] = "pass"
    if mode == "port":
        log.success(f":{variant.tag} passed CIT (port)")
        return 0

    # === HEALTH TEST ===
    if health is None:
        log.error(
            f"Mode '{mode}' needs a health path -- set cit: health: in config "
            "or the io.daemonless.healthcheck-url label"
        )
        return 1
    if not _test_health(ip, port, health, test.wait, https=https):
        results["health"] = "fail"
        _dump_logs(compose_mode, compose_file, backend, cname)
        return 1
    results["health"] = "pass"
    if mode == "health":
        log.success(f":{variant.tag} passed CIT (health)")
        return 0

    # === SCREENSHOT TEST ===
    screenshot_save = f"/tmp/cit-screenshot-{variant.tag}.png"
    passed, msg = _test_screenshot(
        ip,
        port,
        https=https,
        screenshot_path=test.screenshot_path,
        screenshot_wait=test.screenshot_wait or 0,
        baseline=baseline,
        save_to=screenshot_save,
        ssim_threshold=test.ssim_threshold,
        edge_threshold=test.edge_threshold,
    )
    if not passed:
        results["screenshot"] = "fail"
        log.error(msg)
        return 1
    results["screenshot"] = "pass"
    results["verify"] = "pass"
    log.success(f":{variant.tag} passed CIT (screenshot)")
    return 0


def _test_variant(
    cfg: Config,
    variant: Variant,
    test: AppTestConfig,
    *,
    json_output: str | None = None,
    force_backend: str = "auto",
    puid_enabled: bool = False,
    out: dict | None = None,
) -> int:
    """Run CIT against one variant.  Returns 0 on success, 1 on failure.

    When *puid_enabled* (podman, non-compose only), the CIT container is
    started on a ``/config`` volume at PUID/PGID 1000 and doubles as the
    ownership "deploy 1"; after the functional checks pass it is redeployed
    once at 1234:5678 to verify the re-chown.
    """
    build_ref = f"{cfg.full_image}:build-{variant.tag}"
    repo_dir = Path.cwd()

    log.step(f"Testing :{variant.tag}")
    log.info(f"Image: {build_ref}")

    # -- Merge config: labels + config overrides --
    # AppJail backend bypasses compose — it uses Director instead
    compose_mode = test.compose and force_backend != "appjail"
    compose_file: Path | None = None

    if compose_mode:
        if not shutil.which("podman-compose"):
            log.error("compose: true but podman-compose is not installed")
            return 1
        compose_file = _find_compose_file(repo_dir)
        if not compose_file:
            log.error("compose: true but no compose.yaml found")
            return 1
        log.info(f"Compose mode: {compose_file}")
        # Tag build image as :build so compose.yaml can reference it
        build_tag = f"{cfg.full_image}:build"
        podman.tag(build_ref, build_tag)
        label_info: dict = {"port": None, "health": None, "jail_annotations": {}}
    else:
        label_info = _read_labels(build_ref)

    # Merge: config overrides > labels > defaults
    port = test.port or label_info["port"]
    health = test.health or label_info["health"]
    https = test.https
    annotations: dict[str, str] = {}

    # Jail annotations from labels
    annotations.update(label_info.get("jail_annotations", {}))

    # Annotations from config (format: "key=value")
    for ann in test.annotations:
        if "=" in ann:
            k, v = ann.split("=", 1)
            annotations[k] = v

    # Baseline
    baseline = _find_baseline(repo_dir, variant.tag)

    # Resolve mode: auto-detect if needed, downgrade if deps missing
    mode = _resolve_mode(
        test.mode, port=port, health=health, baseline=baseline,
    )
    if out is not None:
        out["mode"] = mode
    log.info(f"Mode: {mode}")

    # === COMMAND MODE — one-shot tools (run to completion, no live process) ===
    # CLI/tool images whose entrypoint runs once and exits have nothing for
    # shell/port/health/screenshot to probe.  Run the image to completion and
    # assert the exit code (and an optional output regex) instead.  Bypasses
    # the detached-start lifecycle below.
    if mode == "command":
        if compose_mode:
            log.error("command mode does not support compose: true")
            return 1
        if force_backend == "appjail":
            log.error("command mode is podman-only (it runs the image to completion)")
            return 1
        cmd_results = {"command": "skip"}
        passed = _test_command(build_ref, test, annotations=annotations)
        cmd_results["command"] = "pass" if passed else "fail"
        if out is not None:
            out.update(cmd_results)
        if json_output:
            _write_json_result(json_output, build_ref, "command", cmd_results, passed)
        if passed:
            log.success(f":{variant.tag} passed CIT (command)")
            return 0
        return 1

    # Fill in defaults for modes that need port/health
    if mode in ("port", "health", "screenshot"):
        if port is None:
            port = 8080
        log.info(f"Port: {port}")

    if mode in ("health", "screenshot"):
        if health is None:
            health = "/"
        log.info(f"Health: {health}")

    # Ready patterns
    ready_patterns = test.ready or _DEFAULT_READY_PATTERNS

    # -- Result tracking --
    results: dict[str, str] = {
        "shell": "skip",
        "port": "skip",
        "health": "skip",
        "screenshot": "skip",
        "verify": "skip",
        "ownership": "skip",
        "re-chown": "skip",
    }
    rc = 1  # assume failure; set to 0 on success

    # -- Select backend --
    if force_backend == "appjail":
        if not AppJailBackend.available():
            log.error("--backend appjail requested but appjail is not installed")
            return 1
        backend: ContainerBackend = AppJailBackend()
    elif force_backend == "podman":
        backend = PodmanBackend()
    else:  # auto
        backend = AppJailBackend() if (cfg.metadata.appjail and AppJailBackend.available()) else PodmanBackend()

    # PUID check only works on the podman backend, non-compose (needs env/volume).
    puid_run = puid_enabled and not compose_mode and isinstance(backend, PodmanBackend)
    if puid_enabled and not puid_run:
        log.info("Ownership check skipped (needs podman backend, non-compose)")

    cname = f"cit-{os.getpid()}-{cfg.image}"
    cname2 = f"{cname}-rechown"

    # When the ownership check runs, the CIT container is started on a
    # persistent /config volume at PUID/PGID 1000 (= ownership deploy 1).
    init_uid, init_gid = _PUID_INITIAL
    volume = f"cit-puid-{os.getpid()}-{cfg.image}-{variant.tag}" if puid_run else None
    start_env = {"PUID": str(init_uid), "PGID": str(init_gid)} if puid_run else None
    start_vols = [f"{volume}:/config"] if puid_run else None

    # -- Register for cleanup (survives SIGTERM) --
    cleanup_entry = _CleanupTarget(
        compose_file=str(compose_file) if compose_mode and compose_file else None,
        backend=None if compose_mode else backend,
        cname=None if compose_mode else cname,
    )
    rechown_entry = _CleanupTarget(backend=backend, cname=cname2)
    _cleanup_targets.append(cleanup_entry)
    if puid_run:
        _cleanup_targets.append(rechown_entry)
        assert volume is not None
        _volume_cleanup.append(volume)

    # -- Start container / compose stack --
    try:
        if compose_mode:
            assert compose_file is not None
            podman.compose_up(str(compose_file))
        else:
            backend.start(
                cname, build_ref, annotations=annotations,
                env=start_env, volumes=start_vols,
            )

        # -- Functional checks (shell/port/health/screenshot) --
        rc = _functional_checks(
            backend=backend, cname=cname, mode=mode, port=port, health=health,
            https=https, ready_patterns=ready_patterns, test=test,
            variant=variant, baseline=baseline, results=results,
            compose_mode=compose_mode, compose_file=compose_file,
        )
        if rc != 0:
            return rc

        # -- Ownership check (deploy 1 = this container, then re-chown) --
        if puid_run:
            assert volume is not None
            rc = _puid_phase(
                backend, cname, cname2, build_ref,
                volume=volume, annotations=annotations,
                wait=test.wait, results=results, ignore=test.puid_ignore,
            )
        return rc

    finally:
        # -- Write JSON result if requested --
        if json_output:
            _write_json_result(json_output, build_ref, mode, results, rc == 0)

        # -- Cleanup: always stop/rm --
        log.info("Cleaning up...")
        if compose_mode and compose_file:
            podman.compose_down(str(compose_file))
        else:
            backend.stop(cname)
        if puid_run:
            backend.stop(cname2)
            assert volume is not None
            podman.volume_rm(volume)
            if volume in _volume_cleanup:
                _volume_cleanup.remove(volume)

        # Deregister from emergency cleanup
        for entry in (cleanup_entry, rechown_entry):
            if entry in _cleanup_targets:
                _cleanup_targets.remove(entry)


# ── Screenshot entry point ────────────────────────────────────────────

def run_screenshot(cfg: Config, args: argparse.Namespace) -> int:
    """Capture a screenshot of a built image.

    Starts the container (or compose stack), waits for readiness,
    captures a screenshot, and saves it.  Cleaned up afterwards.

    Returns 0 on success, 1 on failure.
    """
    missing = _check_screenshot_deps()
    if missing:
        log.error("Screenshot requires:")
        for dep in missing:
            log.error(f"  - {dep}")
        return 1

    if cfg.test is None:
        log.error("No test configuration found")
        return 1

    test = cfg.test
    repo_dir = Path.cwd()
    variant_filter: str | None = getattr(args, "variant", None)

    # -- Compose detection --
    compose_mode = test.compose
    compose_file: Path | None = None

    if compose_mode:
        if not shutil.which("podman-compose"):
            log.error("compose: true but podman-compose is not installed")
            return 1
        compose_file = _find_compose_file(repo_dir)
        if not compose_file:
            log.error("compose: true but no compose.yaml found")
            return 1
        log.info(f"Compose mode: {compose_file}")

    # -- Variant resolution (optional for compose) --
    variant = None
    for v in cfg.variants:
        if variant_filter and v.tag != variant_filter:
            continue
        variant = v
        break

    if variant is None and not compose_mode:
        log.error("No matching variant found")
        return 1

    # -- Config / label reading --
    tag = variant.tag if variant else None

    if compose_mode:
        if variant:
            # Try build-{tag} first (CI), fall back to :{tag} (local)
            build_ref = f"{cfg.full_image}:build-{tag}"
            if not podman.image_exists(build_ref):
                build_ref = f"{cfg.full_image}:{tag}"
                if not podman.image_exists(build_ref):
                    log.error(f"No image found: tried build-{tag} and {tag}")
                    return 1
                log.info(f"Using local image: {build_ref}")
            build_tag = f"{cfg.full_image}:build"
            podman.tag(build_ref, build_tag)
        label_info: dict = {"port": None, "health": None, "jail_annotations": {}}
    else:
        assert variant is not None
        # Try build-{tag} first (CI), fall back to :{tag} (local)
        build_ref = f"{cfg.full_image}:build-{tag}"
        if not podman.image_exists(build_ref):
            build_ref = f"{cfg.full_image}:{tag}"
            if not podman.image_exists(build_ref):
                log.error(f"No image found: tried build-{tag} and {tag}")
                return 1
            log.info(f"Using local image: {build_ref}")
        label_info = _read_labels(build_ref)

    port = test.port or label_info["port"]
    health = test.health or label_info["health"]
    https = test.https

    if not port:
        log.error("No port configured — cannot capture screenshot")
        return 1

    annotations: dict[str, str] = {}
    annotations.update(label_info.get("jail_annotations", {}))
    for ann in test.annotations:
        if "=" in ann:
            k, v = ann.split("=", 1)
            annotations[k] = v

    ready_patterns = test.ready or _DEFAULT_READY_PATTERNS

    # -- Output path --
    output = getattr(args, "output", None)
    if not output:
        if tag:
            output = str(repo_dir / ".daemonless" / f"baseline-{tag}.png")
        else:
            output = str(repo_dir / ".daemonless" / "baseline.png")

    # -- Cleanup registration --
    container_name = f"screenshot-{int(time.time())}-{os.getpid()}"
    backend = PodmanBackend()
    cleanup_entry = _CleanupTarget(
        compose_file=str(compose_file) if compose_mode and compose_file else None,
        backend=None if compose_mode else backend,
        cname=None if compose_mode else container_name,
    )
    _cleanup_targets.append(cleanup_entry)

    label = f":{tag}" if tag else "(compose stack)"
    log.step(f"Capturing screenshot for {label}")
    if not compose_mode:
        log.info(f"Image: {build_ref}")
    log.info(f"Output: {output}")

    try:
        if compose_mode:
            assert compose_file is not None
            podman.compose_up(str(compose_file))
            ip = "127.0.0.1"
        else:
            cid = podman.run_detached(
                build_ref,
                name=container_name,
                annotations=annotations,
            )
            log.info(f"Started: {cid}")

            # Shell check
            if not _test_shell(container_name, backend=backend):
                return 1

            ip = podman.inspect_ip(container_name)
            if not ip:
                log.error("Could not get container IP")
                return 1
            log.info(f"Container IP: {ip}")

            # Wait for ready
            _wait_for_ready(container_name, ready_patterns, test.wait, backend=backend)

        # Wait for port
        if not _test_port(ip, port, test.wait):
            if compose_mode:
                assert compose_file is not None
                output_logs = podman.compose_logs(str(compose_file))
            else:
                output_logs = podman.logs(container_name)
            for line in output_logs.splitlines()[-10:]:
                log.info(f"  {line}")
            return 1

        # Wait for health if configured
        if health and not _test_health(ip, port, health, test.wait, https=https):
            if compose_mode:
                assert compose_file is not None
                output_logs = podman.compose_logs(str(compose_file))
            else:
                output_logs = podman.logs(container_name)
            for line in output_logs.splitlines()[-10:]:
                log.info(f"  {line}")
            return 1

        # Capture screenshot
        from dbuild.screenshot import capture

        screenshot_wait = test.screenshot_wait or 0
        scheme = "https" if https else "http"
        screenshot_path = test.screenshot_path or "/"
        url = f"{scheme}://{ip}:{port}{screenshot_path}"

        log.info(f"Capturing: {url}")
        if not capture(url, output, timeout=30, min_wait=screenshot_wait):
            log.error("Screenshot capture failed")
            return 1

        log.success(f"Screenshot saved to {output}")
        return 0

    finally:
        log.info("Cleaning up...")
        cleanup_entry.cleanup()
        if cleanup_entry in _cleanup_targets:
            _cleanup_targets.remove(cleanup_entry)


# ── PUID/PGID remap test ──────────────────────────────────────────────

#: Logged by base/root/init once cont-init.d (incl. the usermod chown) is done.
_PUID_READY = r"\[init\] Initialization complete"

#: Deploy 1 uses the baked-in defaults; deploy 2 uses values that can't
#: coincide with them, so a pass proves the remap actually happened.
_PUID_INITIAL = (1000, 1000)
_PUID_CHANGED = (1234, 5678)


def _puid_assert(
    backend: ContainerBackend, cname: str, want_uid: int, want_gid: int,
    ignore: list[str] | None = None,
) -> tuple[bool, str]:
    """Verify the ``bsd`` user is remapped and /config is fully owned by it.

    Paths matching an *ignore* glob (find ``-path``) are pruned, e.g. a
    ``/config/ssh`` dir whose host keys must stay root-owned for sshd.
    """
    uid = backend.exec_in(cname, ["id", "-u", "bsd"]).stdout.strip()
    gid = backend.exec_in(cname, ["id", "-g", "bsd"]).stdout.strip()
    if uid != str(want_uid) or gid != str(want_gid):
        return False, f"id bsd = {uid}:{gid}, expected {want_uid}:{want_gid}"

    # Any path under /config not owned by want_uid:want_gid is a failure,
    # except pruned ignore paths.
    find_cmd = ["find", "/config"]
    if ignore:
        prune = []
        for glob in ignore:
            if prune:
                prune.append("-o")
            prune += ["-path", glob]
        find_cmd += ["(", *prune, ")", "-prune", "-o"]
    find_cmd += [
        "(", "!", "-uid", str(want_uid), "-o", "!", "-gid", str(want_gid), ")",
        "-print",
    ]
    res = backend.exec_in(cname, find_cmd)
    offenders = res.stdout.strip()
    if offenders:
        listed = "\n".join(f"    {p}" for p in offenders.splitlines()[:20])
        return False, f"paths under /config not owned by {want_uid}:{want_gid}:\n{listed}"
    return True, f"bsd={want_uid}:{want_gid}, /config fully owned"


def _puid_start_and_wait(
    backend: ContainerBackend,
    cname: str,
    build_ref: str,
    *,
    env: dict[str, str],
    volumes: list[str],
    annotations: dict[str, str],
    wait: int,
) -> bool:
    """Start a container and wait for cont-init to finish."""
    backend.start(cname, build_ref, annotations=annotations, env=env, volumes=volumes)
    if not _test_shell(cname, backend=backend):
        return False
    return _wait_for_ready(cname, _PUID_READY, wait, backend=backend)


def _puid_phase(
    backend: ContainerBackend,
    cname1: str,
    cname2: str,
    build_ref: str,
    *,
    volume: str,
    annotations: dict[str, str],
    wait: int,
    results: dict[str, str],
    ignore: list[str] | None = None,
) -> int:
    """Ownership check, integrated into the CIT flow.

    *cname1* is the already-running CIT container, started on *volume* at
    PUID/PGID 1000 — it doubles as ownership "deploy 1".  This asserts its
    ownership, then redeploys (*cname2*) on the same volume at 1234:5678 to
    verify the re-chown.  Returns 0 on success, 1 on failure.
    """
    init_uid, init_gid = _PUID_INITIAL
    new_uid, new_gid = _PUID_CHANGED

    # Make sure cont-init (incl. the usermod chown) has finished before
    # asserting — shell-mode functional checks don't wait for it.
    _wait_for_ready(cname1, _PUID_READY, wait, backend=backend)

    # ── Deploy 1: assert ownership on the running CIT container ──
    ok, msg = _puid_assert(backend, cname1, init_uid, init_gid, ignore)
    if not ok:
        results["ownership"] = "fail"
        log.error(f"ownership (PUID={init_uid}): {msg}")
        return 1
    results["ownership"] = "pass"
    log.success(f"ownership: {msg}")

    # Drop a marker owned by the initial uid/gid so the re-chown has
    # concrete pre-existing data to act on.
    marker = "/config/.cit-puid-marker"
    backend.exec_in(cname1, [
        "sh", "-c", f"touch {marker} && chown {init_uid}:{init_gid} {marker}",
    ])
    backend.stop(cname1)  # named volume persists

    # ── Deploy 2: redeploy with changed ids on the SAME volume ──
    log.info(f"Re-chown: redeploy PUID={new_uid} PGID={new_gid} (same volume)")
    if not _puid_start_and_wait(
        backend, cname2, build_ref,
        env={"PUID": str(new_uid), "PGID": str(new_gid)},
        volumes=[f"{volume}:/config"],
        annotations=annotations, wait=wait,
    ):
        results["re-chown"] = "fail"
        log.error("re-chown: redeploy failed to start")
        return 1

    ok, msg = _puid_assert(backend, cname2, new_uid, new_gid, ignore)
    if not ok:
        results["re-chown"] = "fail"
        log.error(f"re-chown: {msg}")
        return 1
    results["re-chown"] = "pass"
    log.success(f"re-chown: {msg}")
    return 0


# ── Public entry point ────────────────────────────────────────────────

def run(cfg: Config, args: argparse.Namespace) -> int:
    """Run CIT for all (or filtered) variants.

    Parameters
    ----------
    cfg:
        Parsed build configuration.
    args:
        CLI arguments.  Recognised attributes:

        * ``variant`` -- test only this tag (optional).

    Returns
    -------
    int
        ``0`` if all tests passed, otherwise the first non-zero exit code.
    """
    from dbuild import ci as ci_mod
    ci = ci_mod.detect()
    if ci.should_skip("test"):
        log.info("Skipping tests ([skip test] in commit message)")
        return 0

    if cfg.test is None:
        log.warn("No test configuration found -- skipping CIT")
        return 0

    variant_filter: str | None = getattr(args, "variant", None)
    json_output: str | None = getattr(args, "json_output", None)
    backend_arg: str = getattr(args, "backend", "all")

    # Determine which backends to run
    if backend_arg == "all":
        backends = ["podman"]
        if cfg.metadata.appjail:
            if AppJailBackend.available():
                backends.append("appjail")
            else:
                log.warn("appjail configured but not installed — skipping appjail backend")
    else:
        backends = [backend_arg]

    # PUID/PGID ownership check — folded into each variant's CIT run.
    # `--puid=true/false` overrides the per-image `cit: puid:` config.
    puid_override = getattr(args, "puid", None)
    puid_enabled = cfg.test.puid if puid_override is None else puid_override
    if not puid_enabled:
        log.info("Ownership (PUID/PGID) check disabled (--puid=false or cit: puid: false)")

    worst_rc = 0
    # (backend, tag, mode, passed)
    results_table: list[tuple[str, str, str, bool]] = []

    # Per-run JSON paths: when one invocation produces multiple results,
    # suffix the filename with whatever varies so runs don't overwrite
    # each other (the single-run case keeps the exact path given).
    matching = [
        v for v in cfg.variants
        if not variant_filter or v.tag == variant_filter
    ]

    for force_backend in backends:
        if len(backends) > 1:
            log.step(f"Backend: {force_backend}")
        for variant in matching:
            out_path = json_output
            if json_output:
                out_path = _json_output_path(
                    json_output,
                    backend=force_backend if len(backends) > 1 else None,
                    tag=variant.tag if len(matching) > 1 else None,
                )
            out: dict = {}
            rc = _test_variant(
                cfg, variant, cfg.test,
                json_output=out_path,
                force_backend=force_backend,
                puid_enabled=puid_enabled,
                out=out,
            )
            if rc != 0 and worst_rc == 0:
                worst_rc = rc
            results_table.append((force_backend, variant.tag, out.get("mode", "?"), rc == 0))

    tested = len(results_table)
    if tested == 0:
        log.warn("No variants matched the filter")
        return 0

    log.step("Test summary")
    multi_backend = len(backends) > 1
    tag_w = max(len(t) for _, t, _, _ in results_table) + 2  # +2 for colons
    for bk, tag, _, passed in results_table:
        tag_col = f":{tag}".ljust(tag_w)
        status = "[ok]" if passed else "[FAIL]"
        if multi_backend:
            log.plain(f"  {bk:<8}  {tag_col}  {status}")
        else:
            log.plain(f"  {tag_col}  {status}")

    passed_count = sum(1 for _, _, _, p in results_table if p)
    if worst_rc == 0:
        log.success(f"{passed_count}/{tested} passed")
    else:
        log.error(f"{passed_count}/{tested} passed")

    return worst_rc
