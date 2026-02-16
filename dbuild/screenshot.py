"""Screenshot capture using Selenium with smart UI stability waiting.

Requires: selenium, chromium/chrome.  These are optional dependencies --
import errors are caught by the caller (test.py) so the rest of dbuild
works without them.
"""

from __future__ import annotations

import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

# Config via environment variables
CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/local/bin/chrome")
CHROMEDRIVER_BIN = os.environ.get("CHROMEDRIVER_BIN", "/usr/local/bin/chromedriver")
WINDOW_SIZE = os.environ.get("SCREENSHOT_SIZE", "1920,1080")


def capture(url: str, output: str, timeout: int = 30, min_wait: int = 0) -> bool:
    """Capture a screenshot of *url* and save to *output*.

    Waits for ``document.readyState == "complete"`` then monitors for UI
    stability (consecutive identical screenshots) before saving.

    Parameters
    ----------
    url:
        The page URL to capture.
    output:
        Path to save the PNG screenshot.
    timeout:
        Selenium page-load timeout in seconds.
    min_wait:
        Minimum seconds to wait before declaring stable.

    Returns
    -------
    bool
        True on success, False on error.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument(f"--window-size={WINDOW_SIZE}")
    options.set_capability("acceptInsecureCerts", True)
    if CHROME_BIN:
        options.binary_location = CHROME_BIN

    service = Service(executable_path=CHROMEDRIVER_BIN)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(timeout)

    # Set exact viewport size via CDP so screenshots are identical across chromium versions
    w, h = (int(x) for x in WINDOW_SIZE.split(","))
    driver.execute_cdp_cmd(
        "Emulation.setDeviceMetricsOverride",
        {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False},
    )

    try:
        driver.get(url)
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        # Smart wait: compare consecutive screenshots until stable
        last_screen = None
        start_time = time.time()
        max_stability_wait = max(10, min_wait)
        print(
            f"Waiting for UI stability (max {max_stability_wait}s, min {min_wait}s)...",
            file=sys.stderr,
        )
        stable = False
        while time.time() - start_time < max_stability_wait:
            current_screen = driver.get_screenshot_as_base64()
            elapsed = time.time() - start_time
            if last_screen and current_screen == last_screen and elapsed >= min_wait:
                    print(f"UI stabilized after {elapsed:.2f}s", file=sys.stderr)
                    stable = True
                    break
            last_screen = current_screen
            time.sleep(0.5)

        if not stable:
            print(
                "UI did not stabilize (timeout reached), taking final screenshot",
                file=sys.stderr,
            )

        driver.save_screenshot(output)
        return True
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False
    finally:
        driver.quit()
