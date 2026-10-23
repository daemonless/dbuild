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


def _png(pattern: bool) -> bytes:
    import io

    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    a = np.full((32, 32, 3), 128, dtype=np.uint8)
    if pattern:
        a[::4] = 0
    b = io.BytesIO()
    Image.fromarray(a).save(b, "PNG")
    return b.getvalue()


def test_frame_is_blank():
    pytest.importorskip("skimage")
    from dbuild.screenshot import frame_is_blank

    assert frame_is_blank(_png(False))
    assert not frame_is_blank(_png(True))


class _FakeDriver:
    def __init__(self, frames):
        self.frames = frames
        self.shots = 0
        self.saved = None

    def set_page_load_timeout(self, t):
        pass

    def execute_cdp_cmd(self, *a):
        pass

    def get(self, url):
        pass

    def execute_script(self, s):
        return "complete"

    def get_screenshot_as_png(self):
        f = self.frames[min(self.shots, len(self.frames) - 1)]
        self.shots += 1
        return f

    def save_screenshot(self, path):
        self.saved = self.frames[min(self.shots - 1, len(self.frames) - 1)]

    def quit(self):
        pass


def _capture(monkeypatch, frames):
    import dbuild.screenshot as s

    clock = [0.0]
    monkeypatch.setattr(s.time, "time", lambda: clock[0])
    monkeypatch.setattr(s.time, "sleep", lambda n: clock.__setitem__(0, clock[0] + n))
    d = _FakeDriver(frames)
    monkeypatch.setattr(s.webdriver, "Chrome", lambda **kw: d)
    monkeypatch.setattr(s, "Service", lambda **kw: None)
    assert s.capture("http://10.88.0.4:8080/", "/nonexistent.png")
    return d


def test_blank_frames_are_not_stable(monkeypatch, capsys):
    # nocodb on CI: the SPA's blank first paint matched itself and was shot.
    pytest.importorskip("skimage")
    blank, ui = _png(False), _png(True)
    d = _capture(monkeypatch, [blank, blank, blank, ui, ui])
    assert d.saved == ui
    assert "UI stabilized" in capsys.readouterr().err


def test_page_that_stays_blank_times_out(monkeypatch, capsys):
    pytest.importorskip("skimage")
    d = _capture(monkeypatch, [_png(False)])
    assert d.saved == _png(False)
    assert "did not stabilize" in capsys.readouterr().err
