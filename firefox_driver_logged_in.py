"""
This script sets up a Selenium WebDriver for Firefox that uses an existing user profile
to maintain logged-in sessions. It automatically detects the Firefox installation and user
profile based on the operating system (Windows, macOS, Linux). You can also specify a
custom profile path via an environment variable or function argument.
"""

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
import os
import sys
import glob


def create_logged_in_firefox(profile_path: str | None = None,
                             start_url: str | None = None,
                             page_load_timeout: int = 60):

    env_profile = (os.environ.get("FIREFOX_PROFILE", "") or "").strip()
    options = Options()

    # Binary + profile discovery per platform
    if os.name == "nt" or sys.platform.startswith("win"):
        bin_path = _windows_firefox_binary() if "_windows_firefox_binary" in globals() else None
        if bin_path:
            options.binary_location = bin_path
        auto_profile = _windows_profile_dir() if "_windows_profile_dir" in globals() else None
    elif sys.platform == "darwin":
        bin_path = _mac_firefox_binary() if "_mac_firefox_binary" in globals() else None
        if bin_path:
            options.binary_location = bin_path
        auto_profile = _mac_profile_dir() if "_mac_profile_dir" in globals() else None
    else:
        bin_path = None
        auto_profile = _linux_profile_dir() if "_linux_profile_dir" in globals() else None

    profile_to_use = profile_path or env_profile or auto_profile
    if profile_to_use and os.path.isdir(profile_to_use):
        options.profile = profile_to_use

    driver = webdriver.Firefox(options=options)
    driver.set_page_load_timeout(page_load_timeout)
    if start_url:
        driver.get(start_url)
    return driver


def _windows_firefox_binary():
    candidates = [
        r"C:\\Program Files\\Firefox Developer Edition\\firefox.exe",
        r"C:\\Program Files\\Mozilla Firefox\\firefox.exe",
        r"C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _windows_profile_dir():
    base = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles")
    patterns = ["*.dev-edition-default*", "*.default-release*", "*.default*"]
    dirs = []
    for pat in patterns:
        dirs.extend([d for d in glob.glob(os.path.join(base, pat)) if os.path.isdir(d)])
    if not dirs:
        return None
    # pick most recently modified profile dir
    dirs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return dirs[0]


def _mac_firefox_binary():
    # Firefox Developer Edition first, then standard Firefox
    mac_dev = "/Applications/Firefox Developer Edition.app/Contents/MacOS/firefox"
    mac_std = "/Applications/Firefox.app/Contents/MacOS/firefox"
    if os.path.exists(mac_dev):
        return mac_dev
    if os.path.exists(mac_std):
        return mac_std
    return None


def _mac_profile_dir():
    # Your previously shared Developer Edition default profile (adjust if needed)
    default_dev = os.path.expanduser(
        "/Users/forhad/Library/Application Support/Firefox/Profiles/a119ge5a.dev-edition-default-1656653236628"
    )
    default_std = os.path.expanduser(
        "/Users/forhad/Library/Application Support/Firefox/Profiles/ur49iatr.default"
    )
    if os.path.exists(default_dev):
        return default_dev
    if os.path.exists(default_std):
        return default_std
    return None


def _linux_profile_dir():
    # Common Linux path
    base = os.path.expanduser("~/.mozilla/firefox")
    patterns = ["*.default-release", "*.default", "*.dev-edition-default"]
    dirs = []
    for pat in patterns:
        dirs.extend([d for d in glob.glob(os.path.join(base, pat)) if os.path.isdir(d)])
    if not dirs:
        return None
    dirs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return dirs[0]


def main():
    driver = create_logged_in_firefox(start_url="https://www.linkedin.com/sales")



if __name__ == "__main__":
    main()