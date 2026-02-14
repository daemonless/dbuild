try:
    from importlib.metadata import version as _pkg_version
    VERSION = _pkg_version("dbuild")
except Exception:
    VERSION = "dev"
