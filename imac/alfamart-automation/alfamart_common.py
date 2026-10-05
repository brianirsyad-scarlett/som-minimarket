#!/usr/bin/env python3
"""
Shared helpers for the Alfamart B2B report automation.

login_and_get_np_session() drives a real (Selenium) Chrome browser to:
  1. log in to b2b.alfamart.co.id (username + password + TOTP 2FA) -- the login
     form is behind bot-detection that rejects plain HTTP clients, so this step
     must go through a real browser.
  2. follow the "Laporan" -> "Dashboard & Modular" link (get_laporan_new_premium.php),
     which performs a server-side SSO handoff and sets a `session` cookie scoped
     to b2b-np.alfamart.co.id.

That `session` cookie DOES work from a plain HTTP client (verified against the
live site), so everything after login (the report-trigger POSTs) is done with
urllib, not Selenium -- much faster and avoids keeping Chrome open.

The browser session cookie is a JS-session (non-persistent) cookie that is
discarded when Chrome closes, so there is nothing to cache across runs: every
run must fully re-login (uname/pass/TOTP), every time.
"""

import base64
import hashlib
import hmac
import os
import struct
import sys
import time
import urllib.parse
import urllib.request

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(HERE, "browser_profile")
NP_BASE = "https://b2b-np.alfamart.co.id"


def totp(secret, digits=6, period=30):
    secret = secret.strip().replace(" ", "").upper()
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(secret + padding)
    counter = int(time.time() // period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code_int).zfill(digits)


def login_and_get_np_session(headless=True):
    """Returns the b2b-np.alfamart.co.id `session` cookie value."""
    uname = os.environ.get("ALFAMART_UNAME")
    upass = os.environ.get("ALFAMART_UPASS")
    totp_secret = os.environ.get("ALFAMART_TOTP_SECRET")
    if not uname or not upass or not totp_secret:
        sys.exit("ERROR: set ALFAMART_UNAME, ALFAMART_UPASS, ALFAMART_TOTP_SECRET "
                  "(source ~/.alfamart.env first).")

    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--start-maximized")
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,1000")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    wait = WebDriverWait(driver, 30)
    try:
        driver.get("https://b2b.alfamart.co.id/login.php")
        if "login" in driver.current_url:
            wait.until(EC.visibility_of_element_located((By.NAME, "uname"))).send_keys(uname)
            driver.find_element(By.NAME, "upass").send_keys(upass)
            driver.find_element(By.NAME, "upass").submit()
            wait.until(EC.url_contains("validasi-otp"))
            code_input = wait.until(EC.visibility_of_element_located((By.NAME, "code")))
            code_input.send_keys(totp(totp_secret))
            code_input.submit()
            wait.until(lambda d: "validasi-otp" not in d.current_url)

        if "index" not in driver.current_url:
            raise RuntimeError(f"login did not land on index.php, got {driver.current_url}")

        main_handle = driver.current_window_handle
        driver.execute_script(
            "window.open(arguments[0], '_blank');",
            "https://b2b.alfamart.co.id/get_laporan_new_premium.php",
        )
        wait.until(lambda d: len(d.window_handles) > 1)
        new_handle = [h for h in driver.window_handles if h != main_handle][0]
        driver.switch_to.window(new_handle)
        wait.until(EC.url_contains("b2b-np.alfamart.co.id"))

        cookie = driver.get_cookie("session")
        if not cookie:
            raise RuntimeError("SSO handoff did not set a `session` cookie on b2b-np.alfamart.co.id")
        return cookie["value"]
    finally:
        driver.quit()


def np_post(session_cookie, path, params, timeout=60):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"{NP_BASE}{path}",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"session={session_cookie}",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")
