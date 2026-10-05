"""Manual, interactive first-time (or re-auth) login for Tokopedia seller center.

Run this by hand whenever the saved session has expired and tokped_export.py
reports it can't proceed unattended. Opens a visible Chrome window so you can
solve the CAPTCHA / email verification step yourself.
"""
from selenium.webdriver.support.ui import WebDriverWait

from tokped_common import build_driver, ensure_logged_in

driver = build_driver()
wait = WebDriverWait(driver, 120)  # extended timeout for manual verification

try:
    ensure_logged_in(driver, wait, interactive=True)
finally:
    driver.quit()  # closing the driver flushes session cookies to disk
