"""Tests for dbuild.appjail helpers."""

import subprocess
from unittest.mock import patch

from dbuild import appjail


def test_run_sets_pager_to_cat():
    """`appjail logs read` pages through $PAGER; cat keeps it from blocking."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch("dbuild.appjail.subprocess.run", side_effect=fake_run),
        patch("dbuild.appjail._priv_prefix", return_value=[]),
        patch.dict("os.environ", {"PAGER": "less"}),
    ):
        appjail._run(["appjail", "logs", "read", "jails/x/container/y.log"])

    assert seen["cmd"][0] == "appjail"
    assert seen["env"]["PAGER"] == "cat"
