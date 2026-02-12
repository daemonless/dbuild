"""Unit tests for dbuild.log."""

from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import patch

from dbuild import log


class TestFormatElapsed(unittest.TestCase):
    """Tests for _format_elapsed()."""

    def test_seconds(self):
        self.assertEqual(log._format_elapsed(5.0), "5.0s")

    def test_seconds_fractional(self):
        self.assertEqual(log._format_elapsed(12.3), "12.3s")

    def test_under_one_second(self):
        self.assertEqual(log._format_elapsed(0.5), "0.5s")

    def test_exactly_60(self):
        self.assertEqual(log._format_elapsed(60.0), "1m0.0s")

    def test_minutes_and_seconds(self):
        self.assertEqual(log._format_elapsed(90.5), "1m30.5s")

    def test_large_value(self):
        self.assertEqual(log._format_elapsed(3661.2), "61m1.2s")

    def test_zero(self):
        self.assertEqual(log._format_elapsed(0.0), "0.0s")


class TestColorDetection(unittest.TestCase):
    """Tests for color detection."""

    def test_set_color_true(self):
        original = log._use_color
        try:
            log.set_color(True)
            self.assertTrue(log._color_enabled())
        finally:
            log._use_color = original

    def test_set_color_false(self):
        original = log._use_color
        try:
            log.set_color(False)
            self.assertFalse(log._color_enabled())
        finally:
            log._use_color = original

    def test_c_returns_empty_when_no_color(self):
        original = log._use_color
        try:
            log.set_color(False)
            self.assertEqual(log._c("bold"), "")
            self.assertEqual(log._c("red"), "")
        finally:
            log._use_color = original

    def test_c_returns_ansi_when_color(self):
        original = log._use_color
        try:
            log.set_color(True)
            self.assertIn("\033[", log._c("bold"))
            self.assertIn("\033[", log._c("red"))
        finally:
            log._use_color = original


class TestOutput(unittest.TestCase):
    """Tests for output functions."""

    def setUp(self):
        self._original_color = log._use_color
        log.set_color(False)

    def tearDown(self):
        log._use_color = self._original_color

    def test_step_output(self):
        buf = io.StringIO()
        with patch.object(sys, "stdout", buf):
            log.step("Building")
        output = buf.getvalue()
        self.assertIn("Building", output)
        self.assertIn("===", output)

    def test_info_output(self):
        buf = io.StringIO()
        with patch.object(sys, "stdout", buf):
            log.info("test message")
        output = buf.getvalue()
        self.assertIn("[info]", output)
        self.assertIn("test message", output)

    def test_warn_output(self):
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            log.warn("warning message")
        output = buf.getvalue()
        self.assertIn("[warn]", output)
        self.assertIn("warning message", output)

    def test_error_output(self):
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            log.error("error message")
        output = buf.getvalue()
        self.assertIn("[error]", output)
        self.assertIn("error message", output)

    def test_success_output(self):
        buf = io.StringIO()
        with patch.object(sys, "stdout", buf):
            log.success("done")
        output = buf.getvalue()
        self.assertIn("[ok]", output)
        self.assertIn("done", output)


class TestTimers(unittest.TestCase):
    """Tests for timer_start/timer_stop."""

    def setUp(self):
        self._original_color = log._use_color
        log.set_color(False)

    def tearDown(self):
        log._use_color = self._original_color

    def test_timer_round_trip(self):
        log.timer_start("test-timer")
        buf = io.StringIO()
        with patch.object(sys, "stdout", buf):
            result = log.timer_stop("test-timer")
        self.assertTrue(result.endswith("s"))
        self.assertIn("test-timer", buf.getvalue())

    def test_timer_stop_unknown(self):
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            result = log.timer_stop("nonexistent")
        self.assertEqual(result, "??s")
        self.assertIn("unknown timer", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
