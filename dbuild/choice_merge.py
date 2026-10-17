"""One page for every way of answering a stack's choices.

The docs used to write a full set of files per combination; ten on/off
choices made 2048 of them. Here the combinations' markdown is merged into one
superset of lines, each line tagged with the options that keep it, and the
site's deploy-choices.js shows the lines the visitor's picks keep.

The merge is proven, not assumed: every combination is rebuilt from the
superset and compared with its own render. The few that differ (all ten apps
off: ``services: {}``) are kept whole and shown instead.

A blank line between optional parts of a file (two services) belongs to
neither: it is a separator, shown when something shows on both sides of it.
deploy-choices.js implements ``visible`` again; keep the two the same.
"""

from __future__ import annotations

import difflib
import html
import json
import re
from dataclasses import dataclass, field

# Combinations kept whole because the merge cannot express them. Past this,
# the merge is not worth it and the caller falls back.
MAX_EXCEPTIONS = 8

_FENCE = re.compile(r"^(\s*)(```+|~~~+)")


@dataclass
class Line:
    text: str
    # choice index -> options that keep the line; a choice absent keeps it
    # under any option.
    allowed: dict[int, set[str]] = field(default_factory=dict)
    sep: bool = False
    code: bool = False

    def kept(self, picks: tuple[str, ...]) -> bool:
        return all(picks[i] in opts for i, opts in self.allowed.items())


def _mark_code(lines: list[Line]) -> None:
    fence = None
    for ln in lines:
        m = _FENCE.match(ln.text)
        if fence is None and m:
            fence = m.group(2)
        elif fence is not None and m and m.group(2).startswith(fence) and not ln.text.strip()[len(m.group(2)):]:
            fence = None
        else:
            ln.code = fence is not None


def visible(lines: list[Line], picks: tuple[str, ...]) -> list[bool]:
    """Which lines the picks show. A separator shows when each side, up to
    the nearest line every pick shows, has a line shown (or is that line),
    and not right after another blank."""
    base = [not ln.sep and ln.kept(picks) for ln in lines]

    def side(k: int, step: int) -> bool:
        saw = False
        j = k + step
        while 0 <= j < len(lines):
            ln = lines[j]
            if not ln.sep and ln.text.strip():
                if not ln.allowed:
                    return not saw
                saw = True
                if base[j]:
                    return True
            j += step
        return False

    out = [base[k] or (ln.sep and side(k, -1) and side(k, 1)) for k, ln in enumerate(lines)]
    last_blank = True
    for k, ln in enumerate(lines):
        if not out[k]:
            continue
        blank = not ln.text.strip()
        if ln.sep and last_blank:
            out[k] = False
            continue
        last_blank = blank
    return out


def _norm(lines: list[str], code: list[bool]) -> list[str]:
    """What a render can tell apart. Prose blank lines only separate
    paragraphs (the page keeps them all); a code block's leading and
    trailing blank lines are dropped by the highlighter."""
    out: list[str] = []
    block: list[str] = []
    for t, c in zip(lines, code, strict=True):
        if c:
            block.append(t)
            continue
        if block:
            out.extend(_trim_blank(block))
            block = []
        if t.strip():
            out.append(t)
    out.extend(_trim_blank(block))
    return out


def _trim_blank(block: list[str]) -> list[str]:
    a, b = 0, len(block)
    while a < b and not block[a].strip():
        a += 1
    while b > a and not block[b - 1].strip():
        b -= 1
    return block[a:b]


def _code_flags(text: list[str]) -> list[bool]:
    ls = [Line(t) for t in text]
    _mark_code(ls)
    return [ln.code for ln in ls]


def _indent(chunk: list[str]) -> int:
    for t in chunk:
        if t.strip():
            return len(t) - len(t.lstrip())
    return 0


def _slid(ops: list[tuple], a: list[str], b: list[str]) -> list[tuple]:
    """difflib's opcodes with each pure delete or insert slid, among the
    places it could equally be, to where it starts least indented. Services
    all end in `restart: always`; unslid, dropping one took the line from
    its neighbour, and the merge tagged it with the wrong choice."""
    out = []
    for n, (tag, a1, a2, b1, b2) in enumerate(ops):
        if tag in ("delete", "insert"):
            seq, lo, hi = (a, a1, a2) if tag == "delete" else (b, b1, b2)
            prev_eq = ops[n - 1][0] == "equal" if n else False
            next_eq = ops[n + 1][0] == "equal" if n + 1 < len(ops) else False
            room_up = (ops[n - 1][2] - ops[n - 1][1]) if prev_eq else 0
            room_down = (ops[n + 1][2] - ops[n + 1][1]) if next_eq else 0
            best, shift = None, 0
            for d in range(-room_up, room_down + 1):
                # Moving by d swaps the lines it uncovers for the ones it
                # covers: they must read the same.
                ok = all(seq[lo + k] == seq[hi + k] for k in (range(d, 0) if d < 0 else range(d)))
                if ok and (best is None or _indent(seq[lo + d:hi + d]) < best):
                    best, shift = _indent(seq[lo + d:hi + d]), d
            if tag == "delete":
                out.append((tag, a1 + shift, a2 + shift, b1, b2))
            else:
                out.append((tag, a1 + shift, a2 + shift, b1 + shift, b2 + shift))
        else:
            out.append((tag, a1, a2, b1, b2))
    return out


def merge(variants: dict[tuple[str, ...], list[str]], default: tuple[str, ...],
          options: list[list[str]]) -> tuple[list[Line], list[tuple[str, ...]]] | None:
    """The superset of lines for ``variants`` (picks -> lines), and the picks
    it cannot rebuild. None when there are more of those than MAX_EXCEPTIONS.

    Built from the default and each one-choice deviation from it; the other
    combinations only check the result.
    """
    lines = [Line(t) for t in variants[default]]
    for i, opts in enumerate(options):
        for o in opts:
            if o == default[i]:
                continue
            picks = (*default[:i], o, *default[i + 1:])
            target = variants.get(picks)
            if target is None:
                return None
            shown = [k for k, ln in enumerate(lines) if ln.kept(picks)]
            seen = [lines[k].text for k in shown]
            sm = difflib.SequenceMatcher(None, seen, target, autojunk=False)
            # Back to front, so an insert does not shift the indices still to do.
            for tag, a1, a2, b1, b2 in reversed(_slid(sm.get_opcodes(), seen, target)):
                if tag == "equal":
                    continue
                for k in shown[a1:a2]:
                    lines[k].allowed.setdefault(i, set(opts)).discard(o)
                if b2 > b1:
                    at = shown[a2 - 1] + 1 if a2 > a1 else (shown[a1] if a1 < len(shown) else len(lines))
                    lines[at:at] = [Line(t, {i: {o}}) for t in target[b1:b2]]
    for ln in lines:
        ln.allowed = {i: s for i, s in ln.allowed.items() if s != set(options[i])}
    _mark_code(lines)
    want = {p: _norm(t, _code_flags(t)) for p, t in variants.items()}

    def misses(limit: int) -> list[tuple[str, ...]]:
        out = []
        for p in variants:
            shown = visible(lines, p)
            got = _norm([ln.text for ln, v in zip(lines, shown, strict=True) if v],
                        [ln.code for ln, v in zip(lines, shown, strict=True) if v])
            if got != want[p]:
                out.append(p)
                if len(out) > limit:
                    break
        return out

    # A blank line in code that only some picks have is a separator (between
    # two services) or simply part of what an option adds (an .env empty but
    # for one option). Separators first; then each one, in turn, back to its
    # options where that rebuilds more combinations.
    seps = [ln for ln in lines if ln.code and not ln.text.strip() and ln.allowed]
    kept = {id(ln): ln.allowed for ln in seps}
    for ln in seps:
        ln.sep, ln.allowed = True, {}
    for ln in lines:
        if not ln.code and not ln.text.strip():
            ln.allowed = {}
    exceptions = misses(len(variants))
    for ln in seps:
        if not exceptions:
            break
        ln.sep, ln.allowed = False, kept[id(ln)]
        trial = misses(len(exceptions) - 1)
        if len(trial) < len(exceptions):
            exceptions = trial
        else:
            ln.sep, ln.allowed = True, {}
    if len(exceptions) > MAX_EXCEPTIONS:
        return None
    return lines, exceptions


def to_markdown(lines: list[Line], choice_ids: list[str]) -> tuple[str, dict]:
    """The superset as markdown for the site, and what the page script reads
    (``data-when`` on the block): ``conds`` by id, ``lines`` one entry per
    superset line ([cond id or "", separator?, blank?]), and ``blocks``, each code
    block's line numbers in that list. A varying line outside code is a
    ``<span data-l=N>``; a code block is named by ``data-wb``.
    """
    conds: dict[str, dict[str, list[str]]] = {}
    ids: dict[str, str] = {}

    def cond_id(ln: Line) -> str:
        if not ln.allowed:
            return ""
        c = {choice_ids[i]: sorted(s) for i, s in sorted(ln.allowed.items())}
        key = json.dumps(c, sort_keys=True)
        if key not in ids:
            ids[key] = f"c{len(ids)}"
            conds[ids[key]] = c
        return ids[key]

    entries: list[list] = []
    blocks: dict[str, list[int]] = {}
    out: list[str] = []
    block: list[int] | None = None
    for k, ln in enumerate(lines):
        cid = cond_id(ln)
        entries.append([cid, 1 if ln.sep else 0, 0 if ln.text.strip() else 1])
        m = _FENCE.match(ln.text)
        if block is None and m and not ln.code:
            bid = f"b{len(blocks)}"
            block = blocks.setdefault(bid, [])
            text = ln.text.rstrip()
            attr = f'data-wb="{bid}" data-l="{k}"'
            text = text[:-1].rstrip() + f" {attr} }}" if text.endswith("}") and "{" in text else f"{text} {{ {attr} }}"
            out.append(text)
        elif block is not None and ln.code:
            block.append(k)
            out.append(ln.text)
        elif block is not None:
            # The highlighter drops a block's leading and trailing blank
            # lines: they have no line on the page to show or hide.
            while block and not lines[block[0]].text.strip():
                block.pop(0)
            while block and not lines[block[-1]].text.strip():
                block.pop()
            block = None
            out.append(ln.text)
        elif cid and ln.text.strip():
            indent = len(ln.text) - len(ln.text.lstrip())
            out.append(f'{ln.text[:indent]}<span data-l="{k}">{ln.text[indent:]}</span>')
        else:
            out.append(ln.text)
    return "\n".join(out), {"conds": conds, "lines": entries, "blocks": blocks}


def when_attr(data: dict) -> str:
    """``data`` as an HTML attribute value."""
    return html.escape(json.dumps(data, separators=(",", ":")), quote=True)
