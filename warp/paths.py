"""Cross-platform locations.

The ONLY module that knows about directories — no other file may hardcode a
path. On Windows it uses %APPDATA%, on macOS ~/Library/Application Support,
elsewhere $XDG_CONFIG_HOME (falling back to ~/.config).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP = "warp"


def _base() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def config_dir() -> Path:
    p = _base() / APP
    p.mkdir(parents=True, exist_ok=True)
    return p


def account_path() -> Path:
    return config_dir() / "account.json"


def wireproxy_conf_path() -> Path:
    return config_dir() / "wireproxy.conf"


def app_dir() -> Path:
    """Where this package lives — used to find the bundled UI."""
    return Path(__file__).resolve().parent


def ui_path() -> Path:
    return app_dir() / "ui" / "index.html"
