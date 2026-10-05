#!/usr/bin/env python3
"""
scrwms_report.py -- automate report generation on scrwms.anchanto.com (Anchanto SCRWMS)

Reconstructed from a browser HAR capture. The flow is:

  1. POST /api/login                     -> JWT returned in the `Authorization` response header
  2. POST /api/v1/report_schedules       -> creates the report job (also emails it to mailing_list)
  3. GET  /api/v1/report_schedules?...   -> poll until the row's `report_url` (pre-signed S3) is filled
  4. GET  <report_url>                   -> download the CSV/XLS file (no auth; link valid ~7 days)

CREDENTIALS are read from the environment, never hard-coded:
    export SCRWMS_EMAIL='you@example.com'
    export SCRWMS_PASSWORD='...'

Optional env:
    SCRWMS_API_BASE        default https://scrwms-api.anchanto.com
    SCRWMS_WAREHOUSE_CODE  default SCR   (sent as the `warehouse` header)
    SCRWMS_WEB_ORIGIN      default https://scrwms.anchanto.com

Usage:
    python3 scrwms_report.py generate --profile b2c_order_report.json --last-days 7
    python3 scrwms_report.py generate --profile b2c_order_report.json --from 2026-09-01 --to 2026-09-10
    python3 scrwms_report.py generate --profile b2c_order_report.json --month-to-date --no-download
    python3 scrwms_report.py list-types
    python3 scrwms_report.py list-fields 3
    python3 scrwms_report.py list-occurrences
    python3 scrwms_report.py list-companies --warehouse-ids 1

A "profile" is a small JSON file describing the report (see b2c_order_report.json,
which reproduces the exact request captured in the HAR).
"""

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Local, non-cloud-synced home for this script's own bookkeeping and a safety-net
# copy of every export. Cloud-provider folders (OneDrive/iCloud) intermittently
# refuse reads/writes from background (launchd) processes -- see download() and
# _state_path() below -- so nothing this script *depends on* should live there.
_HOME_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_STATE_DIR = os.path.join(_HOME_DIR, ".scrwms_state")
LOCAL_EXPORT_DIR = os.path.join(_HOME_DIR, "exports")

API_BASE = os.environ.get("SCRWMS_API_BASE", "https://scrwms-api.anchanto.com").rstrip("/")
WEB_ORIGIN = os.environ.get("SCRWMS_WEB_ORIGIN", "https://scrwms.anchanto.com").rstrip("/")
WAREHOUSE_CODE = os.environ.get("SCRWMS_WAREHOUSE_CODE", "SCR")

TIMEOUT = 120


class ApiError(RuntimeError):
    pass


def _headers(token=None, json_body=False):
    h = {
        "Accept": "application/json",
        "Origin": WEB_ORIGIN,
        "Referer": WEB_ORIGIN + "/",
        "warehouse": WAREHOUSE_CODE,
        "x-language": "en",
        "x-ui-request-id": str(random.randint(1, 2_000_000_000)),
        "User-Agent": "scrwms-report-automation/1.0",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = token if token.lower().startswith("bearer ") else "Bearer " + token
    return h


def _request(method, url, token=None, body=None, expect_json=True):
    data = None
    headers = _headers(token=token, json_body=body is not None)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:2000]
        raise ApiError(f"{method} {url} -> HTTP {e.code}\n{detail}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"{method} {url} -> {e}") from None
    raw = resp.read()
    payload = json.loads(raw.decode("utf-8")) if expect_json and raw else raw
    return resp, payload


def login():
    email = os.environ.get("SCRWMS_EMAIL")
    password = os.environ.get("SCRWMS_PASSWORD")
    if not email or not password:
        sys.exit("ERROR: set SCRWMS_EMAIL and SCRWMS_PASSWORD in the environment.")
    resp, payload = _request(
        "POST",
        f"{API_BASE}/api/login",
        body={"api_user": {"email": email, "password": password}},
    )
    # JWT comes back in a response header, not the body.
    token = None
    for k, v in resp.getheaders():
        if k.lower() == "authorization" and v.strip():
            token = v.strip()
            break
    if not token:
        raise ApiError("login succeeded but no Authorization header was returned")
    user = (payload or {}).get("data", {}).get("attributes", {})
    print(f"Logged in as {user.get('full_name') or email} "
          f"(company_id={user.get('company_id')}, warehouse_id={user.get('warehouse_id')})")
    return token


def js_date(d: dt.date) -> str:
    # The web app sends dates as JS Date.toDateString(), e.g. "Tue Sep 01 2026".
    return d.strftime("%a %b %d %Y")


def resolve_dates(args):
    today = dt.date.today()
    if args.from_date and args.to_date:
        start = dt.date.fromisoformat(args.from_date)
        end = dt.date.fromisoformat(args.to_date)
    elif args.last_days is not None:
        end = today
        start = today - dt.timedelta(days=args.last_days - 1)
    elif args.yesterday:
        start = end = today - dt.timedelta(days=1)
    elif args.month_to_date:
        start = today.replace(day=1)
        end = today
    elif args.today:
        start = end = today
    else:
        return None, None  # use whatever the profile specifies
    return start, end


def build_schedule_body(profile, start, end):
    sched = dict(profile.get("report_schedule", {}))
    if start and end:
        sched["from_date"] = js_date(start)
        sched["end_date"] = js_date(end)
    # normalise types the API expects as strings
    for key in ("report_type_id", "report_occurrence_id"):
        if key in sched and sched[key] is not None:
            sched[key] = str(sched[key])
    sched["field_ids"] = [str(x) for x in sched.get("field_ids", [])]
    filters = sched.get("filters", {})
    for fk, fv in list(filters.items()):
        if isinstance(fv, list):
            filters[fk] = [str(x) for x in fv]
    return {"report_schedule": sched}


def find_row(rows, schedule_id):
    for r in rows:
        if str(r.get("id")) == str(schedule_id):
            return r
    return None


def poll_until_ready(token, schedule_id, poll_every=10, max_wait=1800):
    url = (f"{API_BASE}/api/v1/report_schedules?page[size]=20"
           "&include=report_occurrence,report_type,created_by,latest_report")
    waited = 0
    while True:
        _, payload = _request("GET", url, token=token)
        row = find_row(payload.get("data", []), schedule_id)
        if row is None:
            raise ApiError(f"schedule {schedule_id} not found while polling")
        attrs = row.get("attributes", {})
        state = attrs.get("state")
        status = attrs.get("status")
        report_url = attrs.get("report_url") or ""
        prog = attrs.get("progress_status") or {}
        pct = prog.get("percentage")
        print(f"  [{waited:>4}s] state={state} status={status} "
              f"progress={pct}% ({prog.get('at')}/{prog.get('total')})")
        if report_url:
            return report_url, attrs
        if str(state).lower() in {"failed", "error"} or str(status).lower() in {"failed", "error"}:
            raise ApiError(f"report generation failed: {json.dumps(attrs)[:500]}")
        if waited >= max_wait:
            raise ApiError(f"timed out after {max_wait}s waiting for report_url "
                           f"(schedule {schedule_id}); it will still arrive by email")
        time.sleep(poll_every)
        waited += poll_every


def sync_to(local_path, out_dir, fname, attempts=5):
    """Best-effort copy of an already-downloaded local file into `out_dir` (which may
    be a OneDrive/iCloud-synced folder). Those folders reject overwriting a file they
    already have fully synced when the write comes from a background process (EPERM) --
    removing the destination first turns the write into a fresh create, which succeeds.
    Retries with backoff; never raises -- caller gets the local path back on failure."""
    dest = os.path.join(out_dir, fname)
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            os.makedirs(out_dir, exist_ok=True)
            if os.path.exists(dest):
                try:
                    os.remove(dest)
                except OSError as rm_err:
                    print(f"  (could not remove old {dest} first: {rm_err}; trying overwrite anyway)")
            shutil.copyfile(local_path, dest)
            print(f"  synced -> {dest}" + (f"  (attempt {attempt})" if attempt > 1 else ""))
            return dest
        except OSError as e:
            last_err = e
            print(f"  sync attempt {attempt}/{attempts} to {dest} failed ({e}); retrying")
            time.sleep(min(2 ** attempt, 30))
    print(f"  WARNING: could not sync to {dest} after {attempts} attempts ({last_err}). "
          f"Report is safe locally at {local_path} -- copy it over manually.")
    return local_path


def download(report_url, out_dir, filename=None, fetch_attempts=5):
    """Always save to LOCAL_EXPORT_DIR first (a plain local folder, never a cloud
    provider path, so this write can never be blocked). Then best-effort copy that
    file into `out_dir` via sync_to().

    The fetch itself (report_url -> local file) is retried: SCRWMS can set
    `report_url` on the schedule a moment before the underlying S3 object has
    actually finished materializing, which shows up as a 404 on the very first
    request. Raises ApiError if every attempt fails -- caller decides what that
    means for the run (see poll_and_download)."""
    os.makedirs(LOCAL_EXPORT_DIR, exist_ok=True)
    fname = filename  # may still be None; refined below from Content-Disposition
    last_err = None
    for attempt in range(1, fetch_attempts + 1):
        req = urllib.request.Request(report_url, method="GET",
                                     headers={"User-Agent": "scrwms-report-automation/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                if not fname:
                    cd = resp.headers.get("Content-Disposition", "")
                    m = re.search(r'filename="?([^";]+)"?', cd)
                    fname = m.group(1) if m else f"scrwms_report_{int(time.time())}.csv"
                local_path = os.path.join(LOCAL_EXPORT_DIR, fname)
                total = 0
                with open(local_path, "wb") as fh:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        total += len(chunk)
            print(f"Downloaded {total:,} bytes -> {local_path}")
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            print(f"  fetch attempt {attempt}/{fetch_attempts} of report_url failed ({e}); "
                  f"retrying (S3 object may still be materializing)")
            time.sleep(min(5 * attempt, 30))
    else:
        raise ApiError(f"could not fetch report_url after {fetch_attempts} attempts: {last_err}")

    if os.path.abspath(out_dir) == os.path.abspath(LOCAL_EXPORT_DIR):
        return local_path
    return sync_to(local_path, out_dir, fname)


def upload_to_gcs(local_path, bucket_name, prefix, key_path, blob_name=None, attempts=3):
    """Upload one local file to gs://bucket_name/prefix/<blob_name or basename>.
    Requires google-cloud-storage (pip install google-cloud-storage) and a
    service-account key file. Never raises -- logs and returns False on failure so
    a GCS hiccup can't take down the rest of the run."""
    try:
        from google.cloud import storage  # lazy import: only needed when --gcs-* is used
    except ImportError:
        print("  GCS SKIPPED: google-cloud-storage not installed "
              "(pip install google-cloud-storage)")
        return False

    fname = blob_name or os.path.basename(local_path)
    blob_path = f"{prefix.strip('/')}/{fname}"
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            client = storage.Client.from_service_account_json(key_path)
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            blob.upload_from_filename(local_path)
            print(f"  GCS -> gs://{bucket_name}/{blob_path}"
                  + (f"  (attempt {attempt})" if attempt > 1 else ""))
            return True
        except Exception as e:  # noqa: BLE001 -- any GCS/auth error, never crash the run
            last_err = e
            print(f"  GCS upload attempt {attempt}/{attempts} failed ({e}); retrying")
            time.sleep(min(2 ** attempt, 20))
    print(f"  GCS WARNING: could not upload {fname} after {attempts} attempts ({last_err})")
    return False


def cmd_generate(args):
    with open(args.profile) as fh:
        profile = json.load(fh)
    start, end = resolve_dates(args)
    body = build_schedule_body(profile, start, end)

    if args.mailing_list:
        body["report_schedule"]["mailing_list"] = args.mailing_list
    if args.format:
        body["report_schedule"]["report_format"] = args.format

    sched = body["report_schedule"]
    print("Report request:")
    print(f"  type_id={sched.get('report_type_id')} format={sched.get('report_format')} "
          f"occurrence_id={sched.get('report_occurrence_id')}")
    print(f"  dates: {sched.get('from_date')}  ->  {sched.get('end_date')}")
    print(f"  filters: {json.dumps(sched.get('filters', {}))}")
    print(f"  fields: {len(sched.get('field_ids', []))} columns")
    print(f"  mailing_list: {sched.get('mailing_list')}")
    if args.dry_run:
        print("\n--dry-run: request body below, nothing sent.\n")
        print(json.dumps(body, indent=2))
        return

    token = login()
    _, payload = _request("POST", f"{API_BASE}/api/v1/report_schedules", token=token, body=body)
    data = payload.get("data", {})
    schedule_id = data.get("id")
    number = data.get("attributes", {}).get("number")
    print(f"Created report_schedule id={schedule_id} number={number} "
          f"({payload.get('summary')})")

    if sched.get("report_occurrence_id") not in ("5", 5, None):
        print("Occurrence is recurring -> Anchanto will keep emailing this on schedule. Done.")
        return

    if args.no_download:
        print("Report is generating; it will be emailed to the mailing list. (--no-download)")
        return

    report_url, _ = poll_until_ready(token, schedule_id,
                                     poll_every=args.poll_every, max_wait=args.max_wait)
    download(report_url, args.out_dir)


MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _last_day(year, month):
    if month == 12:
        return 31
    return (dt.date(year, month + 1, 1) - dt.timedelta(days=1)).day


def month_segments(year, month):
    """Return the 3 ten-day segments used for the 'Anchanto YY M MMM (NN)' naming.

      (01) day 1-10   (02) day 11-20   (03) day 21-end of month
    """
    last = _last_day(year, month)
    return [
        ("01", dt.date(year, month, 1), dt.date(year, month, 10)),
        ("02", dt.date(year, month, 11), dt.date(year, month, 20)),
        ("03", dt.date(year, month, 21), dt.date(year, month, last)),
    ]


def segment(year, month, nn):
    """(year, month, '01'|'02'|'03') -> (year, month, nn, start_date, end_date)."""
    spans = {"01": (1, 10), "02": (11, 20), "03": (21, _last_day(year, month))}
    s, e = spans[nn]
    return (year, month, nn, dt.date(year, month, s), dt.date(year, month, e))


def segment_containing(d):
    """The 10-day segment that date d falls in."""
    nn = "01" if d.day <= 10 else "02" if d.day <= 20 else "03"
    return segment(d.year, d.month, nn)


def previous_segment(year, month, nn):
    """The segment immediately before (year, month, nn), crossing month boundaries."""
    if nn == "03":
        return segment(year, month, "02")
    if nn == "02":
        return segment(year, month, "01")
    prev_month_last = dt.date(year, month, 1) - dt.timedelta(days=1)
    return segment(prev_month_last.year, prev_month_last.month, "03")


def rolling_segments(today, count=3):
    """`count` consecutive 10-day segments ending with the one that contains `today`,
    returned oldest-first."""
    segs = [segment_containing(today)]
    while len(segs) < count:
        y, m, nn, _, _ = segs[0]
        segs.insert(0, previous_segment(y, m, nn))
    return segs


def parse_month(spec):
    """'2026-08' -> (2026, 8). 'last'/'prev' -> the month before the current one."""
    if spec.lower() in ("last", "prev", "previous"):
        first_of_this = dt.date.today().replace(day=1)
        prev = first_of_this - dt.timedelta(days=1)
        return prev.year, prev.month
    if spec.lower() in ("this", "current"):
        t = dt.date.today()
        return t.year, t.month
    y, m = spec.split("-")
    return int(y), int(m)


def _state_path(out_dir):
    # Always local (see LOCAL_STATE_DIR note above) -- keyed by out_dir so different
    # --out-dir targets don't clobber each other's tracked schedule ids.
    os.makedirs(LOCAL_STATE_DIR, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]+", "_", os.path.abspath(out_dir)).strip("_")
    return os.path.join(LOCAL_STATE_DIR, f"last_run__{key}.json")


def load_prev_ids(out_dir):
    try:
        with open(_state_path(out_dir)) as fh:
            return [int(x) for x in json.load(fh).get("schedule_ids", [])]
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def save_ids(out_dir, ids):
    try:
        with open(_state_path(out_dir), "w") as fh:
            json.dump({"schedule_ids": ids, "at": dt.datetime.now().isoformat()}, fh)
    except OSError as e:
        print(f"  (warning: could not write state file: {e})")


def delete_schedules(token, ids, label="cleanup"):
    """Delete specific report schedules by id (mirrors the HAR bulk_delete call)."""
    ids = sorted({int(i) for i in ids})
    if not ids:
        print(f"  {label}: nothing to remove")
        return
    _, payload = _request("DELETE", f"{API_BASE}/api/v1/report_schedules/bulk_delete",
                          token=token, body={"ids": ids, "filter": {}})
    print(f"  {label}: removed {ids} -> {payload.get('summary')}")


def list_schedule_ids(token, type_id=None):
    """Every report-schedule id currently on the Reports list (all pages).
    This is what the UI's select-all + bulk-delete operates on. Optionally
    restrict to one report type."""
    ids = []
    page = 1
    while True:
        url = (f"{API_BASE}/api/v1/report_schedules?page[number]={page}"
               "&page[size]=100&include=report_type")
        _, payload = _request("GET", url, token=token)
        rows = payload.get("data", [])
        for r in rows:
            if type_id is None:
                ids.append(int(r["id"]))
            else:
                tid = (((r.get("relationships") or {}).get("report_type") or {})
                       .get("data") or {}).get("id")
                if str(tid) == str(type_id):
                    ids.append(int(r["id"]))
        nxt = (payload.get("meta") or {}).get("next_page")
        if not rows or not nxt:
            break
        page = nxt
    return ids


def _plan_name(seg):
    year, month, nn, _, _ = seg
    return f"Anchanto {year % 100:02d} {month} {MONTH_ABBR[month]} ({nn}).csv"


def _gcs_name(seg):
    # Matches the bucket's existing convention (Anchanto YY M MMM - Order Report...)
    # but with a plain "-N" suffix (no space) so it's never mistaken for one of the
    # pre-existing full-month files -- N is which third of that month this is (1/2/3).
    year, month, nn, _, _ = seg
    return f"Anchanto {year % 100:02d} {month} {MONTH_ABBR[month]} - Order Report-{int(nn)}.csv"


def poll_and_download(token, pending, out_dir, poll_every=10, max_wait=3600, gcs=None):
    """Fire-and-collect. `pending` maps schedule_id -> (target filename, gcs blob name).
    One list call per cycle checks every job; each is downloaded the moment its
    pre-signed URL appears, rather than waiting for the ones before it.
    `gcs`, if given, is {"bucket":..., "prefix":..., "key": path} -- each file is
    also uploaded to GCS (from the guaranteed-good local copy) right after download."""
    remaining = {int(k): v for k, v in pending.items()}
    saved, failed = [], []
    waited = 0
    url = (f"{API_BASE}/api/v1/report_schedules?page[number]=1&page[size]=50"
           "&include=latest_report")
    while remaining:
        _, payload = _request("GET", url, token=token)
        rows = {int(r["id"]): (r.get("attributes") or {}) for r in payload.get("data", [])}
        for sid in list(remaining):
            attrs = rows.get(sid)
            if attrs is None:
                continue
            report_url = attrs.get("report_url") or ""
            state = str(attrs.get("state", "")).lower()
            status = str(attrs.get("status", "")).lower()
            if report_url:
                name, gcs_name = remaining.pop(sid)
                print(f"  [{waited:>4}s] {name}  ready -> downloading")
                try:
                    saved.append(download(report_url, out_dir, filename=name))
                except ApiError as e:
                    print(f"  [{waited:>4}s] {name}  DOWNLOAD FAILED: {e}")
                    failed.append(name)
                    continue
                if gcs:
                    local_copy = os.path.join(LOCAL_EXPORT_DIR, name)
                    upload_to_gcs(local_copy, gcs["bucket"], gcs["prefix"], gcs["key"],
                                 blob_name=gcs_name)
            elif state in {"failed", "error"} or status in {"failed", "error"}:
                name, _ = remaining.pop(sid)
                print(f"  [{waited:>4}s] {name}  FAILED: {json.dumps(attrs)[:300]}")
                failed.append(name)
        if not remaining:
            break
        if waited >= max_wait:
            print(f"  [{waited:>4}s] timed out; still generating: "
                  f"{[n for n, _ in remaining.values()]} (they will still be emailed)")
            break
        prog = "  ".join(
            f"{n.split('(')[-1].rstrip(').csv')}="
            f"{(rows.get(s, {}).get('progress_status') or {}).get('percentage', '?')}%"
            for s, (n, _) in remaining.items())
        print(f"  [{waited:>4}s] pending: {prog}")
        time.sleep(poll_every)
        waited += poll_every
    return saved, failed


def run_plan(args, profile, plan, title):
    """plan = list of (year, month, nn, start_date, end_date), oldest first."""
    print(f"{title}  ->  {args.out_dir}")
    if getattr(args, "replace", False):
        print(f"  will first bulk-delete existing report schedules "
              f"(scope={getattr(args, 'delete_scope', 'type')}), then create these:")
    for seg in plan:
        _, _, nn, start, end = seg
        print(f"  {start.isoformat()} .. {end.isoformat()}   -> {_plan_name(seg)}")
    if args.dry_run:
        print("\n--dry-run: nothing sent.")
        return

    token = login()

    scope = getattr(args, "delete_scope", "type")
    if getattr(args, "replace", False):
        print(f"\n=== Clearing existing report schedules (scope={scope}) ===")
        type_id = profile.get("report_schedule", {}).get("report_type_id")
        existing = list_schedule_ids(token, type_id=None if scope == "all" else type_id)
        # also make sure the ids from our own previous run are included
        existing = sorted(set(existing) | set(load_prev_ids(args.out_dir)))
        delete_schedules(token, existing, label="cleared")

    # --- Phase 1: fire off every report job without waiting ---
    print("\n=== Creating report jobs ===")
    pending = {}          # schedule_id -> (target filename, gcs blob name)
    for seg in plan:
        _, _, nn, start, end = seg
        target_name = _plan_name(seg)
        gcs_name = _gcs_name(seg)
        body = build_schedule_body(profile, start, end)
        if args.mailing_list:
            body["report_schedule"]["mailing_list"] = args.mailing_list
        if getattr(args, "format", None):
            body["report_schedule"]["report_format"] = args.format
        body["report_schedule"]["report_occurrence_id"] = "5"  # adhoc

        try:
            _, payload = _request("POST", f"{API_BASE}/api/v1/report_schedules",
                                  token=token, body=body)
        except ApiError as e:
            print(f"  {target_name}  <- SKIPPED: {str(e).splitlines()[-1][:200]}")
            continue
        data = payload.get("data", {})
        schedule_id = data.get("id")
        print(f"  {target_name}  <- schedule id={schedule_id} "
              f"number={data.get('attributes', {}).get('number')}  "
              f"({start.isoformat()}..{end.isoformat()})")
        if schedule_id:
            pending[int(schedule_id)] = (target_name, gcs_name)

    # persist ids straight away so the next run's cleanup catches them
    # even if the download phase is interrupted
    save_ids(args.out_dir, list(pending))

    gcs = None
    if getattr(args, "gcs_bucket", None):
        gcs = {"bucket": args.gcs_bucket, "prefix": args.gcs_prefix, "key": args.gcs_key}
        print(f"  will also upload each file to gs://{gcs['bucket']}/{gcs['prefix']}/")

    # --- Phase 2: download each as soon as it is ready ---
    print("\n=== Waiting for reports, downloading as they finish ===")
    saved, failed = poll_and_download(token, pending, args.out_dir,
                                      poll_every=args.poll_every, max_wait=args.max_wait,
                                      gcs=gcs)

    print(f"\nDownloaded {len(saved)}/{len(pending)}:")
    for r in saved:
        print(f"  {r}")
    if failed:
        print(f"FAILED ({len(failed)}): {failed}")
        sys.exit(2)


def cmd_month_batch(args):
    with open(args.profile) as fh:
        profile = json.load(fh)
    year, month = parse_month(args.month)
    plan = [segment(year, month, nn) for nn, _, _ in month_segments(year, month)]
    if args.only:
        plan = [s for s in plan if s[2] in set(args.only)]
    run_plan(args, profile, plan, f"Month batch for {MONTH_ABBR[month]} {year}")


def cmd_rolling(args):
    with open(args.profile) as fh:
        profile = json.load(fh)
    plan = rolling_segments(dt.date.today(), count=args.count)
    run_plan(args, profile, plan,
             f"Rolling {args.count} ten-day segments ending {dt.date.today().isoformat()}")


_LOCAL_NAME_RE = re.compile(r"^Anchanto (\d{2}) (\d{1,2}) \w+ \((\d{2})\)\.csv$")


def _local_name_to_gcs_name(fname):
    """'Anchanto 26 8 Aug (03).csv' -> 'Anchanto 26 8 Aug - Order Report-3.csv'.
    Falls back to the original name if it doesn't match that pattern."""
    m = _LOCAL_NAME_RE.match(fname)
    if not m:
        return fname
    yy, month, nn = m.groups()
    return f"Anchanto {yy} {month} {MONTH_ABBR[int(month)]} - Order Report-{int(nn)}.csv"


def _add_gcs_args(p):
    p.add_argument("--gcs-bucket", help="also upload each file to this GCS bucket")
    p.add_argument("--gcs-prefix", default="sales_parquet/raw/online/anchanto/scrwms",
                   help="object prefix within the bucket (default sales_parquet/raw/online/anchanto/scrwms)")
    p.add_argument("--gcs-key", help="path to a service-account JSON key (required with --gcs-bucket)")


def cmd_gcs_upload(args):
    """Upload files already sitting in LOCAL_EXPORT_DIR to GCS. No SCRWMS login --
    just the GCS API, so it's cheap to retry after a fix without regenerating reports."""
    os.makedirs(LOCAL_EXPORT_DIR, exist_ok=True)
    names = args.files if args.files else sorted(os.listdir(LOCAL_EXPORT_DIR))
    names = [n for n in names if n.lower().endswith((".csv", ".xls", ".xlsx"))]
    if not names:
        print(f"No exportable files in {LOCAL_EXPORT_DIR}")
        return
    print(f"Uploading {len(names)} file(s) from {LOCAL_EXPORT_DIR} "
          f"-> gs://{args.gcs_bucket}/{args.gcs_prefix}/")
    ok = 0
    for n in names:
        local_path = os.path.join(LOCAL_EXPORT_DIR, n)
        if not os.path.isfile(local_path):
            continue
        gcs_name = _local_name_to_gcs_name(n)
        print(f"\n=== {n}  ->  {gcs_name} ===")
        if upload_to_gcs(local_path, args.gcs_bucket, args.gcs_prefix, args.gcs_key,
                         blob_name=gcs_name):
            ok += 1
    print(f"\nUploaded {ok}/{len(names)}.")


def cmd_sync_exports(args):
    """Re-sync files already sitting in LOCAL_EXPORT_DIR into --out-dir. No login,
    no API calls -- pure filesystem, so it's cheap to retry after a sync-only fix."""
    os.makedirs(LOCAL_EXPORT_DIR, exist_ok=True)
    names = args.files if args.files else sorted(os.listdir(LOCAL_EXPORT_DIR))
    names = [n for n in names if n.lower().endswith((".csv", ".xls", ".xlsx"))]
    if not names:
        print(f"No exportable files in {LOCAL_EXPORT_DIR}")
        return
    print(f"Syncing {len(names)} file(s) from {LOCAL_EXPORT_DIR} -> {args.out_dir}")
    ok, failed = [], []
    for n in names:
        local_path = os.path.join(LOCAL_EXPORT_DIR, n)
        if not os.path.isfile(local_path):
            continue
        print(f"\n=== {n} ===")
        dest = sync_to(local_path, args.out_dir, n, attempts=args.attempts)
        (ok if os.path.abspath(dest) != os.path.abspath(local_path) else failed).append(n)
    print(f"\nSynced {len(ok)}/{len(names)}. Still local-only: {failed or 'none'}")


def cmd_list_types(args):
    token = login()
    _, payload = _request(
        "GET", f"{API_BASE}/api/v1/report_types?page[number]=1&sort=display_name&page[size]=200",
        token=token)
    for t in payload.get("data", []):
        a = t["attributes"]
        print(f"  {t['id']:>4}  {a['display_name']:<40} formats={a.get('supported_formats')} "
              f"date_range={a.get('date_range')}")


def cmd_list_fields(args):
    token = login()
    _, payload = _request("GET", f"{API_BASE}/api/v1/report_fields/{args.type_id}", token=token)
    for f in payload.get("data", []):
        a = f["attributes"]
        star = "*" if a.get("default") else " "
        print(f" {star} {f['id']:>5}  {a['display_name']:<32} ({a['name']})")
    print("\n('*' = selected by default in the UI)")


def cmd_list_occurrences(args):
    token = login()
    _, payload = _request("GET", f"{API_BASE}/api/v1/report_occurrences?page[number]=1", token=token)
    for o in payload.get("data", []):
        a = o["attributes"]
        print(f"  {o['id']:>3}  {a['display_name']} ({a['name']})")


def cmd_list_companies(args):
    token = login()
    ids = "[" + ",".join(str(x) for x in args.warehouse_ids) + "]"
    url = f"{API_BASE}/api/v1/warehouses/report_companies?warehouse_ids={urllib.parse.quote(ids)}"
    _, payload = _request("GET", url, token=token)
    for c in payload.get("data", []):
        a = c["attributes"]
        print(f"  {c['id']:>4}  {a.get('name'):<24} {a.get('business_name')}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="create a report (and download it)")
    g.add_argument("--profile", required=True, help="path to a report profile JSON")
    g.add_argument("--from", dest="from_date", help="start date YYYY-MM-DD")
    g.add_argument("--to", dest="to_date", help="end date YYYY-MM-DD")
    g.add_argument("--last-days", type=int, help="date range = the last N days (incl. today)")
    g.add_argument("--yesterday", action="store_true")
    g.add_argument("--today", action="store_true")
    g.add_argument("--month-to-date", action="store_true")
    g.add_argument("--format", choices=["csv", "xls"], help="override profile report_format")
    g.add_argument("--mailing-list", nargs="+", metavar="EMAIL",
                   help="override profile mailing_list")
    g.add_argument("--out-dir", default="./reports", help="download directory (default ./reports)")
    g.add_argument("--no-download", action="store_true",
                   help="just trigger it (still emailed); don't poll/download")
    g.add_argument("--poll-every", type=int, default=10, help="poll interval seconds (default 10)")
    g.add_argument("--max-wait", type=int, default=1800, help="max seconds to wait (default 1800)")
    g.add_argument("--dry-run", action="store_true", help="print the request body and exit")
    g.set_defaults(func=cmd_generate)

    mb = sub.add_parser("month-batch",
                        help="generate the 3 ten-day segments of a month, named 'Anchanto YY M MMM (NN)'")
    mb.add_argument("--profile", required=True)
    mb.add_argument("--month", required=True, metavar="YYYY-MM",
                    help="e.g. 2026-08, or 'last' for the previous calendar month")
    mb.add_argument("--out-dir", required=True, help="folder to write the CSVs into")
    mb.add_argument("--only", nargs="+", choices=["01", "02", "03"],
                    help="only these segments (default: all three)")
    mb.add_argument("--replace", action="store_true",
                    help="first delete existing report schedules (see --delete-scope), like the "
                         "manual select-all + bulk-delete on the Reports page, then create fresh ones")
    mb.add_argument("--delete-scope", choices=["type", "all"], default="type",
                    help="with --replace: 'type' (default) clears existing schedules of this "
                         "report type only; 'all' clears every schedule on the Reports list")
    mb.add_argument("--format", choices=["csv", "xls"])
    mb.add_argument("--mailing-list", nargs="+", metavar="EMAIL")
    mb.add_argument("--poll-every", type=int, default=10)
    mb.add_argument("--max-wait", type=int, default=3600)
    mb.add_argument("--dry-run", action="store_true")
    _add_gcs_args(mb)
    mb.set_defaults(func=cmd_month_batch)

    rb = sub.add_parser("rolling",
                        help="generate the current 10-day segment plus the previous ones "
                             "(default 3 total), each named 'Anchanto YY M MMM (NN)' for its own month")
    rb.add_argument("--profile", required=True)
    rb.add_argument("--out-dir", required=True, help="folder to write the CSVs into")
    rb.add_argument("--count", type=int, default=3, help="how many segments (default 3)")
    rb.add_argument("--replace", action="store_true",
                    help="first delete existing report schedules (see --delete-scope), like the "
                         "manual select-all + bulk-delete on the Reports page, then create fresh ones")
    rb.add_argument("--delete-scope", choices=["type", "all"], default="type",
                    help="with --replace: 'type' (default) clears existing schedules of this "
                         "report type only; 'all' clears every schedule on the Reports list")
    rb.add_argument("--format", choices=["csv", "xls"])
    rb.add_argument("--mailing-list", nargs="+", metavar="EMAIL")
    rb.add_argument("--poll-every", type=int, default=10)
    rb.add_argument("--max-wait", type=int, default=3600)
    rb.add_argument("--dry-run", action="store_true")
    _add_gcs_args(rb)
    rb.set_defaults(func=cmd_rolling)

    se = sub.add_parser("sync-exports",
                        help="re-copy files already in the local safety-net folder into --out-dir "
                             "(no login, no API calls -- just filesystem)")
    se.add_argument("--out-dir", required=True)
    se.add_argument("--files", nargs="+", help="specific filenames (default: everything local)")
    se.add_argument("--attempts", type=int, default=5)

    gu = sub.add_parser("gcs-upload",
                        help="upload files already in the local safety-net folder to GCS "
                             "(no SCRWMS login -- just the GCS API)")
    gu.add_argument("--gcs-bucket", required=True)
    gu.add_argument("--gcs-prefix", required=True)
    gu.add_argument("--gcs-key", required=True, help="path to a service-account JSON key")
    gu.add_argument("--files", nargs="+", help="specific filenames (default: everything local)")
    gu.set_defaults(func=cmd_gcs_upload)
    se.set_defaults(func=cmd_sync_exports)

    lt = sub.add_parser("list-types", help="list report types"); lt.set_defaults(func=cmd_list_types)
    lf = sub.add_parser("list-fields", help="list columns for a report type")
    lf.add_argument("type_id"); lf.set_defaults(func=cmd_list_fields)
    lo = sub.add_parser("list-occurrences", help="list occurrence ids")
    lo.set_defaults(func=cmd_list_occurrences)
    lc = sub.add_parser("list-companies", help="list company ids for warehouse(s)")
    lc.add_argument("--warehouse-ids", type=int, nargs="+", default=[1])
    lc.set_defaults(func=cmd_list_companies)

    args = p.parse_args()
    try:
        args.func(args)
    except ApiError as e:
        sys.exit(f"\nAPI ERROR:\n{e}")


if __name__ == "__main__":
    main()
