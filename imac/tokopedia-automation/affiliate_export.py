"""Exports TikTok Affiliate Center "Transaction Analysis" reports (Creators,
Products, Videos, LIVE streams) day-by-day and uploads them to GCS, mirroring
tokped_export.py's trigger-all-then-poll-all architecture for the Seller
Center order export.

Key differences from tokped_export.py, confirmed live 2026-09-28:
  - One page (https://affiliate-id.tokopedia.com/insights/transaction-analysis)
    has all 4 report types as TABS ("Creators", "Products", "Videos",
    "LIVE streams"), each with its own independent date-range control,
    Export button, and "Exported Reports" panel.
  - The custom date-range picker only allows historical days up to 179 days
    back, AND only up to (today - 2 days) -- the most recent 2 days aren't
    finalized yet and are greyed out/unselectable in Custom mode ("Today
    (real-time)" is a separate, differently-shaped quick-select not handled
    by this script).
  - Export tasks can take several minutes to finish generating (observed up
    to ~20 minutes for a single day in this account's history) -- much
    longer than Seller Center's per-day report. This is exactly why this
    script triggers every (report type, date) pair first, then polls/
    downloads afterward, rather than waiting on each one in turn.
  - The Export button re-uses the CURRENTLY APPLIED date-range filter (no
    separate date picker inside an "export" modal) -- so the date must be
    set via the page's own filter control before each click.
  - Each generated file is named Transaction_Analysis_<Token>_List_
    YYYYMMDD-YYYYMMDD.xlsx, exactly matching what's already in
    gs://bucket_som/sales_parquet/raw/online/tiktok/affiliate/<Folder>/ -- so a
    (report type, date) pair can be tracked by that exact string appearing
    in the "Exported Reports" panel, without needing a task_id.
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tokped_common import build_driver, ensure_logged_in, wait_clickable, tag_finder_file

AUTOMATION_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(AUTOMATION_DIR, "affiliate_exports")

AFFILIATE_URL = "https://affiliate-id.tokopedia.com/insights/transaction-analysis"

# (tab label as shown on the page, GCS folder name, filename report-type token)
REPORT_TYPES = [
    ("Creators", "Creator", "Creator"),
    ("Products", "Products", "Product"),
    ("Videos", "Video", "Video"),
    ("LIVE streams", "Live", "Live"),
]

GCS_BUCKET_NAME = "bucket_som"
GCS_AFFILIATE_PREFIX = "sales_parquet/raw/online/tiktok/affiliate"

# Confirmed live 2026-09-28: Custom range's latest selectable day is (today - 2).
FINALIZATION_LAG_DAYS = 2
MONTH_NAV_MAX_CLICKS = 36


def safe_click(driver, element):
    """Standard click with a JS fallback if an overlay intervenes -- this
    page has a floating "Chats" support-widget bubble pinned to the bottom
    corner that intercepts normal clicks on whatever happens to render
    underneath it (confirmed live 2026-09-28 on the Exported-Reports-panel
    icon). Same pattern as tokped_export.py's safe_click."""
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def _active_tabpanel(driver):
    """The one visible tabpanel among the 4 report-type tabs' panels. Holds
    the Collaboration-type/Creator-name filters, Export button, results
    table, and Exported-Reports-panel icon -- all per-report-type."""
    panels = driver.find_elements(By.CSS_SELECTOR, '[role="tabpanel"]')
    for p in panels:
        if p.is_displayed():
            return p
    raise RuntimeError("Could not find a visible tabpanel")


def _details_container(driver):
    """The "Details" section as a whole (heading + shared date-range
    control + tabs + active tabpanel). Confirmed live 2026-09-28: the page
    has a SECOND, separate 'Last 7 days'/'Custom' date-range control up in
    the unrelated "Key metrics" section higher on the page, with identical
    option text -- scoping to this container (found via the one Export
    button on the page, which only exists in the Details section) is what
    keeps searches from accidentally hitting the wrong one.
    """
    return driver.find_element(
        By.XPATH,
        "//*[normalize-space(.)='Details']"
        "/ancestor::div[.//button[@data-testid='export-button']][1]",
    )


def click_report_tab(driver, wait, tab_label):
    tab = wait_clickable(driver, wait, (By.XPATH, f"//*[@role='tab' and normalize-space(.)='{tab_label}']"))
    safe_click(driver, tab)
    time.sleep(1)


def _date_range_dropdown_trigger(panel):
    """The clickable control showing 'Last 7 days' / 'Last 30 days' /
    'Today (real-time)' / 'Custom' -- whichever is currently applied."""
    return panel.find_element(
        By.XPATH,
        ".//*[self::div or self::span]"
        "[normalize-space(.)='Last 7 days' or normalize-space(.)='Last 30 days' "
        "or normalize-space(.)='Today (real-time)' or normalize-space(.)='Custom']",
    )


def _date_range_text(panel):
    """The read-only 'MM/DD/YYYY - MM/DD/YYYY' display next to the dropdown."""
    for e in panel.find_elements(By.XPATH, ".//*"):
        t = e.text.strip()
        if re.fullmatch(r"\d{2}/\d{2}/\d{4} - \d{2}/\d{2}/\d{4}", t):
            return e
    raise RuntimeError("Could not find the applied date-range text display")


def _month_header_texts(driver):
    """Confirmed live 2026-09-28: the header renders as 'MM/YYYY' with NO
    spaces around the slash -- screenshots visually render it with spacing
    from letter/word-spacing CSS, which is not real whitespace in the DOM.
    Never trust a screenshot's apparent spacing over the actual .text."""
    return [
        h.text.strip() for h in driver.find_elements(By.CSS_SELECTOR, ".core-picker-header-value")
        if re.fullmatch(r"\d{2}/\d{4}", h.text.strip())
    ]


def _navigate_calendar_to_month(driver, wait, target_date):
    """Click the single-step prev/next month arrow the right number of times.

    Direction and magnitude are computed up front from the currently-shown
    left panel's 'MM/YYYY' header vs the target -- not a blind loop -- so
    this can't repeat the Seller Center substring-matching bug (see
    tokped_export.py's navigate_to_target_month for that history). Verified
    live 2026-09-28 across a 7-month backward jump and a 4-month forward
    jump from a fresh page load.
    """
    headers = _month_header_texts(driver)
    if not headers:
        raise RuntimeError("Could not read the calendar's month/year header")
    cur_month, cur_year = (int(x) for x in headers[0].split("/"))
    target_total = target_date.year * 12 + target_date.month
    current_total = cur_year * 12 + cur_month
    diff = target_total - current_total
    if diff == 0:
        return

    forward = diff > 0
    nav_label = "right" if forward else "left"
    # Confirmed live 2026-09-28: same "arco-icon-left/right" single-step icon
    # naming as Seller Center's picker, but wrapped in "core-picker-header-icon"
    # here (not "p-picker-header-icon") -- a different underlying component.
    nav_btn = wait_clickable(
        driver, wait,
        (By.XPATH, f"//*[contains(@class, 'arco-icon-{nav_label}')]"
                   "/ancestor::div[contains(@class, 'core-picker-header-icon')]"),
    )

    for _ in range(min(abs(diff), MONTH_NAV_MAX_CLICKS)):
        safe_click(driver, nav_btn)
        time.sleep(0.3)

    headers = _month_header_texts(driver)
    expected = f"{target_date.month:02d}/{target_date.year}"
    if expected not in headers:
        raise RuntimeError(
            f"Calendar navigation landed on {headers} instead of {expected} "
            f"(wanted {'+' if forward else '-'}{abs(diff)} month click(s) from {cur_month:02d}/{cur_year})"
        )


def _find_in_view_day_cell(driver, target_date):
    """Finds the clickable day-number element for target_date within
    whichever of the two visible month panels currently shows target_date's
    month/year -- scoped to '.core-picker-cell-in-view' so it can't match an
    adjacent month's overflow filler day showing the same number (e.g. Aug
    30/31 rendered at the start of September's grid), confirmed live
    2026-09-28.
    """
    expected_header = f"{target_date.month:02d}/{target_date.year}"
    day_str = f"{target_date.day:02d}"
    for header in driver.find_elements(By.CSS_SELECTOR, ".core-picker-header-value"):
        if header.text.strip() != expected_header:
            continue
        panel_root = header.find_element(
            By.XPATH, "./ancestor::*[contains(@class,'core-picker-header')]/parent::*"
        )
        for cell in panel_root.find_elements(By.CSS_SELECTOR, ".core-picker-cell-in-view .core-picker-date"):
            if cell.text.strip() == day_str:
                return cell
    raise RuntimeError(f"No in-view day cell found for {target_date:%Y-%m-%d} "
                        "(likely outside the 179-day lookback or in the 2-day finalization lag)")


def select_custom_single_day(driver, wait, details, target_date):
    """Sets the shared Details-section date-range filter to a single day
    and verifies the resulting display text matches exactly before
    returning -- never trust the click alone, see tokped_export.py's
    February substring-matching incident for exactly why.
    """
    trigger = _date_range_dropdown_trigger(details)
    safe_click(driver, trigger)
    time.sleep(0.5)

    custom_option = wait_clickable(driver, wait, (By.XPATH, "//*[normalize-space(.)='Custom']"))
    safe_click(driver, custom_option)
    time.sleep(0.5)

    _navigate_calendar_to_month(driver, wait, target_date)

    day_cell = _find_in_view_day_cell(driver, target_date)
    safe_click(driver, day_cell)
    time.sleep(0.2)
    # Re-find for the second click -- selecting the first date can re-render
    # the grid and leave the old element stale (same reasoning as
    # tokped_export.py's day_cell re-find).
    day_cell = _find_in_view_day_cell(driver, target_date)
    safe_click(driver, day_cell)
    time.sleep(0.5)

    expected_text = f"{target_date:%m/%d/%Y} - {target_date:%m/%d/%Y}"
    actual = _date_range_text(details).text.strip()
    if actual != expected_text:
        raise RuntimeError(f"Date range shows {actual!r}, expected {expected_text!r} for {target_date:%Y-%m-%d}")


def click_export(driver, wait, panel):
    export_btn = panel.find_element(By.CSS_SELECTOR, 'button[data-testid="export-button"]')
    safe_click(driver, export_btn)
    time.sleep(1)


def trigger_all(driver, wait, dates):
    """Phase 1: for every (report type, date), apply the filter and click
    Export. Returns {(gcs_folder, filename_token): [dates triggered]}."""
    driver.get(AFFILIATE_URL)
    triggered = {rt[1]: [] for rt in REPORT_TYPES}

    for tab_label, gcs_folder, _token in REPORT_TYPES:
        click_report_tab(driver, wait, tab_label)
        for d in dates:
            try:
                details = _details_container(driver)
                select_custom_single_day(driver, wait, details, d)
                panel = _active_tabpanel(driver)  # re-fetch, filter change can re-render the panel
                click_export(driver, wait, panel)
                triggered[gcs_folder].append(d)
                print(f"Queued {tab_label} export for {d:%Y-%m-%d}")
            except Exception as e:
                print(f"WARNING: failed to queue {tab_label} export for {d:%Y-%m-%d}: {e}")
    return triggered


def _expected_filename_stub(token, d):
    return f"Transaction_Analysis_{token}_List_{d:%Y%m%d}-{d:%Y%m%d}"


def download_ready(driver, wait, triggered, timeout=3600, poll_interval=20):
    """Phase 2: repeatedly open each tab's Exported Reports panel and
    download whichever queued (report type, date) pairs have finished,
    until everything's downloaded or the timeout is hit.

    Confirmed live 2026-09-28: the actual file download itself (after the
    export has finished generating) can take several minutes for larger
    reports (e.g. Video's ~8MB file) -- clicking Download and then blocking
    on a short wait for the file to appear was the wrong shape, since giving
    up early meant re-clicking Download again next cycle and restarting a
    competing download rather than just letting the first one finish. So
    "click Download" happens at most ONCE per (folder, date) -- tracked in
    `clicked` -- and every cycle thereafter just checks the filesystem
    (non-blocking) for whichever downloads have finished by now.
    """
    token_by_folder = {rt[1]: rt[2] for rt in REPORT_TYPES}
    tab_by_folder = {rt[1]: rt[0] for rt in REPORT_TYPES}
    pending = {folder: list(dates) for folder, dates in triggered.items() if dates}
    downloaded = {folder: [] for folder in triggered}
    clicked = {}  # (folder, date) -> before_names snapshot taken at click time

    deadline = time.time() + timeout
    while any(pending.values()) and time.time() < deadline:
        for gcs_folder, dates in list(pending.items()):
            if not dates:
                continue
            token = token_by_folder[gcs_folder]

            # Filesystem check first (cheap, no browser interaction needed)
            # for anything already clicked on a prior cycle.
            for d in list(dates):
                key = (gcs_folder, d)
                if key not in clicked:
                    continue
                stub = _expected_filename_stub(token, d)
                new_file = _check_for_new_file(DOWNLOAD_DIR, clicked[key], stub)
                if new_file:
                    tag_finder_file(os.path.join(DOWNLOAD_DIR, new_file))
                    downloaded[gcs_folder].append((d, new_file))
                    dates.remove(d)
                    print(f"Downloaded {new_file}")

            remaining = [d for d in dates if (gcs_folder, d) not in clicked]
            if not remaining:
                continue

            try:
                click_report_tab(driver, wait, tab_by_folder[gcs_folder])
                panel = _active_tabpanel(driver)
                reports_icon = panel.find_element(By.CSS_SELECTOR, 'button[data-testid="export-history-button"]')
                safe_click(driver, reports_icon)
                time.sleep(1)
            except Exception as e:
                # Confirmed live 2026-09-28: after enough tab-switch/download
                # cycles in one long-lived session, tab-clicking can start
                # timing out for every folder for the rest of the run (root
                # cause not pinned down -- some accumulated page state, not
                # a one-off animation). A plain "skip and retry next pass"
                # never recovered on its own; a full page reload does. This
                # is deliberately blunt rather than clever, since it's cheap
                # (one page load) and this loop runs for up to an hour anyway.
                print(f"WARNING: couldn't open Exported Reports panel for {gcs_folder}: {e} -- reloading page")
                try:
                    driver.get(AFFILIATE_URL)
                    time.sleep(2)
                except Exception as reload_err:
                    print(f"WARNING: page reload also failed: {reload_err}")
                continue

            # Confirmed live 2026-09-28: each row is a .pcm-ae-record-item
            # whose innerText includes the exact filename, and it only
            # contains a .pcm-ae-download-button once the export is ready
            # (an in-progress row shows a spinner/"please wait" instead).
            for d in remaining:
                stub = _expected_filename_stub(token, d)
                rows = []
                for r in driver.find_elements(By.CSS_SELECTOR, ".pcm-ae-record-item"):
                    try:
                        if stub in r.text:
                            rows.append(r)
                    except StaleElementReferenceException:
                        # The panel can re-render its row list while we're
                        # still iterating a previous snapshot of elements
                        # (e.g. a new export just finished and got prepended)
                        # -- confirmed live 2026-09-28. Just skip it; the
                        # next poll cycle re-fetches a fresh element list.
                        continue
                for row in rows:
                    try:
                        download_btns = row.find_elements(By.CSS_SELECTOR, ".pcm-ae-download-button")
                    except StaleElementReferenceException:
                        continue
                    if download_btns:
                        before = set(os.listdir(DOWNLOAD_DIR)) if os.path.isdir(DOWNLOAD_DIR) else set()
                        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                        safe_click(driver, download_btns[0])
                        clicked[(gcs_folder, d)] = before
                        print(f"Clicked Download for {stub} -- waiting for it to finish")
                    break
            time.sleep(0.5)

        if any(pending.values()):
            time.sleep(poll_interval)

    still_pending = {folder: dates for folder, dates in pending.items() if dates}
    return downloaded, still_pending


def _check_for_new_file(download_dir, before_names, expected_stub):
    """Non-blocking: is there already a new, size-stable file matching the
    stub? Two back-to-back listings a beat apart, not a long wait -- this
    gets called every poll cycle instead of blocking once."""
    after = set(os.listdir(download_dir))
    new_names = [n for n in (after - before_names) if expected_stub in n and not n.endswith((".crdownload", ".tmp"))]
    if not new_names:
        return None
    name = new_names[0]
    path = os.path.join(download_dir, name)
    size1 = os.path.getsize(path)
    time.sleep(1)
    if os.path.exists(path) and os.path.getsize(path) == size1:
        return name
    return None


def upload_downloaded_to_gcs(downloaded):
    """Uploads every successfully-downloaded file to
    gs://bucket_som/sales_parquet/raw/online/tiktok/affiliate/<gcs_folder>/<filename>,
    matching the layout the existing 1-4_*_csv_gcp.py converters already
    read from. Same retry+explicit-timeout pattern as gcs_upload_tiktok_raw.py
    (a plain default-timeout upload isn't reliable for larger files, e.g.
    Video's ~8MB reports -- confirmed elsewhere in this project 2026-09-28)."""
    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        os.path.expanduser("~/.config/gcloud/keys/odoo-service-account.json"),
    )
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET_NAME)

    uploaded, failed = [], []
    for gcs_folder, items in downloaded.items():
        for d, filename in items:
            local_path = os.path.join(DOWNLOAD_DIR, filename)
            local_size = os.path.getsize(local_path)
            blob = bucket.blob(f"{GCS_AFFILIATE_PREFIX}/{gcs_folder}/{filename}")

            last_err = None
            for attempt in range(3):
                try:
                    blob.upload_from_filename(local_path, timeout=600)
                    blob.reload()
                    if blob.size != local_size:
                        last_err = f"uploaded size {blob.size} != local size {local_size}"
                        continue
                    uploaded.append(filename)
                    last_err = None
                    break
                except Exception as e:
                    last_err = str(e)
                    time.sleep(5)

            if last_err:
                failed.append((filename, last_err))
                print(f"ERROR uploading {filename}: {last_err}")
            else:
                print(f"Uploaded {filename} -> gs://{GCS_BUCKET_NAME}/{GCS_AFFILIATE_PREFIX}/{gcs_folder}/")

    return uploaded, failed


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", help="YYYY-MM-DD, defaults to (today - 2 - days-back + 1)")
    ap.add_argument("--end", help="YYYY-MM-DD, defaults to today - 2 (the finalization lag)")
    ap.add_argument("--days-back", type=int, default=30)
    ap.add_argument("--dates", help="comma-separated YYYY-MM-DD list instead of a --start/--end range")
    # Confirmed live 2026-09-28: individual exports can take a long time to
    # both generate AND download once ready (Video's ~8MB file took several
    # minutes just to download after it finished generating) -- a full
    # 30-day x 4-report-type backfill (120 items) needs a much longer budget
    # than a single day's worth, hence a generous default here.
    ap.add_argument("--download-timeout", type=int, default=14400)
    return ap.parse_args()


def main():
    args = parse_args()

    if args.dates:
        dates = sorted(datetime.strptime(d.strip(), "%Y-%m-%d") for d in args.dates.split(",") if d.strip())
    else:
        end_dt = (
            datetime.strptime(args.end, "%Y-%m-%d") if args.end
            else datetime.now() - timedelta(days=FINALIZATION_LAG_DAYS)
        )
        start_dt = (
            datetime.strptime(args.start, "%Y-%m-%d") if args.start
            else end_dt - timedelta(days=args.days_back - 1)
        )
        dates = []
        d = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        while d <= end_dt:
            dates.append(d)
            d += timedelta(days=1)

    print(f"Exporting {len(dates)} day(s) x {len(REPORT_TYPES)} report type(s): "
          f"{dates[0]:%Y-%m-%d} .. {dates[-1]:%Y-%m-%d}")

    driver = build_driver(download_dir=DOWNLOAD_DIR)
    wait = WebDriverWait(driver, 20)
    try:
        ensure_logged_in(driver, wait, interactive=False)
        triggered = trigger_all(driver, wait, dates)
        total_triggered = sum(len(v) for v in triggered.values())
        print(f"\n{total_triggered}/{len(dates) * len(REPORT_TYPES)} export(s) queued. Polling for completion...")
        downloaded, pending = download_ready(driver, wait, triggered, timeout=args.download_timeout)
    finally:
        driver.quit()

    total_downloaded = sum(len(v) for v in downloaded.values())
    print(f"\n{total_downloaded} file(s) downloaded to {DOWNLOAD_DIR}")
    for folder, dates_missing in pending.items():
        if dates_missing:
            print(f"WARNING: {folder}: {len(dates_missing)} date(s) never finished generating: "
                  f"{', '.join(d.strftime('%Y-%m-%d') for d in dates_missing)}")

    if total_downloaded:
        print(f"\nUploading {total_downloaded} file(s) to GCS...")
        uploaded, failed = upload_downloaded_to_gcs(downloaded)
        print(f"\n{len(uploaded)}/{total_downloaded} file(s) uploaded to "
              f"gs://{GCS_BUCKET_NAME}/{GCS_AFFILIATE_PREFIX}/")
        if failed:
            print(f"WARNING: {len(failed)} upload(s) failed:")
            for filename, err in failed:
                print(f"  {filename}: {err}")

    return downloaded


if __name__ == "__main__":
    main()
