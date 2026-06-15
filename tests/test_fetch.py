"""Tests for dbuild.fetch shared download/MIME helpers."""

import unittest

from dbuild import fetch


class TestGithubRawUrl(unittest.TestCase):
    def test_blob_url_converted(self):
        url = "https://github.com/owner/repo/blob/main/path/logo.svg"
        self.assertEqual(
            fetch.github_raw_url(url),
            "https://raw.githubusercontent.com/owner/repo/main/path/logo.svg",
        )

    def test_raw_url_unchanged(self):
        url = "https://raw.githubusercontent.com/owner/repo/main/logo.svg"
        self.assertEqual(fetch.github_raw_url(url), url)

    def test_non_github_unchanged(self):
        url = "https://example.com/assets/logo.png"
        self.assertEqual(fetch.github_raw_url(url), url)

    def test_github_non_blob_unchanged(self):
        url = "https://github.com/owner/repo/releases/download/v1/logo.png"
        self.assertEqual(fetch.github_raw_url(url), url)


if __name__ == "__main__":
    unittest.main()
