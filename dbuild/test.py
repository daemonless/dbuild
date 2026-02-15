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
from datetime import UTC, datetime
from pathlib import Path

from dbuild import log, podman
from dbuild.config import Config, TestConfig, Variant

# ── Cleanup registry (survives SIGTERM) ───────────────────────────────

_cleanup_targets: list[tuple[str | None, str | None]] = []
"""Stack of (compose_file, container_name) to clean up on exit."""


def _emergency_cleanup(*_args) -> None:
    """Remove all registered containers/stacks, then exit."""
    for compose_file, container_name in _cleanup_targets:
        try:
            if compose_file:
                podman.compose_down(compose_file)
            elif container_name:
                podman.stop(container_name)
                podman.rm(container_name)
        except Exception:
            pass
    _cleanup_targets.clear()


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
        if health or port:
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
    container: str,
    patterns: str,
    timeout: int,
) -> bool:
    """Poll container logs for ready patterns.

    Returns True if a ready pattern was found, False on timeout.
    Also checks that the container is still running.
    """
    compiled = re.compile(patterns)
    poll_interval = 3
    elapsed = 0
    while elapsed < timeout:
        if not podman.container_running(container, quiet=True):
            log.error("Container exited during ready wait")
            output = podman.logs(container)
            for line in output.splitlines()[-20:]:
                log.info(f"  {line}")
            return False

        output = podman.logs(container, quiet=True)
        if compiled.search(output):
            log.info(f"Ready signal after {elapsed}s")
            time.sleep(2)
            return True

        time.sleep(poll_interval)
        elapsed += poll_interval

    log.info(f"No ready signal after {timeout}s (continuing anyway)")
    return True  # timeout is not fatal -- the port/health check will catch failures


# ── Individual test implementations ──────────────────────────────────

def _test_shell(container: str) -> bool:
    """Verify the container is running and we can exec into it."""
    time.sleep(2)
    if not podman.container_running(container):
        log.error("Container exited immediately")
        output = podman.logs(container)
        for line in output.splitlines()[-20:]:
            log.info(f"  {line}")
        return False

    result = podman.exec_in(container, ["/bin/sh", "-c", "echo ok"])
    if result.returncode != 0:
        log.error("Cannot exec into container")
        return False

    log.success("Shell test passed")
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

    elapsed = 0
    while elapsed < timeout:
        try:
            if https:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
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
        passed, msg = verify(screenshot_file)
        if not passed:
            if save_to:
                _copy_file(screenshot_file, save_to)
            return False, f"Screenshot verification failed: {msg}"

        # Baseline comparison
        if baseline and baseline.is_file():
            log.info(f"Comparing to baseline: {baseline}")
            passed, msg = verify(screenshot_file, str(baseline))
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

def _test_variant(
    cfg: Config,
    variant: Variant,
    test: TestConfig,
    *,
    json_output: str | None = None,
) -> int:
    """Run CIT against one variant.  Returns 0 on success, 1 on failure."""
    build_ref = f"{cfg.full_image}:build-{variant.tag}"
    repo_dir = Path.cwd()

    log.step(f"Testing :{variant.tag}")
    log.info(f"Image: {build_ref}")

    # -- Merge config: labels + config overrides --
    compose_mode = test.compose
    compose_file: Path | None = None
    container_name = f"cit-{int(time.time())}-{os.getpid()}"

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
    log.info(f"Mode: {mode}")

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
    }
    rc = 1  # assume failure; set to 0 on success

    # -- Register for cleanup (survives SIGTERM) --
    cleanup_entry = (
        str(compose_file) if compose_mode and compose_file else None,
        container_name if not compose_mode else None,
    )
    _cleanup_targets.append(cleanup_entry)

    # -- Start container / compose stack --
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
            ip = ""  # resolved later

        # === SHELL TEST ===
        if not compose_mode:
            if not _test_shell(container_name):
                results["shell"] = "fail"
                return 1
            results["shell"] = "pass"

            if mode == "shell":
                log.success(f":{variant.tag} passed CIT (shell)")
                rc = 0
                return 0

        # Get container IP
        if not compose_mode:
            ip = podman.inspect_ip(container_name)
            if not ip:
                log.error("Could not get container IP")
                return 1
            log.info(f"Container IP: {ip}")

        # Wait for ready signal (health/screenshot only — port test has its own poll)
        if not compose_mode and mode in ("health", "screenshot"):
            _wait_for_ready(container_name, ready_patterns, test.wait)

        # === PORT TEST ===
        assert port is not None
        if not _test_port(ip, port, test.wait):
            results["port"] = "fail"
            if not compose_mode:
                output = podman.logs(container_name)
            else:
                assert compose_file is not None
                output = podman.compose_logs(str(compose_file))
            for line in output.splitlines()[-10:]:
                log.info(f"  {line}")
            return 1
        results["port"] = "pass"

        if mode == "port":
            log.success(f":{variant.tag} passed CIT (port)")
            rc = 0
            return 0

        # === HEALTH TEST ===
        assert health is not None
        if not _test_health(ip, port, health, test.wait, https=https):
            results["health"] = "fail"
            if not compose_mode:
                output = podman.logs(container_name)
            else:
                assert compose_file is not None
                output = podman.compose_logs(str(compose_file))
            for line in output.splitlines()[-10:]:
                log.info(f"  {line}")
            return 1
        results["health"] = "pass"

        if mode == "health":
            log.success(f":{variant.tag} passed CIT (health)")
            rc = 0
            return 0

        # === SCREENSHOT TEST ===
        passed, msg = _test_screenshot(
            ip,
            port,
            https=https,
            screenshot_path=test.screenshot_path,
            screenshot_wait=test.screenshot_wait or 0,
            baseline=baseline,
        )
        if not passed:
            results["screenshot"] = "fail"
            log.error(msg)
            return 1
        results["screenshot"] = "pass"
        results["verify"] = "pass"

        log.success(f":{variant.tag} passed CIT (screenshot)")
        rc = 0
        return 0

    finally:
        # -- Write JSON result if requested --
        if json_output:
            _write_json_result(json_output, build_ref, mode, results, rc == 0)

        # -- Cleanup: always stop/rm --
        log.info("Cleaning up...")
        if compose_mode and compose_file:
            podman.compose_down(str(compose_file))
        else:
            podman.stop(container_name)
            podman.rm(container_name)

        # Deregister from emergency cleanup
        if cleanup_entry in _cleanup_targets:
            _cleanup_targets.remove(cleanup_entry)


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
    backend = ci_mod.detect()
    if backend.should_skip("test"):
        log.info("Skipping tests ([skip test] in commit message)")
        return 0

    if cfg.test is None:
        log.warn("No test configuration found -- skipping CIT")
        return 0

    variant_filter: str | None = getattr(args, "variant", None)
    json_output: str | None = getattr(args, "json_output", None)
    worst_rc = 0
    tested = 0

    for variant in cfg.variants:
        if variant_filter and variant.tag != variant_filter:
            continue
        rc = _test_variant(cfg, variant, cfg.test, json_output=json_output)
        if rc != 0 and worst_rc == 0:
            worst_rc = rc
        tested += 1

    if tested == 0:
        log.warn("No variants matched the filter")
        return 0

    log.step("Test summary")
    if worst_rc == 0:
        log.success(f"All {tested} variant(s) passed")
    else:
        log.error(f"One or more variants failed (exit code {worst_rc})")

    return worst_rc
