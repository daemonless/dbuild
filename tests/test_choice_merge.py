"""dbuild.choice_merge: every combination's render folded into one block."""

from __future__ import annotations

import itertools
import json
import unittest

from dbuild import choice_merge as cm


def _shown(lines, picks):
    vis = cm.visible(lines, picks)
    return [ln.text for ln, v in zip(lines, vis, strict=True) if v]


def _apps(picks, names):
    """A compose of the apps switched on, a blank line between services."""
    on = [n for n, p in zip(names, picks, strict=True) if p == "on"]
    body = []
    for n in on:
        if body:
            body.append("")
        body += [f"  {n}:", f"    image: {n}", "    restart: always"]
    return ["```yaml", "services:", *body, "```"]


class TestMerge(unittest.TestCase):
    def test_toggles_merge_with_no_combination_kept_whole(self):
        names = ["a", "b", "c", "d"]
        opts = [["on", "off"]] * 4
        variants = {p: _apps(p, names) for p in itertools.product(*opts)}
        lines, exceptions = cm.merge(variants, ("on",) * 4, opts)
        # All off is "services:" and nothing: still the superset's lines.
        self.assertEqual(exceptions, [])
        for p, want in variants.items():
            self.assertEqual(_shown(lines, p), want, p)

    def test_identical_service_tails_do_not_cross_tag(self):
        # Every service ends "restart: always": a naive diff takes the line
        # from the neighbour when one is dropped.
        names = ["a", "b", "c"]
        opts = [["on", "off"]] * 3
        variants = {p: _apps(p, names) for p in itertools.product(*opts)}
        lines, _ = cm.merge(variants, ("on",) * 3, opts)
        tails = [ln.allowed for ln in lines if ln.text == "    restart: always"]
        self.assertEqual(tails, [{0: {"on"}}, {1: {"on"}}, {2: {"on"}}])

    def test_options_that_add_and_replace_lines(self):
        def env(p):
            db = p[0]
            out = ["```env", f"DB_TYPE={db}"]
            if db != "sqlite":
                out += [f"DB_HOST={db}", "DB_PASSWORD="]
            return [*out, "```"]
        opts = [["sqlite", "postgres", "mariadb"]]
        variants = {p: env(p) for p in itertools.product(*opts)}
        lines, exceptions = cm.merge(variants, ("sqlite",), opts)
        self.assertEqual(exceptions, [])
        for p, want in variants.items():
            self.assertEqual(_shown(lines, p), want)

    def test_an_empty_file_for_one_option_is_its_blank_not_a_separator(self):
        def env(p):
            return ["```env", "", "```"] if p[0] == "sqlite" else ["```env", "DB=pg", "", "PASS=", "```"]
        opts = [["sqlite", "postgres"]]
        variants = {p: env(p) for p in itertools.product(*opts)}
        got = cm.merge(variants, ("sqlite",), opts)
        self.assertIsNotNone(got)
        self.assertEqual(got[1], [])

    def test_what_it_cannot_express_is_kept_whole(self):
        # A line that moves with another choice's pick.
        def f(p):
            return ["X", "Y"] if p == ("a", "c") else ["Y", "X"] if p == ("b", "c") else ["Z"]
        opts = [["a", "b"], ["c", "d"]]
        variants = {p: f(p) for p in itertools.product(*opts)}
        got = cm.merge(variants, ("a", "c"), opts)
        self.assertIsNotNone(got)
        for p in got[1]:
            self.assertNotEqual(_shown(got[0], p), variants[p])

    def test_too_many_kept_whole_is_none(self):
        # Two choices that order each other's lines: not per-line at all.
        letters = list("abcdef")
        opts = [letters, letters]
        variants = {p: sorted(p) for p in itertools.product(*opts)}
        self.assertIsNone(cm.merge(variants, ("a", "a"), opts))


class TestMarkdown(unittest.TestCase):
    def test_spans_blocks_and_conditions(self):
        names = ["a", "b"]
        opts = [["on", "off"]] * 2

        def render(p):
            return ["intro", *[f"`{n}`" for n, x in zip(names, p, strict=True) if x == "on"], "", *_apps(p, names)]
        variants = {p: render(p) for p in itertools.product(*opts)}
        lines, _ = cm.merge(variants, ("on", "on"), opts)
        md, data = cm.to_markdown(lines, names)
        self.assertIn('<span data-l="1">`a`</span>', md)
        self.assertIn('```yaml { data-wb="b0" data-l="', md)
        self.assertEqual(len(data["conds"]), 2)
        self.assertEqual(len(data["lines"]), len(lines))
        # The block's line list skips the fences.
        self.assertTrue(all(lines[k].code for k in data["blocks"]["b0"]))
        json.loads(cm.when_attr(data).replace("&quot;", '"'))
