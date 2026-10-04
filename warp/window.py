"""Open the UI in its own window instead of a browser tab.

A tab is not an app: it has an address bar, it shares a window with the user's
mail, and it disappears the moment they tidy their tabs. Chromium-family
browsers have an application mode — a chromeless window with its own WM class —
which is the whole of what "a dedicated app" needs, and it costs nothing new
because the browser is already installed.

Deliberately *not* pywebview, QtWebEngine or Electron: a real embedded webview
is a large dependency to reproduce a flag the browser already has, and it would
break the stdlib-only property that makes `python3 -m warp` work anywhere.

Detection is a launcher *prefix*, not a bare path, because on Linux the browser
is very often a Flatpak (`flatpak run com.google.Chrome`) and never appears on
PATH. A PATH-only scan silently finds nothing on such a machine — the first
version of this module returned None on the machine it was written on.

Firefox is deliberately not a candidate: it has no chromeless app-window mode,
so the honest outcome there is the tab fallback rather than a fake app window.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Checked on PATH first, in order of how app-like the result looks.
PATH_NAMES = (
    "chromium", "chromium-browser", "google-chrome-stable", "google-chrome",
    "brave-browser", "microsoft-edge-stable", "microsoft-edge", "vivaldi", "opera",
)

# Flatpak app ids, same order. A Flatpak browser has no /usr/bin entry at all.
FLATPAK_IDS = (
    "com.google.Chrome", "org.chromium.Chromium", "com.brave.Browser",
    "com.microsoft.Edge", "com.vivaldi.Vivaldi",
)

# macOS and Windows rarely put the browser on PATH either.
MAC_APPS = (
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
)


def _windows_paths() -> list[str]:
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
             os.environ.get("LOCALAPPDATA")]
    rel = (
        r"Google\Chrome\Application\chrome.exe",
        r"Microsoft\Edge\Application\msedge.exe",
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
        r"Chromium\Application\chrome.exe",
    )
    return [str(Path(r) / p) for r in roots if r for p in rel]


def _flatpak_launchers() -> list[list[str]]:
    """Installed Flatpak browsers. Empty when flatpak is absent or has none."""
    if not shutil.which("flatpak"):
        return []
    try:
        done = subprocess.run(["flatpak", "list", "--app", "--columns=application"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    installed = {line.strip() for line in done.stdout.splitlines()}
    return [["flatpak", "run", app] for app in FLATPAK_IDS if app in installed]


def launchers() -> list[list[str]]:
    """Every way to open an app window here, best first."""
    found = [[path] for name in PATH_NAMES if (path := shutil.which(name))]
    found += _flatpak_launchers()
    if sys.platform == "darwin":
        found += [[p] for p in MAC_APPS if os.path.exists(p)]
    elif sys.platform == "win32":
        found += [[p] for p in _windows_paths() if os.path.exists(p)]
    return found


def find() -> list[str] | None:
    found = launchers()
    return found[0] if found else None


def command(launcher: list[str], url: str, profile: str | None = None) -> list[str]:
    """The argv that opens `url` as its own window.

    A separate --user-data-dir is what makes this reliably *its own* app: with
    the default profile the launch is handed to an already-running browser,
    which ignores --class, and the window comes up as a generic browser window
    on the taskbar rather than as warp.
    """
    cmd = list(launcher) + [f"--app={url}", "--class=warp", "--window-size=520,780",
                            # a fresh profile shows the first-run flow without these
                            "--no-first-run", "--no-default-browser-check"]
    if profile:
        cmd.append(f"--user-data-dir={profile}")
    return cmd


def profile_for(launcher: list[str], base: Path | None) -> str | None:
    """Where this launcher can actually write a profile.

    A Flatpak browser is sandboxed: HOME is the same path but ~/.config is not
    exposed at all (verified — `ls ~/.config/warp` inside the sandbox says "No
    such file or directory"), so a host path there is silently ignored and the
    browser falls back to its own profile. Only ~/.var/app/<id> is writable.
    """
    if len(launcher) >= 3 and launcher[0] == "flatpak" and launcher[1] == "run":
        return str(Path.home() / ".var" / "app" / launcher[2] / "config" / "warp-browser")
    return str(base) if base else None


def open_ui(url: str, profile_dir: Path | None = None, prefer_tab: bool = False) -> str:
    """Open the UI. Returns 'window', 'tab' or 'none' — what actually happened."""
    if not prefer_tab:
        launcher = find()
        if launcher:
            cmd = command(launcher, url, profile_for(launcher, profile_dir))
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                return "window"
            except OSError:
                pass  # fall through to a tab rather than opening nothing at all
    import webbrowser
    try:
        if webbrowser.open(url):
            return "tab"
    except Exception:  # noqa: BLE001 - "no browser at all" is a valid outcome
        pass
    return "none"
