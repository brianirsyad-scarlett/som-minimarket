"""Daily Tokopedia seller-center order export.

Meant to run unattended from launchd every day. By default it exports one day
at a time for the rolling window [today-45 days, today] -- pass --start/--end
to override for a manual backfill.

Tokopedia's export is asynchronous: clicking "Export" only queues a report in
the "Export history" list -- the actual file is only saved once you separately
click that row's own Download button, and at this account's order volume
(thousands/day) a single day's report can take a long time to generate, or
occasionally seem to never finish. So this runs in two phases instead of one
trigger-then-immediately-download step per day:

  Phase 1 (trigger_all_exports): queue all 45 days' reports, one at a time.
  Tokopedia's own report filename encodes the TRIGGER time, not the target
  date filter, and generation finishes in server-side order (which varies
  with each day's order volume), not trigger order -- so there's no way to
  work out afterwards which finished report belongs to which day. Instead,
  right after clicking Export for a given day, this watches the history list
  for the one new row that appears (still disabled/generating) and records
  that EXACT filename as the answer for that day. Ground truth, captured live,
  not inferred.

  Phase 2 (download_ready_reports): poll the Export history list and download
  each day's report by its exact recorded filename as soon as that filename's
  Download button is enabled. Anything still generating when Phase 2's timeout
  is hit is left pending -- rerun with --resume-dir to keep polling for it
  without re-triggering (which would just queue a duplicate report), since
  Tokopedia keeps generating it server-side regardless of whether this script
  is running.

Each file is downloaded locally first (into exports/<run-timestamp>/), renamed
to its target date, then copied into the OneDrive "TikTok Seller Data" folder --
launchd-driven Chrome downloads straight into an OneDrive-synced folder are
unreliable (same lesson as the SCRWMS/Alfamart automations), so keep the
browser writing locally only.
"""
import argparse
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

from tokped_common import ORDERS_URL, EXPORTS_DIR, build_driver, ensure_logged_in, wait_clickable, close_stray_windows, tag_finder_file

ONEDRIVE_EXPORT_DIR = (
    "/Users/salesops/Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/"
    "SOM/Anchanto Report/E-Commerce Data/TikTok Seller Data"
)

TRIGGERED_MAP_FILENAME = "triggered_map.json"

# Finds every "All order-<date>-<time>.csv" filename currently rendered in the
# Export history list, plus whether that row's Download button is disabled
# (still generating) -- restricted to this exact report type/pattern so it
# never picks up Tokopedia's other unrelated report types (e.g. "Untuk
# Dikirim pesanan..." shipping reminders) that share the same list.
ALL_ROWS_JS = r"""
const rows = [];
const all = Array.from(document.querySelectorAll('*'));
const seen = new Set();
for (const el of all) {
    if (el.children.length > 0) continue;
    const text = (el.textContent || '').trim();
    const m = text.match(/^(All order-\d{4}-\d{2}-\d{2}-\d{2}:\d{2}\.csv)$/);
    if (!m) continue;
    if (seen.has(m[1])) continue;
    seen.add(m[1]);
    let row = el;
    let disabled = true;
    for (let i = 0; i < 8 && row; i++) {
        row = row.parentElement;
        if (!row) break;
        const b = row.querySelectorAll('button');
        if (b.length > 0 && b.length < 5) {
            disabled = Array.from(b).some(x => x.disabled);
            break;
        }
    }
    rows.push({name: m[1], disabled});
}
return rows;
"""

CLICK_BY_NAME_JS = r"""
const targetName = arguments[0];
const buttons = Array.from(document.querySelectorAll('button')).filter(
    b => (b.innerText || '').trim() === 'Download' && !b.disabled
);
for (const btn of buttons) {
    let row = btn;
    let name = null;
    for (let i = 0; i < 6 && row; i++) {
        row = row.parentElement;
        if (!row) break;
        const m = (row.textContent || '').match(/(All order-\d{4}-\d{2}-\d{2}-\d{2}:\d{2}\.csv)/);
        if (m) { name = m[1]; break; }
    }
    if (name === targetName) {
        btn.scrollIntoView({block: 'center'});
        btn.click();
        return true;
    }
}
return false;
"""


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", help="YYYY-MM-DD, defaults to 45 days before today")
    ap.add_argument("--end", help="YYYY-MM-DD, defaults to today")
    ap.add_argument("--days-back", type=int, default=45, help="used when --start is omitted")
    ap.add_argument("--download-timeout", type=int, default=1800, help="seconds to wait for reports to finish generating (phase 2)")
    ap.add_argument("--resume-dir", help="skip Phase 1 (no new exports triggered) and just keep polling/downloading the pending days recorded in this run's exports/<...>/triggered_map.json")
    ap.add_argument("--dates", help="comma-separated YYYY-MM-DD list of specific (possibly non-contiguous) days to re-export, instead of a --start/--end range -- e.g. for refreshing a scattered set of previously-downloaded days")
    return ap.parse_args()


def safe_click(driver, element):
    """Executes standard click with JS fallback if overlay intervenes."""
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def generate_date_range(start_date, end_date):
    """Generates individual dates sequentially."""
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


DAY_CELL_JS = """
const targetMonth = arguments[0], targetYear = arguments[1], day = arguments[2];
const headers = Array.from(document.querySelectorAll('.p-picker-header-value'));
for (const h of headers) {
    // Exact "MM/YYYY" match -- a loose .includes() check is a real bug: the
    // header format is "MM/YYYY" (e.g. "09/2026"), and for year 2026
    // specifically, month "02" is a substring of "2026" itself ("20-02-6"),
    // so .includes("02") && .includes("2026") was TRUE for every header in
    // 2026 regardless of which month was actually shown -- confirmed
    // 2026-09-28 as the cause of several dates silently exporting the wrong
    // month's data (day-of-month stayed right, month became whatever was
    // actually on screen).
    if (h.textContent.trim() !== (targetMonth + "/" + targetYear)) continue;
    // walk up from this month's header until we reach the ancestor that also
    // contains that month's day grid (the picker shows two months side by
    // side, so we must stay scoped to the matching panel, not just grab the
    // first day cell with a matching number from whichever panel is first).
    let node = h;
    for (let i = 0; i < 8 && node; i++) {
        node = node.parentElement;
        if (node && node.querySelector('.p-picker-cell-in-view')) {
            const cells = Array.from(node.querySelectorAll('.p-picker-cell-in-view .p-picker-date-value'));
            const match = cells.find(c => c.textContent.trim() === day);
            if (match) return match;
            break;
        }
    }
}
return null;
"""


def _any_header_matches(driver, target_month_str, target_year_str):
    """True if either visible calendar panel (the picker shows two months
    side by side) is already showing the target month/year.

    Requires an exact "MM/YYYY" match, not a substring check -- see
    DAY_CELL_JS's comment for why a loose .includes()-style check on month
    "02" falsely matches any 2026 header (the year "2026" itself contains
    "02" as a substring), which made navigate_to_target_month believe it had
    already reached February without ever actually navigating there.
    """
    target = f"{target_month_str}/{target_year_str}"
    for h in driver.find_elements(By.CSS_SELECTOR, '.p-picker-header-value'):
        if h.text.strip() == target:
            return True
    return False


def _current_header_month_year(driver):
    """Parses the first visible calendar panel's 'MM/YYYY' header text into
    (month, year), or None if it can't be read."""
    headers = driver.find_elements(By.CSS_SELECTOR, '.p-picker-header-value')
    if not headers:
        return None
    m = re.match(r"(\d{2})/(\d{4})", headers[0].text.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def navigate_to_target_month(driver, target_date):
    """Adjusts calendar view to match target month/year.

    Checks BOTH visible panels, not just the first -- a picker showing two
    months at once (e.g. Aug/Sep) only ever has one header match if the
    target is Sep, and checking just the first (Aug) panel would never
    match, clicking "previous" forever past the target with no way to stop.

    The picker's scroll position PERSISTS across separate Filter-panel opens
    within the same page session (confirmed 2026-09-28, re-exporting a
    scattered/non-contiguous list of dates): after processing a January day
    the view stays on Jan/Feb, and a later March target is FORWARD of that,
    not backward. A previous-only search can only ever find months further
    in the past than wherever the picker currently happens to be sitting --
    it silently searches the wrong direction and exhausts max_clicks. So the
    direction (the 'previous' or 'next' arrow) is chosen each call based on
    whether the target is before or after the currently-visible month, not
    hardcoded to 'previous'.
    """
    target_month_str = target_date.strftime("%m")
    target_year_str = target_date.strftime("%Y")
    max_clicks = 36  # 3 years in either direction is far more than this job ever needs

    if _any_header_matches(driver, target_month_str, target_year_str):
        return

    current = _current_header_month_year(driver)
    if current is None:
        forward = False  # can't tell -- fall back to the old backward-only search
    else:
        cur_month, cur_year = current
        target_total = target_date.year * 12 + target_date.month
        forward = target_total > (cur_year * 12 + cur_month)

    icon_class = "arco-icon-right" if forward else "arco-icon-left"

    for _ in range(max_clicks):
        try:
            if _any_header_matches(driver, target_month_str, target_year_str):
                return
            nav_btn = driver.find_element(
                By.XPATH, f"//*[contains(@class, '{icon_class}')]/ancestor::div[contains(@class, 'p-picker-header-icon')]"
            )
            safe_click(driver, nav_btn)
            time.sleep(0.5)
        except Exception as e:
            print(f"Calendar navigation warning: {e}")
            return

    raise RuntimeError(f"Could not navigate the date picker to {target_month_str}/{target_year_str}")


def find_day_cell(driver, target_date):
    return driver.execute_script(
        DAY_CELL_JS, target_date.strftime("%m"), target_date.strftime("%Y"), target_date.strftime("%d")
    )


def trigger_all_exports(driver, wait, dates):
    """Phase 1: queue one export per day, oldest to newest. `dates` is any
    iterable of datetime objects -- a contiguous generate_date_range() or an
    explicit scattered list (e.g. re-refreshing specific days). Returns
    {date: report_name} -- the exact filename Tokopedia assigned to each
    day's report, captured live right after triggering it (see module
    docstring for why this replaces position/order-based matching)."""
    print(f"Navigating to Orders page: {ORDERS_URL}")
    driver.get(ORDERS_URL)
    time.sleep(3)

    print("Selecting 'All' orders tab...")
    all_tab = wait_clickable(driver, wait, (By.CSS_SELECTOR, 'div[data-log_click_for="all"]'))
    safe_click(driver, all_tab)
    time.sleep(1)

    triggered = {}
    main_handle = driver.current_window_handle
    last_used_minute = None  # see the wait-for-minute-rollover note below

    for single_date in sorted(dates):
        formatted_date_log = single_date.strftime("%d %B %Y")
        print(f"\n--- Triggering export for: {formatted_date_log} ---")

        try:
            if len(driver.window_handles) > 1:
                close_stray_windows(driver, main_handle)

            print("Opening Filter panel...")
            filter_btn = wait_clickable(
                driver, wait,
                (By.XPATH, "//span[text()='Filter'] | //*[contains(@class, 'sc-fnVmsx') and text()='Filter']"),
            )
            safe_click(driver, filter_btn)
            time.sleep(1)

            print("Opening Date Picker...")
            from_input = wait_clickable(driver, wait, (By.CSS_SELECTOR, 'input[placeholder="From"]'))
            safe_click(driver, from_input)
            time.sleep(1)

            navigate_to_target_month(driver, single_date)

            day_cell = None
            for _ in range(10):
                day_cell = find_day_cell(driver, single_date)
                if day_cell is not None:
                    break
                time.sleep(0.3)
            if day_cell is None:
                raise RuntimeError(f"Could not find the day cell for {single_date:%Y-%m-%d} in the date picker")

            safe_click(driver, day_cell)
            time.sleep(0.3)
            # Re-find rather than reuse the same reference -- selecting a date
            # can cause a re-render that leaves the old element stale.
            day_cell = find_day_cell(driver, single_date) or day_cell
            safe_click(driver, day_cell)
            time.sleep(0.5)

            print("Applying filter...")
            apply_btn = wait_clickable(driver, wait, (By.CSS_SELECTOR, 'button[data-log_click_for="apply"]'))
            safe_click(driver, apply_btn)
            time.sleep(2)

            print("Opening Export panel (export_entry)...")
            export_entry_btn = wait_clickable(driver, wait, (By.CSS_SELECTOR, 'button[data-log_click_for="export_entry"]'))
            safe_click(driver, export_entry_btn)
            time.sleep(1.5)

            # Snapshot the history list now that the panel (and its
            # pre-existing rows) is actually open and rendered -- snapshotting
            # before the panel opens sees ~0 rows, which would make every
            # pre-existing row look "new" below.
            before_names = {r["name"] for r in driver.execute_script(ALL_ROWS_JS)}

            # Tokopedia's own report filename only has MINUTE granularity
            # (e.g. "All order-2026-09-16-17:01.csv") and carries no other
            # identifying info -- two days triggered inside the same clock
            # minute get the literally identical, indistinguishable filename
            # (confirmed live: two separate DOM rows with the same text).
            # Since that name is the only handle Phase 2 has to click the
            # right row -- including on a completely fresh page load via
            # --resume-dir, where no DOM/JS state from this run survives --
            # the only reliable fix is to never let two days share a minute.
            if last_used_minute == datetime.now().strftime("%H:%M"):
                while datetime.now().strftime("%H:%M") == last_used_minute:
                    time.sleep(1)

            print("Clicking Export (queues the report)...")
            export_confirm_btn = wait_clickable(driver, wait, (By.CSS_SELECTOR, 'button[data-log_click_for="export"]'))
            safe_click(driver, export_confirm_btn)

            new_name = None
            deadline = time.time() + 15
            while time.time() < deadline:
                rows = driver.execute_script(ALL_ROWS_JS)
                new_rows = [r["name"] for r in rows if r["name"] not in before_names]
                if new_rows:
                    new_name = new_rows[0]
                    break
                time.sleep(0.3)

            if not new_name:
                raise RuntimeError("Export queued but no new report row appeared in history within 15s")

            triggered[single_date] = new_name
            last_used_minute = datetime.now().strftime("%H:%M")
            print(f"Queued export for {formatted_date_log} -> {new_name}")
            time.sleep(1)  # brief pacing buffer before the next trigger

        except Exception as day_err:
            print(f"WARNING: failed to queue export for {formatted_date_log}: {day_err}")
            continue

    return triggered


def _wait_for_expected_download(run_dir, expected_name, timeout=30):
    """Wait for the specific file this click should produce, not just "any
    new file" -- Tokopedia's own page can trigger unrelated downloads on its
    own (e.g. a periodic shipping-reminder report) once Chrome is told to
    allow downloads without prompting, and those can land in run_dir around
    the same time as ours. Matching "whatever's newest" risked renaming that
    unrelated file to our target date instead of the real one.

    Chrome sanitizes ':' (invalid in filenames) to '_' when saving, and may
    append " (1)", " (2)", etc. if a same-named file already exists in this
    run_dir (e.g. two reports triggered in the same minute).
    """
    base, ext = os.path.splitext(expected_name.replace(":", "_"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        matches = [
            f for f in os.listdir(run_dir)
            if f.startswith(base) and f.endswith(ext) and not f.endswith((".crdownload", ".tmp"))
        ]
        if matches:
            name = matches[0]
            path = os.path.join(run_dir, name)
            size1 = os.path.getsize(path)
            time.sleep(1)
            if os.path.exists(path) and os.path.getsize(path) == size1:
                return name
        time.sleep(0.5)
    return None


def download_ready_reports(driver, wait, triggered_map, run_dir, timeout=480, poll_interval=8):
    """Phase 2: poll the Export history list and download each day's report
    by its exact recorded filename as soon as that filename's Download button
    is enabled. Returns (downloaded, still_pending) where downloaded maps
    date -> local filename and still_pending is the list of dates that never
    finished generating within the timeout."""

    def panel_is_open():
        # Presence alone is unreliable -- "Export history" text can exist
        # elsewhere in the page (e.g. an embedded i18n string blob) even when
        # the panel was never actually opened, which previously made this
        # skip clicking export_entry and silently see zero rows all run.
        try:
            return any(
                el.is_displayed()
                for el in driver.find_elements(By.XPATH, "//*[contains(text(),'Export history')]")
            )
        except Exception:
            return False

    if not panel_is_open():
        export_entry_btn = wait_clickable(driver, wait, (By.CSS_SELECTOR, 'button[data-log_click_for="export_entry"]'))
        safe_click(driver, export_entry_btn)
        try:
            WebDriverWait(driver, 10).until(lambda d: panel_is_open())
        except TimeoutException:
            print("WARNING: clicked to open the Export panel but it still doesn't look open")

    pending = dict(triggered_map)  # date -> report_name; shrinks as each completes
    downloaded = {}
    deadline = time.time() + timeout

    while pending and time.time() < deadline:
        try:
            rows = {r["name"]: r["disabled"] for r in driver.execute_script(ALL_ROWS_JS)}
        except Exception as e:
            print(f"WARNING: couldn't read export history rows: {e}")
            rows = {}

        matched_this_pass = False
        for target_date, report_name in list(pending.items()):
            if rows.get(report_name) is not False:
                continue  # not seen yet, or still generating (disabled)

            clicked = driver.execute_script(CLICK_BY_NAME_JS, report_name)
            if not clicked:
                continue
            new_file = _wait_for_expected_download(run_dir, report_name, timeout=30)
            if new_file:
                target_name = f"{target_date:%Y-%m-%d}{os.path.splitext(new_file)[1]}"
                target_path = os.path.join(run_dir, target_name)
                os.rename(os.path.join(run_dir, new_file), target_path)
                tag_finder_file(target_path)
                downloaded[target_date] = target_name
                del pending[target_date]
                print(f"Downloaded {target_name} (from '{report_name}')")
            else:
                print(f"WARNING: clicked Download for '{report_name}' but no new file appeared in {run_dir}")
            # Stop iterating this snapshot and re-query fresh next loop --
            # the list can re-render after a click, which would make the
            # rest of this snapshot's disabled/enabled info stale.
            matched_this_pass = True
            break

        if pending and not matched_this_pass:
            time.sleep(poll_interval)

    return downloaded, list(pending.keys())


def _save_pending_map(run_dir, triggered_map, pending_dates):
    """(Re)write triggered_map.json to just the days still not downloaded, so
    a later `--resume-dir` run knows exactly what's left to poll for -- and so
    the file disappears once nothing's left to resume."""
    map_path = os.path.join(run_dir, TRIGGERED_MAP_FILENAME)
    if not pending_dates:
        try:
            os.remove(map_path)
        except FileNotFoundError:
            pass
        return
    remaining = {d.strftime("%Y-%m-%d"): triggered_map[d] for d in pending_dates}
    with open(map_path, "w") as f:
        json.dump(remaining, f, indent=2)


def _copy_to_onedrive_with_retry(src, dest, attempts=5, delay=3):
    """shutil.copy2 into the OneDrive folder occasionally hits
    'OSError: [Errno 11] Resource deadlock avoided' -- OneDrive's sync daemon
    transiently locking the destination file. That's not fatal, just needs a
    moment to clear, but an unhandled OSError here used to crash the whole
    run (losing every other day's copy, and skipping the chained TikTok
    parquet rebuild) even though every day had already downloaded fine."""
    for attempt in range(1, attempts + 1):
        try:
            shutil.copy2(src, dest)
            return True
        except OSError as e:
            print(f"WARNING: copy attempt {attempt}/{attempts} failed for {os.path.basename(dest)}: {e}")
            if attempt < attempts:
                time.sleep(delay)
    print(f"ERROR: giving up copying {os.path.basename(dest)} to OneDrive after {attempts} attempts -- still in {os.path.dirname(src)}")
    return False


def _finish(run_dir, downloaded, pending, not_triggered=0):
    print(f"\n{len(downloaded)} file(s) downloaded to {run_dir}")

    if downloaded:
        os.makedirs(ONEDRIVE_EXPORT_DIR, exist_ok=True)
        copied = 0
        for name in downloaded.values():
            dest = os.path.join(ONEDRIVE_EXPORT_DIR, name)
            if _copy_to_onedrive_with_retry(os.path.join(run_dir, name), dest):
                tag_finder_file(dest)  # shutil.copy2 should carry the xattr over, but don't rely on it
                copied += 1
        print(f"Copied {copied}/{len(downloaded)} file(s) to {ONEDRIVE_EXPORT_DIR}")

    # Anything else sitting in run_dir is a report we did NOT ask for (e.g. an
    # unrelated report type, or a duplicate flushed alongside a real one) --
    # never copy those anywhere, and don't leave them cluttering local disk.
    stray = [f for f in os.listdir(run_dir) if f not in downloaded.values() and f != TRIGGERED_MAP_FILENAME and not f.startswith(".")]
    if stray:
        print(f"\nRemoving {len(stray)} file(s) not matching a requested day (not copied anywhere): {stray}")
        for f in stray:
            os.remove(os.path.join(run_dir, f))

    if not_triggered:
        print(f"\n{not_triggered} day(s) failed to even queue an export this run.")
    if pending:
        pending_str = ", ".join(d.strftime("%Y-%m-%d") for d in pending)
        print(f"{len(pending)} day(s) queued but not ready within the timeout (rerun with --resume-dir {run_dir} to keep polling): {pending_str}")

    if not_triggered or pending:
        raise SystemExit(1)

    print("\nAll daily exports finished processing!")


def main():
    args = parse_args()

    if args.resume_dir:
        run_dir = args.resume_dir
        map_path = os.path.join(run_dir, TRIGGERED_MAP_FILENAME)
        with open(map_path) as f:
            raw_map = json.load(f)
        triggered = {datetime.strptime(k, "%Y-%m-%d"): v for k, v in raw_map.items()}
        print(f"Resuming: {len(triggered)} pending day(s) from {map_path}")

        driver = build_driver(download_dir=run_dir)
        wait = WebDriverWait(driver, 20)
        try:
            ensure_logged_in(driver, wait, interactive=False)
            driver.get(ORDERS_URL)
            downloaded, pending = download_ready_reports(driver, wait, triggered, run_dir, timeout=args.download_timeout)
        finally:
            driver.quit()

        _save_pending_map(run_dir, triggered, pending)
        _finish(run_dir, downloaded, pending)
        return

    if args.dates:
        dates = sorted(
            datetime.strptime(d.strip(), "%Y-%m-%d") for d in args.dates.split(",") if d.strip()
        )
        date_desc = f"{len(dates)} specific date(s): {', '.join(d.strftime('%Y-%m-%d') for d in dates)}"
    else:
        end_dt = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.now()
        start_dt = (
            datetime.strptime(args.start, "%Y-%m-%d") if args.start
            else end_dt - timedelta(days=args.days_back)
        )
        # Normalize to midnight so comparisons/iteration are calendar-day based.
        start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        dates = list(generate_date_range(start_dt, end_dt))
        date_desc = f"{start_dt:%Y-%m-%d} .. {end_dt:%Y-%m-%d}"

    run_dir = os.path.join(EXPORTS_DIR, datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)

    print(f"Date range: {date_desc}")
    print(f"Local download dir: {run_dir}")

    driver = build_driver(download_dir=run_dir)
    wait = WebDriverWait(driver, 20)

    triggered, downloaded, pending = {}, {}, []
    try:
        ensure_logged_in(driver, wait, interactive=False)
        triggered = trigger_all_exports(driver, wait, dates)
        print(f"\n{len(triggered)}/{len(dates)} day(s) queued. Now polling for completed reports...")
        # Persist right away -- if this process dies mid Phase 2, `--resume-dir`
        # can still pick up every day that was successfully queued.
        _save_pending_map(run_dir, triggered, list(triggered.keys()))
        downloaded, pending = download_ready_reports(driver, wait, triggered, run_dir, timeout=args.download_timeout)
    finally:
        driver.quit()

    _save_pending_map(run_dir, triggered, pending)
    not_triggered = len(dates) - len(triggered)
    _finish(run_dir, downloaded, pending, not_triggered=not_triggered)


if __name__ == "__main__":
    main()
