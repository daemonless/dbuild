"""kubectl and crictl wrappers for the Kubernetes CIT backend.

The CIT runs on a cluster node: ``kubectl`` talks to the API, ``crictl`` to
the node's CRI-O for image labels (pulling the image on the way, so the pod's
own pull is a no-op). Each test is one pod in the ``cit`` namespace. Pods are
never restarted by the kubelet (``restartPolicy: Never``): a crash is a
failed pod with its log intact, not a restart loop hidden behind "Running".
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import time

from dbuild import log

NAMESPACE = "cit"

#: How long a pod may stay Pending (the image pull, mostly) before start fails.
PENDING_TIMEOUT = 900


class KubeError(Exception):
    """A kubectl/crictl command failed."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{' '.join(cmd)} failed ({returncode}): {stderr}")


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    quiet: bool = False,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if not quiet:
        log.info(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", input=stdin)
    if check and result.returncode != 0:
        raise KubeError(cmd, result.returncode, (result.stderr or "").strip())
    return result


def kubectl(*args: str, **kw) -> subprocess.CompletedProcess[str]:
    return _run(["kubectl", "-n", NAMESPACE, *args], **kw)


def pod_name(cname: str) -> str:
    """A DNS label for a CIT container name: lowercase, [a-z0-9-], 63 max."""
    name = re.sub(r"[^a-z0-9-]+", "-", cname.lower()).strip("-")
    return name[:63].rstrip("-")


def ensure_namespace() -> None:
    res = _run(["kubectl", "get", "ns", NAMESPACE], check=False, quiet=True)
    if res.returncode != 0:
        _run(["kubectl", "create", "ns", NAMESPACE])


def pod_spec(
    name: str,
    image_ref: str,
    *,
    annotations: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
    args: list[str] | None = None,
    restart: str = "Never",
) -> dict:
    """The pod for one CIT run: one container, the image's own entrypoint.

    Pod annotations reach the OCI runtime as they are (CRI-O filters only
    its own experimental keys), so ``org.freebsd.jail.*`` lands in ocijail
    the same way ``podman run --annotation`` delivers it. No securityContext:
    the container starts as root and drops privileges itself, today's
    contract.
    """
    container: dict = {
        "name": "app",
        "image": image_ref,
        "imagePullPolicy": "IfNotPresent",
    }
    if env:
        container["env"] = [{"name": k, "value": str(v)} for k, v in env.items()]
    if args:
        container["args"] = list(args)
    spec = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/managed-by": "dbuild-cit"},
            "annotations": {k: str(v) for k, v in (annotations or {}).items()},
        },
        "spec": {
            "restartPolicy": restart,
            "terminationGracePeriodSeconds": 30,
            "containers": [container],
        },
    }
    return spec


def apply(spec: dict) -> None:
    kubectl("apply", "-f", "-", stdin=json.dumps(spec))


def get_pod(name: str) -> dict | None:
    res = kubectl("get", "pod", name, "-o", "json", check=False, quiet=True)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return None


def _container_state(pod: dict) -> tuple[str, str]:
    """(state, reason) of the pod's one container: waiting/running/terminated."""
    for cs in (pod.get("status") or {}).get("containerStatuses") or []:
        state = cs.get("state") or {}
        for key in ("running", "terminated", "waiting"):
            if key in state:
                return key, (state[key] or {}).get("reason", "")
    return "", ""


def wait_started(name: str, timeout: int = PENDING_TIMEOUT) -> None:
    """Wait until the pod's container has started (or already finished).

    Raises when the pull or the start fails for good (ErrImagePull,
    ImagePullBackOff, CreateContainerError...) or the timeout passes.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        pod = get_pod(name)
        if pod is not None:
            state, reason = _container_state(pod)
            if state in ("running", "terminated") and not _any_waiting(pod):
                return
            phase = (pod.get("status") or {}).get("phase", "")
            if reason in ("ErrImagePull", "ImagePullBackOff", "CreateContainerError",
                          "CreateContainerConfigError", "InvalidImageName"):
                msg = _waiting_message(pod)
                raise RuntimeError(f"pod {name} cannot start: {reason} {msg}".strip())
            cur = f"{phase}/{reason or state or '-'}"
            if cur != last:
                log.info(f"Pod {name}: {cur}")
                last = cur
        time.sleep(3)
    raise RuntimeError(f"pod {name} still not started after {timeout}s")


def _any_waiting(pod: dict) -> bool:
    """A container of the pod (a stack's sidecar) is still being created."""
    for cs in (pod.get("status") or {}).get("containerStatuses") or []:
        if "waiting" in (cs.get("state") or {}):
            return True
    return False


def _waiting_message(pod: dict) -> str:
    for cs in (pod.get("status") or {}).get("containerStatuses") or []:
        waiting = (cs.get("state") or {}).get("waiting") or {}
        if waiting.get("message"):
            return waiting["message"]
    return ""


def running(name: str) -> bool:
    pod = get_pod(name)
    if pod is None:
        return False
    state, _ = _container_state(pod)
    return state == "running"


def exit_code(name: str) -> int | None:
    pod = get_pod(name)
    if pod is None:
        return None
    for cs in (pod.get("status") or {}).get("containerStatuses") or []:
        term = (cs.get("state") or {}).get("terminated")
        if term is not None:
            return int(term.get("exitCode", -1))
    return None


def pod_ip(name: str) -> str:
    pod = get_pod(name)
    if pod is None:
        return ""
    return ((pod.get("status") or {}).get("podIP")) or ""


def logs(name: str, *, quiet: bool = False) -> str:
    res = kubectl("logs", name, "--container", "app", check=False, quiet=quiet)
    return (res.stdout or "") + (res.stderr if res.returncode != 0 else "")


def exec_in(name: str, cmd: list[str], *, first: bool = False) -> subprocess.CompletedProcess[str]:
    """Run in the pod's ``app`` container, or (first) in its first container."""
    target = [] if first else ["--container", "app"]
    return kubectl("exec", name, *target, "--", *cmd, check=False, quiet=True)


def delete(name: str) -> None:
    """Remove the pod now. Forced: a pod can hang in Terminating after CRI-O
    has already removed everything (seen on the FreeBSD stack), and the CIT
    needs the name back."""
    kubectl("delete", "pod", name, "--ignore-not-found", "--force",
            "--grace-period=0", "--wait=false", check=False)


def wait_finished(name: str, timeout: int) -> int | None:
    """Wait for a one-shot pod to end; its exit code, or None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = exit_code(name)
        if code is not None:
            return code
        time.sleep(2)
    return None


def image_labels(image_ref: str) -> dict[str, str]:
    """The image's OCI labels, through the node's CRI-O (pulling it first)."""
    _run(["crictl", "pull", image_ref])
    res = _run(["crictl", "inspecti", "-o", "json", image_ref], check=False, quiet=True)
    if res.returncode != 0 or not res.stdout.strip():
        return {}
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return {}
    labels = (((data.get("info") or {}).get("imageSpec") or {}).get("config") or {}).get("Labels")
    return labels if isinstance(labels, dict) else {}


def available() -> bool:
    """kubectl present and a kubeconfig to talk with."""
    from shutil import which
    if which("kubectl") is None:
        return False
    cfg = os.environ.get("KUBECONFIG") or os.path.expanduser("~/.kube/config")
    return os.path.exists(cfg)


# ── Compose stacks ───────────────────────────────────────────────────
#
# A daemonless stack is a compose file whose services share the host's
# network (network_mode: host) and find each other on localhost. The pod is
# that same shape: one pod, one container per service, one network
# namespace, so the compose file's localhost addresses hold as written.
# Bind mounts become hostPath dirs under a scratch root; named volumes too.

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}")


def interpolate(text: str, env: dict[str, str]) -> str:
    """Compose-style ``${VAR}`` / ``${VAR:-default}`` substitution."""
    def sub(m: re.Match) -> str:
        val = env.get(m.group(1))
        if val is None or (val == "" and m.group(0).startswith("${" + m.group(1) + ":-")):
            return m.group(2) or ""
        return val
    return _VAR.sub(sub, text)


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=value lines of a .env; comments and blanks skipped, quotes stripped."""
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    return env


def _service_env(svc: dict, env: dict[str, str], env_files: dict[str, dict[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in svc.get("env_file") or []:
        name = f if isinstance(f, str) else f.get("path", "")
        out.update(env_files.get(name, {}))
    raw = svc.get("environment") or {}
    if isinstance(raw, list):
        for item in raw:
            k, _, v = str(item).partition("=")
            out[k] = v
    else:
        out.update({k: "" if v is None else str(v) for k, v in raw.items()})
    return {k: interpolate(v, env) for k, v in out.items()}


def _mount_name(container_path: str) -> str:
    return pod_name("m-" + container_path.strip("/").replace("/", "-")) or "m-root"


def _service_container(
    svc_name: str,
    svc: dict,
    *,
    env: dict[str, str],
    env_files: dict[str, dict[str, str]],
    data_root: str,
    image_map: dict[str, str],
) -> tuple[dict, list[dict], str]:
    """A service's container spec, its hostPath volumes, and its image."""
    image = interpolate(str(svc.get("image", "")), env)
    image = image_map.get(image, image)
    cname = pod_name(svc_name)
    container: dict = {
        "name": cname,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
    }
    svc_env = _service_env(svc, env, env_files)
    if svc_env:
        container["env"] = [{"name": k, "value": v} for k, v in svc_env.items()]
    if svc.get("command"):
        cmd = svc["command"]
        container["args"] = cmd if isinstance(cmd, list) else cmd.split()
    mounts = []
    volumes = []
    for vol in svc.get("volumes") or []:
        if isinstance(vol, dict):
            src, dst, ro = vol.get("source", ""), vol.get("target", ""), bool(vol.get("read_only"))
        else:
            parts = interpolate(str(vol), env).split(":")
            if len(parts) < 2:
                continue
            src, dst = parts[0], parts[1]
            ro = len(parts) > 2 and "ro" in parts[2].split(",")
        src = interpolate(src, env)
        if src == "/etc/localtime":
            continue  # the node's clock reaches the pod anyway
        if src.startswith("/"):
            host = f"{data_root}/{cname}{src}" if src.startswith("/containers") or src.startswith("/path") else f"{data_root}/{cname}/{src.strip('/').replace('/', '-')}"
        else:
            host = f"{data_root}/vol-{pod_name(src)}"
        vname = pod_name(f"{cname}-{_mount_name(dst)}")
        volumes.append({"name": vname, "hostPath": {"path": host, "type": "DirectoryOrCreate"}})
        mount = {"name": vname, "mountPath": dst}
        if ro:
            mount["readOnly"] = True
        mounts.append(mount)
    if mounts:
        container["volumeMounts"] = mounts
    return container, volumes, image


def _pod(name: str, containers: list[dict], volumes: list[dict], annotations: dict[str, str], stack: str) -> dict:
    spec = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/managed-by": "dbuild-cit", "dbuild-cit/stack": stack},
            "annotations": annotations,
        },
        "spec": {
            "restartPolicy": "Always",
            "terminationGracePeriodSeconds": 60,
            "containers": containers,
        },
    }
    if volumes:
        spec["spec"]["volumes"] = volumes
    return spec


def published_ports(svc: dict, env: dict[str, str]) -> dict[int, int]:
    """host port -> container port from a service's ``ports:`` list."""
    out: dict[int, int] = {}
    for item in svc.get("ports") or []:
        if isinstance(item, dict):
            with contextlib.suppress(TypeError, ValueError):
                out[int(item.get("published", item.get("target")))] = int(item.get("target"))
            continue
        text = interpolate(str(item), env).split("/")[0]
        parts = text.split(":")
        try:
            if len(parts) == 1:
                out[int(parts[0])] = int(parts[0])
            else:
                out[int(parts[-2])] = int(parts[-1])
        except ValueError:
            continue
    return out


def stack_spec(
    name: str,
    compose: dict,
    *,
    env: dict[str, str],
    env_files: dict[str, dict[str, str]] | None = None,
    data_root: str,
    image_map: dict[str, str] | None = None,
) -> tuple[dict, dict[str, str]]:
    """One pod for a host-networked compose stack, and each service's image.

    Services under a ``profiles:`` key are left out (compose starts them
    only when the profile is asked for). *image_map* rewrites image refs:
    the CIT compose file names the local ``:build`` tag, which on a cluster
    node is the registry's image of the variant under test. Returns the
    spec and a map of service name to image ref.
    """
    env_files = env_files or {}
    image_map = image_map or {}
    containers = []
    volumes = []
    images: dict[str, str] = {}
    annotations: dict[str, str] = {}
    for svc_name, svc in (compose.get("services") or {}).items():
        if svc.get("profiles"):
            continue
        container, vols, image = _service_container(
            svc_name, svc, env=env, env_files=env_files, data_root=data_root, image_map=image_map,
        )
        images[svc_name] = image
        containers.append(container)
        volumes += vols
        for k, v in (svc.get("annotations") or {}).items():
            annotations[str(k)] = str(v)
    return _pod(name, containers, volumes, annotations, name), images


def shares_host_network(compose: dict) -> bool:
    """Every service of the stack runs on the host's network (localhost
    between them), so one pod with a shared network namespace is its shape."""
    svcs = [s for s in (compose.get("services") or {}).values() if not s.get("profiles")]
    return bool(svcs) and all(s.get("network_mode") == "host" for s in svcs)


def stack_specs_per_service(
    name: str,
    compose: dict,
    *,
    env: dict[str, str],
    env_files: dict[str, dict[str, str]] | None = None,
    data_root: str,
    image_map: dict[str, str] | None = None,
) -> tuple[list[dict], dict[str, str], dict[str, dict[int, int]]]:
    """A pod and a Service per compose service, for stacks whose services
    have networks of their own (bridge networking, published ports): the
    Service carries the compose name, so ``db:5432`` resolves as written.
    Returns the specs (pods and services), service -> image, and
    service -> {host port: container port}.
    """
    env_files = env_files or {}
    image_map = image_map or {}
    specs: list[dict] = []
    images: dict[str, str] = {}
    ports: dict[str, dict[int, int]] = {}
    for svc_name, svc in (compose.get("services") or {}).items():
        if svc.get("profiles"):
            continue
        container, vols, image = _service_container(
            svc_name, svc, env=env, env_files=env_files, data_root=data_root, image_map=image_map,
        )
        images[svc_name] = image
        ports[svc_name] = published_ports(svc, env)
        annotations = {str(k): str(v) for k, v in (svc.get("annotations") or {}).items()}
        pod = _pod(pod_name(f"{name}-{svc_name}"), [container], vols, annotations, name)
        pod["metadata"]["labels"]["dbuild-cit/service"] = pod_name(svc_name)
        pod["spec"]["hostname"] = pod_name(svc_name)
        specs.append(pod)
        cports = sorted(set(ports[svc_name].values()))
        if cports:
            specs.append({
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": pod_name(svc_name), "namespace": NAMESPACE,
                             "labels": {"app.kubernetes.io/managed-by": "dbuild-cit", "dbuild-cit/stack": name}},
                "spec": {
                    "selector": {"dbuild-cit/service": pod_name(svc_name), "dbuild-cit/stack": name},
                    "ports": [{"name": f"p{p}", "port": p, "targetPort": p} for p in cports],
                },
            })
    return specs, images, ports


def apply_all(specs: list[dict]) -> None:
    kubectl("apply", "-f", "-", stdin=json.dumps({"apiVersion": "v1", "kind": "List", "items": specs}))


def delete_stack(stack: str) -> None:
    """Remove every pod and Service of a stack, forced (see delete)."""
    kubectl("delete", "pod,service", "-l", f"dbuild-cit/stack={stack}", "--ignore-not-found",
            "--force", "--grace-period=0", "--wait=false", check=False)


def stack_pods(stack: str) -> list[str]:
    res = kubectl("get", "pods", "-l", f"dbuild-cit/stack={stack}", "-o", "name", check=False, quiet=True)
    return [line.split("/", 1)[1] for line in (res.stdout or "").split() if "/" in line]


def stack_logs_all(stack: str, *, quiet: bool = False) -> str:
    res = kubectl("logs", "-l", f"dbuild-cit/stack={stack}", "--all-containers", "--prefix",
                  "--tail=-1", check=False, quiet=quiet)
    return (res.stdout or "") + (res.stderr if res.returncode != 0 else "")


def stack_running_all(stack: str) -> bool:
    pods = stack_pods(stack)
    return bool(pods) and all(stack_running(p) for p in pods)


def stack_env(repo_dir: str) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """The stack's variables: ``.env`` if the repo has one, else ``example.env``
    (what the README tells a user to copy). Also every env_file by name."""
    env: dict[str, str] = {}
    files: dict[str, dict[str, str]] = {}
    for fname in ("example.env", ".env"):
        path = os.path.join(repo_dir, fname)
        if os.path.exists(path):
            with open(path) as fh:
                parsed = parse_env_file(fh.read())
            files[fname] = parsed
            env.update(parsed)
    if ".env" not in files and "example.env" in files:
        files[".env"] = files["example.env"]
    return env, files


def stack_logs(name: str, *, quiet: bool = False) -> str:
    res = kubectl("logs", name, "--all-containers", "--prefix", check=False, quiet=quiet)
    return (res.stdout or "") + (res.stderr if res.returncode != 0 else "")


def stack_running(name: str) -> bool:
    """Every container of the pod is running (none waiting or terminated)."""
    pod = get_pod(name)
    if pod is None:
        return False
    statuses = (pod.get("status") or {}).get("containerStatuses") or []
    return bool(statuses) and all("running" in (cs.get("state") or {}) for cs in statuses)
