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
    "cyan": "\033[36m",
}

_use_color: bool | None = None


def _color_enabled() -> bool:
    global _use_color
    if _use_color is None:
        _use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    return _use_color


def set_color(enabled: bool) -> None:
    """Override automatic color detection."""
    global _use_color
    _use_color = enabled


def _c(name: str) -> str:
    """Return the ANSI escape for *name* if color is enabled, else empty string."""
    if _color_enabled():
        return _COLORS.get(name, "")
    return ""


# ── Public API ────────────────────────────────────────────────────────

def step(message: str) -> None:
    """Print a bold step header.  e.g. ``=== Building :latest ===``"""
    sys.stdout.write(
        f"{_c('bold')}{_c('cyan')}=== {message} ==={_c('reset')}\n"
    )
    sys.stdout.flush()


def info(message: str) -> None:
    sys.stdout.write(f"{_c('blue')}[info]{_c('reset')} {message}\n")
    sys.stdout.flush()


def warn(message: str) -> None:
    sys.stderr.write(f"{_c('yellow')}[warn]{_c('reset')} {message}\n")
    sys.stderr.flush()


def error(message: str) -> None:
    sys.stderr.write(f"{_c('red')}[error]{_c('reset')} {message}\n")
    sys.stderr.flush()


def success(message: str) -> None:
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
