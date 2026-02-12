"""Unit tests for dbuild.podman helper functions."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from dbuild import podman


class TestPrivilegeEscalation(unittest.TestCase):
    """Tests for _needs_privilege / _priv_prefix."""

    @patch("dbuild.podman.os.getuid", return_value=0)
    def test_root_no_privilege(self, _mock_uid):
        self.assertFalse(podman._needs_privilege())
        self.assertEqual(podman._priv_prefix(), [])

    @patch("dbuild.podman.shutil.which", side_effect=lambda x: x if x == "doas" else None)
    @patch("dbuild.podman.os.getuid", return_value=1000)
    def test_non_root_with_doas(self, _mock_uid, _mock_which):
        self.assertTrue(podman._needs_privilege())
        self.assertEqual(podman._priv_prefix(), ["doas"])

    @patch("dbuild.podman.shutil.which", side_effect=lambda x: x if x == "sudo" else None)
    @patch("dbuild.podman.os.getuid", return_value=1000)
    def test_non_root_with_sudo(self, _mock_uid, _mock_which):
        self.assertTrue(podman._needs_privilege())
        self.assertEqual(podman._priv_prefix(), ["sudo"])

    @patch("dbuild.podman.shutil.which", return_value=None)
    @patch("dbuild.podman.os.getuid", return_value=1000)
    def test_non_root_no_escalation_tool(self, _mock_uid, _mock_which):
        self.assertTrue(podman._needs_privilege())
        self.assertEqual(podman._priv_prefix(), [])


class TestRun(unittest.TestCase):
    """Tests for the _run() internal helper."""

    @patch("dbuild.podman._priv_prefix", return_value=[])
    @patch("dbuild.podman.subprocess.run")
    def test_run_captures_output(self, mock_run, _mock_priv):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "ps"], returncode=0, stdout="ok\n", stderr=""
        )
        result = podman._run(["podman", "ps"])
        self.assertEqual(result.stdout, "ok\n")
        mock_run.assert_called_once()
        # Should have capture_output=True by default
        call_kwargs = mock_run.call_args
        self.assertTrue(call_kwargs.kwargs.get("capture_output", False))

    @patch("dbuild.podman._priv_prefix", return_value=[])
    @patch("dbuild.podman.subprocess.run")
    def test_run_raises_on_failure(self, mock_run, _mock_priv):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "fail"], returncode=1, stdout="", stderr="error msg"
        )
        with self.assertRaises(podman.PodmanError) as ctx:
            podman._run(["podman", "fail"])
        self.assertIn("error msg", str(ctx.exception))
        self.assertEqual(ctx.exception.returncode, 1)

    @patch("dbuild.podman._priv_prefix", return_value=[])
    @patch("dbuild.podman.subprocess.run")
    def test_run_check_false_no_raise(self, mock_run, _mock_priv):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "fail"], returncode=1, stdout="", stderr="err"
        )
        result = podman._run(["podman", "fail"], check=False)
        self.assertEqual(result.returncode, 1)

    @patch("dbuild.podman._priv_prefix", return_value=["doas"])
    @patch("dbuild.podman.subprocess.run")
    def test_run_prepends_priv_prefix(self, mock_run, _mock_priv):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["doas", "podman", "ps"], returncode=0, stdout="", stderr=""
        )
        podman._run(["podman", "ps"])
        actual_cmd = mock_run.call_args[0][0]
        self.assertEqual(actual_cmd[:2], ["doas", "podman"])

    @patch("dbuild.podman._priv_prefix", return_value=[])
    @patch("dbuild.podman.subprocess.run")
    def test_run_quiet_suppresses_log(self, mock_run, _mock_priv):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "ps"], returncode=0, stdout="", stderr=""
        )
        with patch("dbuild.podman.log") as mock_log:
            podman._run(["podman", "ps"], quiet=True)
            mock_log.info.assert_not_called()

    @patch("dbuild.podman._priv_prefix", return_value=[])
    @patch("dbuild.podman.subprocess.run")
    def test_run_logs_when_not_quiet(self, mock_run, _mock_priv):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "ps"], returncode=0, stdout="", stderr=""
        )
        with patch("dbuild.podman.log") as mock_log:
            podman._run(["podman", "ps"], quiet=False)
            mock_log.info.assert_called_once()


class TestInspectLabels(unittest.TestCase):
    """Tests for inspect_labels()."""

    @patch("dbuild.podman._run")
    def test_parses_json_labels(self, mock_run):
        labels = {"io.daemonless.port": "7878", "org.freebsd.jail.allow.mlock": "required"}
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(labels), stderr=""
        )
        result = podman.inspect_labels("myimage:latest")
        self.assertEqual(result, labels)

    @patch("dbuild.podman._run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        result = podman.inspect_labels("bad:image")
        self.assertEqual(result, {})

    @patch("dbuild.podman._run")
    def test_returns_empty_on_invalid_json(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        )
        result = podman.inspect_labels("myimage:latest")
        self.assertEqual(result, {})

    @patch("dbuild.podman._run")
    def test_returns_empty_on_null(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="null", stderr=""
        )
        result = podman.inspect_labels("myimage:latest")
        self.assertEqual(result, {})


class TestRunDetached(unittest.TestCase):
    """Tests for run_detached()."""

    @patch("dbuild.podman._run")
    def test_basic_run(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="abc123\n", stderr=""
        )
        cid = podman.run_detached("myimage:latest", name="test-ctr")
        self.assertEqual(cid, "abc123")
        cmd = mock_run.call_args[0][0]
        self.assertIn("-d", cmd)
        self.assertIn("--name", cmd)
        self.assertIn("test-ctr", cmd)
        self.assertIn("myimage:latest", cmd)

    @patch("dbuild.podman._run")
    def test_with_annotations(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="abc123\n", stderr=""
        )
        podman.run_detached(
            "myimage:latest",
            name="test-ctr",
            annotations={"org.freebsd.jail.allow.mlock": "true"},
        )
        cmd = mock_run.call_args[0][0]
        self.assertIn("--annotation", cmd)
        idx = cmd.index("--annotation")
        self.assertEqual(cmd[idx + 1], "org.freebsd.jail.allow.mlock=true")


class TestContainerRunning(unittest.TestCase):
    """Tests for container_running()."""

    @patch("dbuild.podman._run")
    def test_running_returns_true(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="abc123\n", stderr=""
        )
        self.assertTrue(podman.container_running("test-ctr"))

    @patch("dbuild.podman._run")
    def test_not_running_returns_false(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        self.assertFalse(podman.container_running("test-ctr"))


class TestLogs(unittest.TestCase):
    """Tests for logs()."""

    @patch("dbuild.podman._run")
    def test_merges_stdout_and_stderr(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="out\n", stderr="err\n"
        )
        result = podman.logs("test-ctr")
        self.assertIn("out", result)
        self.assertIn("err", result)

    @patch("dbuild.podman._run")
    def test_quiet_passed_through(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        podman.logs("test-ctr", quiet=True)
        self.assertTrue(mock_run.call_args.kwargs.get("quiet", False))


class TestExecIn(unittest.TestCase):
    """Tests for exec_in()."""

    @patch("dbuild.podman._run")
    def test_exec_list_cmd(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok\n", stderr=""
        )
        podman.exec_in("test-ctr", ["/bin/sh", "-c", "echo ok"])
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd, ["podman", "exec", "test-ctr", "/bin/sh", "-c", "echo ok"])

    @patch("dbuild.podman._run")
    def test_exec_string_cmd(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok\n", stderr=""
        )
        podman.exec_in("test-ctr", "echo ok")
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd, ["podman", "exec", "test-ctr", "/bin/sh", "-c", "echo ok"])


class TestStopRm(unittest.TestCase):
    """Tests for stop() and rm()."""

    @patch("dbuild.podman._run")
    def test_stop(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        podman.stop("test-ctr")
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd, ["podman", "stop", "test-ctr"])

    @patch("dbuild.podman._run")
    def test_rm_force(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        podman.rm("test-ctr", force=True)
        cmd = mock_run.call_args[0][0]
        self.assertIn("-f", cmd)

    @patch("dbuild.podman._run")
    def test_rm_no_force(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        podman.rm("test-ctr", force=False)
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("-f", cmd)


if __name__ == "__main__":
    unittest.main()
