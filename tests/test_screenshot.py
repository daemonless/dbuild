"""The screenshot browser treats the container's plain-http origin as secure."""

import pytest

pytest.importorskip("selenium")

from dbuild.screenshot import secure_origin


def test_secure_origin_for_the_container_address():
    # Actual needs SharedArrayBuffer, which the browser only gives a secure
    # context; http://<container ip> is not one.
    assert secure_origin("http://10.88.0.26:5006/") == "http://10.88.0.26:5006"
    assert secure_origin("http://10.88.0.26:5006/some/path?x=1") == "http://10.88.0.26:5006"


def test_secure_origin_leaves_https_and_garbage_alone():
    assert secure_origin("https://10.88.0.26:8443/") == ""
    assert secure_origin("not a url") == ""


def test_secure_context_read_from_config():
    from dbuild.config import _parse_test_config

    assert _parse_test_config({"cit": {"port": 5006, "secure_context": True}}).secure_context is True
    assert _parse_test_config({"cit": {"port": 5006}}).secure_context is False
