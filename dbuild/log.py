"""Logging module for dbuild.

Provides colored output, step headers, and timing support.
No external dependencies -- stdlib only.
"""

from __future__ import annotations

import sys
import time

# ANSI color codes -- only used when stdout is a terminal.
_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}

# Colors cycled across parallel variant tags (avoid red — that's errors)
_TAG_COLORS = ["cyan", "yellow", "green", "blue", "magenta"]

# Mutable module-level state — intentionally not ALL_CAPS constants.
_use_color: bool | None = None  # pylint: disable=invalid-name
_verbose: bool = False           # pylint: disable=invalid-name


def _color_enabled() -> bool:
    global _use_color  # pylint: disable=global-statement
    if _use_color is None:
        _use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    return _use_color


def set_color(enabled: bool) -> None:
    """Override automatic color detection."""
    global _use_color  # pylint: disable=global-statement
    _use_color = enabled


def set_verbose(enabled: bool) -> None:
    """Enable or disable debug-level output."""
    global _verbose  # pylint: disable=global-statement
    _verbose = enabled


def is_verbose() -> bool:
    """Return True if verbose/debug output is enabled."""
    return _verbose


def _c(name: str) -> str:
    """Return the ANSI escape for *name* if color is enabled, else empty string."""
    if _color_enabled():
        return _COLORS.get(name, "")
    return ""


def color_tag(text: str, index: int) -> str:
    """Wrap *text* in a cycling color for parallel variant prefixes."""
    color = _TAG_COLORS[index % len(_TAG_COLORS)]
    return f"{_c(color)}{_c('bold')}{text}{_c('reset')}"


# ── Public API ────────────────────────────────────────────────────────

def step(message: str) -> None:
    """Print a bold step header.  e.g. ``=== Building :latest ===``"""
    sys.stdout.write(
        f"{_c('bold')}{_c('cyan')}=== {message} ==={_c('reset')}\n"
    )
    sys.stdout.flush()


def debug(message: str) -> None:
    """Print only when verbose mode is enabled."""
    if _verbose:
        sys.stdout.write(f"{_c('cyan')}[debug]{_c('reset')} {message}\n")
        sys.stdout.flush()


def plain(message: str) -> None:
    """Print a message with no prefix."""
    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


def info(message: str) -> None:
    """Print an informational message."""
    sys.stdout.write(f"{_c('blue')}[info]{_c('reset')} {message}\n")
    sys.stdout.flush()


def warn(message: str) -> None:
    """Print a warning to stderr."""
    sys.stderr.write(f"{_c('yellow')}[warn]{_c('reset')} {message}\n")
    sys.stderr.flush()


def error(message: str) -> None:
    """Print an error to stderr."""
    sys.stderr.write(f"{_c('red')}[error]{_c('reset')} {message}\n")
    sys.stderr.flush()


def success(message: str) -> None:
    """Print a success message."""
    sys.stdout.write(f"{_c('green')}[ok]{_c('reset')} {message}\n")
    sys.stdout.flush()


# ── Timing helpers ────────────────────────────────────────────────────

_timers: dict[str, float] = {}


def timer_start(name: str) -> None:
    """Start a named timer."""
    _timers[name] = time.monotonic()


def timer_stop(name: str) -> str:
    """Stop a named timer and return a human-readable elapsed string.

    Also prints the elapsed time.  Returns the formatted string for
    callers that want to embed it elsewhere.
    """
    start = _timers.pop(name, None)
    if start is None:
        warn(f"timer_stop called for unknown timer: {name}")
        return "??s"
    elapsed = time.monotonic() - start
    formatted = _format_elapsed(elapsed)
    info(f"{name} completed in {formatted}")
    return formatted


def _format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds) // 60
    secs = seconds - minutes * 60
    return f"{minutes}m{secs:.1f}s"
