"""Tests for dbuild.appjail helpers."""

import subprocess
from unittest.mock import patch

from dbuild import appjail


def test_run_sets_pager_to_cat():
    """`appjail logs read` pages through $PAGER; cat keeps it from blocking."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch("dbuild.appjail.subprocess.run", side_effect=fake_run),
        patch("dbuild.appjail._priv_prefix", return_value=["doas"]),
    ):
        appjail._run(["appjail", "logs", "read", "jails/x/container/y.log"])

    # env(1) sits between doas and appjail, so doas cannot drop the variable.
    assert seen["cmd"][:4] == ["doas", "env", "PAGER=cat", "appjail"]
