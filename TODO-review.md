# dbuild — Code Review TODO

Deep-dive review findings, ordered by severity. Check off as addressed.
Line numbers are approximate (as of review date 2026-06-02).

---

## 🔴 Correctness bugs

- [x] **1. aarch64 multi-arch manifests are broken (suffix mismatch)** — FIXED:
  added `config.arch_tag_suffix` as the single source of truth; push/manifest/detect/sbom
  now all use it. manifest no longer looks for `-arm64`. Tests updated (push & manifest
  agree on `latest-aarch64`). 328 pass.
  - `push.py:27` `_arch_suffix` → `-aarch64`
  - `detect.py:23` `_VM_ARCH_MAP` → `-aarch64`
  - `manifest.py:28` `_ARCH_TAG_SUFFIX` → `-arm64`  ← the odd one out
  - Effect: `push` creates `…:latest-aarch64`, but `dbuild manifest` searches for
    `…:latest-arm64`, doesn't find it, warns "Not found", and silently produces a
    single-arch manifest. riscv64 matches; only aarch64 is wrong.
  - Fix: unify on one convention (`-aarch64`, used by 2 of 3) in a single shared
    helper. Consider a single source of truth like `VALID_CATEGORIES`.

- [ ] **2. SIGTERM during `screenshot`/`baseline` crashes emergency cleanup, leaks ALL containers**
  - `_cleanup_targets` is consumed as a 3-tuple `(compose_file, backend, cname)`,
    unpacked OUTSIDE the per-entry try (`test.py:45`).
  - `run_screenshot` registers a 2-tuple with wrong element types (`test.py:834`):
    second element is `container_name` (str), not a backend; tuple has 2 elements.
  - Effect: a SIGTERM/TaskStop mid-screenshot raises `ValueError` on unpack →
    `_emergency_cleanup` aborts → every registered container leaks, not just the
    screenshot one. Normal `finally` path only works by identity removal.
  - Fix: make `run_screenshot` register the same 3-tuple shape as the rest of the file.

- [ ] **3. `prune` can't see leaked `-rechown` containers or PUID volumes**
  - `cname2 = f"{cname}-rechown"` (`test.py:652`) ends with `-rechown`, but
    `_collect_cit_containers` filters on `endswith(f"-{image_name}")` (`prune.py:25`).
    A crash between the two PUID deploys orphans a container prune never collects.
  - `prune` has no volume collector; PUID test creates `cit-puid-<pid>-<img>-<tag>`
    named volumes that accumulate forever.
  - Fix: broaden the container filter to also catch `-rechown`; add a
    `podman volume ls` collector keyed on `cit-puid-*`.

- [ ] **4. compose + auto-resolved `shell` mode → AssertionError**
  - Shell test + its `if mode == "shell": return 0` early exit are gated behind
    `if not compose_mode` (`test.py:474`). A compose image with no port/health/baseline
    resolves to `shell`, falls through to `assert port is not None` (`test.py:496`) →
    uncaught AssertionError.
  - Fix: handle compose+shell explicitly, or replace the assert with a real error.

---

## 🟡 Minor logic bugs / dead code

- [ ] **5. `_downgrade_mode` dead branch** (`test.py:173`): `if health or port: return "health"`
  makes the following `if port: return "port"` unreachable. A screenshot image with only
  a port (no health) downgrades to `health` instead of `port`. Harmless today, intent lost.

- [ ] **6. Redundant BASE_VERSION injection** (`build.py:76-77`): the explicit
  `if "BASE_VERSION" in variant.args` block is fully subsumed by the `setdefault` loop
  just below it. Dead code.

- [ ] **7. Wrong fallback in `_list_local_images`** (`detect.py:141`):
  `img.get("Names") or img.get("History")` — `History` is image history, not names.
  Should just be `Names`.

---

## 🟠 Architecture / consistency

- [ ] **8. Layering violation — "only podman.py runs podman" broken in 4 places.**
  `manifest.py`, `sbom.py`, `registry/generic.py`, `ci_test.py` reach into the private
  `podman._priv_prefix()` and assemble raw `subprocess.run([... podman/skopeo/trivy ...])`
  calls. Privilege escalation, logging, and error handling get reimplemented
  inconsistently (manifest raises `RuntimeError`, podman raises `PodmanError`).
  - Fix: add `podman.manifest_*` / `skopeo_*` wrappers (or make `_priv_prefix` public and
    own the decision), but stop contradicting podman.py's own "ZERO business logic" docstring.

- [ ] **9. Inconsistent return contract.** `build.run` / `push.run` return `None`;
  `test`/`sbom`/etc. return `int`. Every dispatcher defensively writes `if rc and rc != 0`.
  - Fix: make all `run()` entry points return `int`.

- [ ] **10. `--push` / `--registry` only work BEFORE the subcommand.**
  `--variant`/`--arch` are re-registered on each subparser (`cli.py:95`), but `--push`
  and `--registry` are top-level only. `dbuild build --push` is an argparse error;
  only `dbuild --push build` works — opposite of what the help text implies.
  - Fix: add them to subparsers too, or document the ordering.

- [ ] **11. Duplicated `_priv_prefix`** in `podman.py:42` and `appjail.py:31`
  (podman has an extra `_needs_privilege` indirection the other lacks). Factor into one helper.

- [ ] **12. `backend` name reused for two types** in `test.run`
  (`test.py:1070` CI object, `:1081` str arg). Works, but confusing — rename one.

- [ ] **13. `_DISPATCHERS: dict[str, callable]`** (`cli.py:477`) — `callable` is the
  builtin predicate, not a type. Should be `Callable`.

---

## ⚠️ Footguns

- [ ] **14. `ci-prepare` wipes ALL container storage every run.**
  `cleanup_containers()` (`prepare.py:88`) does
  `rm -rf /var/db/containers /var/lib/containers` unconditionally, so `ci-run --prepare`
  nukes the entire image/build cache before every pipeline. Fine on an ephemeral VM;
  a cache-destroying landmine on a persistent host (dbuild runs directly on saturn).
  - Fix: louder warning, or gate behind an explicit `--clean` flag.

- [ ] **15. `_wait_for_ready` returns `True` on timeout** (`test.py:253`) — intentional;
  the ready-signal timeout is effectively advisory (port/health catches real failures).
  Note only.

- [ ] **16. Import-time side effect in `test.py`.** Importing the module installs a
  process-global `SIGTERM` handler (`test.py:61`) — the one module mutating global process
  state on import, awkward next to `config.py`'s "ZERO side effects". Consider moving into
  `run()`.

---

## Suggested order

1. #1 (aarch64 suffix) — silently breaks a headline feature.
2. #2 (screenshot cleanup tuple) — leaks resources on interrupt.
3. #3 (prune gaps) — leaks accumulate on the persistent host specifically.
4. Then #8 (layering) + #9 (return types) as a consolidation pass.
