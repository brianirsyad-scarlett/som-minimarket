"""Shared config and helpers for the Tokopedia seller-center automation.

Used by tokped_login.py (manual, interactive first-time login) and
tokped_export.py (daily unattended order export).
"""
import os
import plistlib
import subprocess
import time
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

AUTOMATION_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(AUTOMATION_DIR, "browser_profile")
EXPORTS_DIR = os.path.join(AUTOMATION_DIR, "exports")

# Password lives in the macOS login Keychain, not in this file. See README.md
# to add/rotate it: `security add-generic-password -a ACCOUNT -s SERVICE -w PW -A -U`
KEYCHAIN_SERVICE = "tokopedia-seller-automation"
KEYCHAIN_ACCOUNT = "brian.rinaldy@scarlett.co.id"

HOMEPAGE_URL = "https://seller-id.tokopedia.com/homepage?is_new_connect=0&lng=en&region_check=1&shop_region=ID"
ORDERS_URL = "https://seller-id.tokopedia.com/order"


def get_password():
    """Read the Tokopedia seller password from the macOS Keychain."""
    result = subprocess.run(
        ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            "Could not read the Tokopedia password from Keychain "
            f"(account={KEYCHAIN_ACCOUNT!r}, service={KEYCHAIN_SERVICE!r}). "
            "Re-add it with:\n"
            f"  security add-generic-password -a '{KEYCHAIN_ACCOUNT}' -s '{KEYCHAIN_SERVICE}' "
            "-w 'yourpassword' -A -U"
        )
    return result.stdout.strip()


FINDER_TAG = "TikTok Tokopedia"


def tag_finder_file(path, tag=FINDER_TAG):
    """Apply a macOS Finder tag to a file so downloaded exports are visibly
    marked as coming from this automation. Finder tags are a binary-plist
    extended attribute, not plain text -- `os.setxattr` isn't available on
    macOS's CPython build, so this shells out to the `xattr` CLI instead.
    """
    try:
        data = plistlib.dumps([f"{tag}\n0"], fmt=plistlib.FMT_BINARY)
        subprocess.run(
            ["xattr", "-wx", "com.apple.metadata:_kMDItemUserTags", data.hex(), path],
            check=True, capture_output=True,
        )
    except Exception as e:
        print(f"WARNING: couldn't tag {path}: {e}")


def close_stray_windows(driver, keep_handle):
    """Close any browser window/tab other than the one we're meant to be
    driving (e.g. an unrelated promo popup that opened on its own) and make
    sure we stay focused on the right one -- guards against Selenium's
    "current window" silently drifting to a popup and every subsequent call
    acting on the wrong page.
    """
    for handle in driver.window_handles:
        if handle == keep_handle:
            continue
        try:
            driver.switch_to.window(handle)
            print(f"Closing unexpected extra window/tab: {driver.current_url}")
            driver.close()
        except Exception as e:
            print(f"WARNING: couldn't close stray window {handle}: {e}")
    driver.switch_to.window(keep_handle)


def wait_clickable(driver, wait, locator):
    """Drop-in replacement for EC.element_to_be_clickable.

    The installed selenium (4.47.0) + auto-downloaded chromedriver combo has a
    bug where element_to_be_clickable crashes with
    "AttributeError: 'NoneType' object has no attribute 'is_displayed'"
    instead of retrying when the element isn't there yet. This reimplements
    the same check by hand so a missing element just keeps polling.
    """
    def _predicate(d):
        try:
            el = d.find_element(*locator)
            return el if (el.is_displayed() and el.is_enabled()) else False
        except Exception:
            return False

    return wait.until(_predicate)


def build_driver(download_dir=None):
    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--start-maximized")

    if download_dir:
        os.makedirs(download_dir, exist_ok=True)
        options.add_experimental_option("prefs", {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        })

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    if download_dir:
        # Chrome's "Allow this site to download multiple files?" permission
        # prompt is a native browser dialog, not part of the page -- Selenium
        # can't see or click it, so every download after the first silently
        # stalls until a human clicks Allow. Telling Chrome via DevTools
        # Protocol to always allow downloads bypasses that prompt entirely,
        # which is what unattended automation needs.
        driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": download_dir,
        })

    return driver


def _is_on_login_page(driver):
    # Path-based check -- the login page's own redirect_url query param embeds
    # the string "homepage", so a naive substring check on the full URL false-
    # positives as "logged in" while still sitting on /account/login.
    return urlparse(driver.current_url).path.startswith("/account/login")


def _is_on_homepage(driver):
    return urlparse(driver.current_url).path.startswith("/homepage")


def _try_otp_autofill(driver, wait, since_ts):
    """If an email-verification-code screen appeared, fetch the code from the
    mailbox (tokped_otp.py) and submit it. Returns True if a code was
    submitted, False if no OTP screen showed up at all (nothing to do here).
    Raises if the screen appeared but the code couldn't be fetched/submitted --
    NOTE: the selectors/heuristics here are unverified against a real OTP
    email (see tokped_otp.py's docstring); expect to need adjustments once
    this path actually runs for real.
    """
    otp_locator = (By.CSS_SELECTOR, 'input[placeholder="Enter verification code" i]')
    try:
        code_input = WebDriverWait(driver, 10).until(EC.visibility_of_element_located(otp_locator))
    except TimeoutException:
        return False  # no OTP step appeared

    from tokped_otp import fetch_verification_code  # optional dependency, imported lazily

    print("Email verification step detected -- fetching code from inbox...")
    code = fetch_verification_code(since_ts=since_ts, timeout=90)
    code_input.clear()
    code_input.send_keys(code)

    try:
        submit_btn = wait_clickable(driver, wait, (By.ID, "TikTok_Ads_SSO_Login_Btn"))
    except TimeoutException:
        submit_btn = wait_clickable(driver, wait, (By.XPATH, "//button[contains(., 'Log in')]"))
    submit_btn.click()
    return True


def ensure_logged_in(driver, wait, interactive):
    """Navigate to the seller homepage, logging in only if the saved session expired.

    interactive=True (tokped_login.py): pauses on CAPTCHA/verification for a human.
    interactive=False (tokped_export.py, run from launchd): never blocks on input() --
    raises instead, so an unattended run fails fast and visibly rather than hanging.
    """
    driver.get(HOMEPAGE_URL)

    if not _is_on_login_page(driver):
        return  # saved session cookies are still valid, nothing to do

    print("Saved session expired or missing -- logging in...")

    # Select "Log in with email" first -- the login page can default to a
    # different panel (e.g. phone/QR), and username/password only exist once
    # this panel is active.
    email_tab = wait_clickable(driver, wait, (By.ID, "TikTok_Ads_SSO_Login_Email_Panel_Button"))
    email_tab.click()

    email_input = wait.until(EC.visibility_of_element_located((By.ID, "TikTok_Ads_SSO_Login_Email_Input")))
    email_input.clear()
    email_input.send_keys(KEYCHAIN_ACCOUNT)

    password_input = wait.until(EC.visibility_of_element_located((By.ID, "TikTok_Ads_SSO_Login_Pwd_Input")))
    password_input.clear()
    password_input.send_keys(get_password())

    login_btn = wait_clickable(driver, wait, (By.ID, "TikTok_Ads_SSO_Login_Btn"))
    login_click_ts = time.time()
    login_btn.click()

    if interactive:
        input("Solve CAPTCHA/Email verification in browser, then press ENTER here to continue...")
        wait.until(_is_on_homepage)
        print("Logged in successfully! Session saved for future runs.")
        return

    otp_note = ""
    try:
        WebDriverWait(driver, 8).until(_is_on_homepage)
    except TimeoutException:
        try:
            if _try_otp_autofill(driver, wait, login_click_ts):
                print("Submitted an auto-fetched email verification code.")
        except Exception as otp_err:
            otp_note = f" (auto email-verification fetch also failed: {otp_err})"

    try:
        WebDriverWait(driver, 45).until(_is_on_homepage)
    except TimeoutException:
        debug_path = os.path.join(AUTOMATION_DIR, "last_login_failure.png")
        try:
            driver.save_screenshot(debug_path)
        except Exception:
            debug_path = None
        raise RuntimeError(
            "Login didn't reach the homepage unattended (still on: "
            f"{driver.current_url}){otp_note}. "
            + (f"Screenshot saved to {debug_path}. " if debug_path else "")
            + "Run `python3 tokped_login.py` by hand once to refresh the saved "
            "session, then the daily job will go back to working on its own."
        )
