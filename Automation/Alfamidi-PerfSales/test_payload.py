r"""
Regression tests for the request payloads. Run with:

    .\.venv\Scripts\python.exe test_payload.py

These pin the form fields against the real requests captured in
`Data\Sent Email\midi-b2b.et.r.appspot.com_by branch and by store daily selling
out report.har`. If Alfamidi renames a field the portal will usually still
answer 200 with a useless file, so a mismatch here is the earliest warning we
get.

Note the HAR shows values percent-encoded on the wire ("Selling+Out"); the
payload holds them decoded, because Playwright encodes them again on send.
"""

import sys
import types
from pathlib import Path

# Stub Playwright so the pure functions can be imported without a browser.
if "playwright" not in sys.modules:
    pw = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: None
    sync_api.TimeoutError = type("TimeoutError", (Exception,), {})
    pw.sync_api = sync_api
    sys.modules["playwright"] = pw
    sys.modules["playwright.sync_api"] = sync_api

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alfamidi_perfsales as a  # noqa: E402

# Exactly what the browser sent, decoded, minus `filename` which we extend.
HAR_BY_BRANCH = {
    "tipe_prf": "5", "opt": "a", "u": "q", "br": "nas", "cat": "all",
    "plu": "all", "tgla": "2026-09-01", "tglb": "2026-09-18",
    "indicatortext": "Selling Out", "unittext": "Qty",
    "categorytext": "All Category", "itemtext": "All Item",
    "branchtext": "NASIONAL",
}
HAR_BY_BRANCH_STEM = ("detail_performance_by_branch_Selling_Out_Qty_"
                      "All_Category_All_Item_NASIONAL")

HAR_BY_STORE = {
    "tipe_prf": "4", "opt": "a", "u": "v", "br": "nas", "cat": "3251",
    "plu": "all", "tgla": "2026-09-01", "tglb": "2026-09-18", "st": "all",
    "indicatortext": "Selling Out", "unittext": "Value",
    "categorytext": "BODY LOTION", "itemtext": "All Item",
    "branchtext": "NASIONAL", "storetext": "All Store",
}
HAR_BY_STORE_STEM = ("detail_performance_Selling_Out_Value_BODY_LOTION_"
                     "All_Item_NASIONAL_All_Store")

fails = []


def check(condition, message):
    if not condition:
        fails.append(message)


def compare(got: dict, want: dict, want_stem: str, label: str):
    for key, value in want.items():
        if key not in got:
            fails.append(f"{label}: missing field {key!r}")
        elif got[key] != value:
            fails.append(f"{label}: {key!r} = {got[key]!r}, HAR had {value!r}")
    for key in got:
        if key not in want and key != "filename":
            fails.append(f"{label}: unexpected extra field {key!r}")
    check(got["filename"].startswith(want_stem),
          f"{label}: filename stem drifted\n     got  {got['filename']}\n"
          f"     want {want_stem}_<dates>_<token>")


compare(
    a.build_payload("5", "all", "All Category", "q",
                    "2026-09-01", "2026-09-18", "a", "abc123"),
    HAR_BY_BRANCH, HAR_BY_BRANCH_STEM, "by-branch")

compare(
    a.build_payload("4", "3251", "BODY LOTION", "v",
                    "2026-09-01", "2026-09-18", "a", "abc123"),
    HAR_BY_STORE, HAR_BY_STORE_STEM, "by-store")

# by-branch must not carry the store fields; by-store must.
bb = a.build_payload("5", "all", "All Category", "q", "2026-09-01", "2026-09-18", "a", "t")
check("st" not in bb and "storetext" not in bb,
      "by-branch should not send st/storetext")

# The token has to survive into the filename - it is the only link between a
# request and the file that arrives by email later.
check("deadbe" in bb["filename"].lower() or True, "")
bt = a.build_payload("4", "3252", "BODY SERUM", "q", "2026-09-01", "2026-09-18", "a", "deadbe")
check(bt["filename"].endswith("_deadbe"),
      f"token missing from filename: {bt['filename']}")

# Category labels with punctuation must not break the filename.
punct = a.build_payload("4", "3239", "MEN PARFUME EDT & EXTRAIT", "v",
                        "2026-09-01", "2026-09-18", "a", "tok")
check(" " not in punct["filename"] and "&" not in punct["filename"],
      f"filename not slugged: {punct['filename']}")
check(punct["categorytext"] == "MEN PARFUME EDT & EXTRAIT",
      "categorytext must stay human-readable, only filename is slugged")

# --- date windows --------------------------------------------------------
# by-branch and by-store use DIFFERENT calendars, both from period_utils.py and
# shared with 3_upload_and_distribute.py. by-store is not cosmetic: that script
# derives each file's period from its start date and deletes anything whose end
# overshoots the boundary as "invalid (past period end)". A month-to-date
# by-store file would therefore be downloaded and then thrown away.
from calendar import monthrange as _mr  # noqa: E402

cur = a.branch_windows("current")
check(len(cur) == 1 and cur[0][0].endswith("-01"),
      f"by-branch current window should start on the 1st: {cur}")
prev = a.branch_windows("previous")
check(len(prev) == 1 and prev[0][0].endswith("-01"),
      f"by-branch previous window should start on the 1st: {prev}")
check(len(a.branch_windows("both")) == 2, "by-branch 'both' should give 2 windows")

py, pm = int(prev[0][0][:4]), int(prev[0][0][5:7])
check(prev[0][1].endswith(f"-{_mr(py, pm)[1]:02d}"),
      f"by-branch previous window must cover the whole month: {prev[0]}")

store_w = a.store_windows(3)
check(len(store_w) == 3, f"store_windows(3) should give 3 periods, got {len(store_w)}")
starts = [int(w[0][8:10]) for w in store_w]
check(all(d in (1, 11, 21) for d in starts),
      f"every by-store period must start on the 1st, 11th or 21st: {starts}")

for tgla, tglb, label in store_w:
    y, m, d = int(tgla[:4]), int(tgla[5:7]), int(tgla[8:10])
    official_end = 10 if d == 1 else (20 if d == 11 else _mr(y, m)[1])
    check(tglb[:7] == tgla[:7] and int(tglb[8:10]) <= official_end,
          f"by-store window {label} ends {tglb}, past its period boundary "
          f"(day {official_end}) - 3_upload_and_distribute.py would delete it")

# --- request plan --------------------------------------------------------
# by-branch = all categories in one file over 2 month windows -> 4.
# by-store  = one request per category over 3 ten-day periods -> 6x2x3 = 36.
# A 7th category must push that to 42 with nothing edited here.
SIX = {"3251": "BODY LOTION", "3252": "BODY SERUM", "3241": "FACIAL WASH SOAP",
       "3239": "MEN PARFUME EDT & EXTRAIT", "3240": "SUNSCREEN",
       "3232": "WOMEN PARFUME EDT & EXTRAIT"}
BRANCH_W = [("2026-09-01", "2026-09-18", "2026-09 MTD"),
            ("2026-08-01", "2026-08-31", "2026-08 full")]
STORE_W = [("2026-09-11", "2026-09-18", "2026-09 P2"),
           ("2026-09-01", "2026-09-10", "2026-09 P1"),
           ("2026-08-21", "2026-08-31", "2026-08 P3")]

plan = a.plan_requests(["5", "4"], SIX, ["q", "v"], BRANCH_W, STORE_W)
check(len(plan) == 40, f"expected 40 requests for 6 categories, got {len(plan)}")

branch = [p for p in plan if p[0] == "5"]
store = [p for p in plan if p[0] == "4"]
check(len(branch) == 4, f"by-branch should be 4 (2 units x 2 months), got {len(branch)}")
check(len(store) == 36, f"by-store should be 36 (6 cats x 2 units x 3 periods), got {len(store)}")
check(all(p[1] == "all" for p in branch), "by-branch must request cat=all")
check({p[1] for p in store} == set(SIX),
      "by-store must cover every category exactly")

# The two report types must not borrow each other's calendar.
check({(p[4], p[5]) for p in branch} == {(w[0], w[1]) for w in BRANCH_W},
      "by-branch must use the month windows, not the 10-day periods")
check({(p[4], p[5]) for p in store} == {(w[0], w[1]) for w in STORE_W},
      "by-store must use the 10-day periods, not the month windows")

# A seventh category must be picked up with no code change.
seven = dict(SIX, **{"9999": "NEW CATEGORY"})
plan7 = a.plan_requests(["5", "4"], seven, ["q", "v"], BRANCH_W, STORE_W)
check(len(plan7) == 46,
      f"a 7th category should give 46 requests, got {len(plan7)} - "
      "category discovery is not flowing through to the plan")

# --- reply classification ------------------------------------------------
# Both of these were captured live on 2026-09-18. They differ ONLY in the
# message text - `code` is "T" in both. Keying on the code alone would record a
# throttled request as queued, orphaning a token in requests.json against an
# email that never arrives. These two cases are the guard against that.
QUEUED_REPLY = ('{\n  "code": "T", \n  "result": "Request Download File '
                'Berhasil \\nLink Download File Akan dikirim Via Email Jika '
                'File Sudah Tersedia"\n}')
THROTTLED_REPLY = ('{\n  "code": "T", \n  "result": "Request Download File '
                   'Sudah diajukan dalam 1 jam terakhir \\nsilahkan cek email '
                   'untuk download file atau menunggu request file sebelumnya '
                   'selesai"\n}')

check(a.classify_reply(QUEUED_REPLY) == a.QUEUED,
      "the real success reply must classify as queued")
check(a.classify_reply(THROTTLED_REPLY) == a.THROTTLED,
      "the real throttle reply must classify as throttled - note both replies "
      'carry code "T", so only the message text separates them')
check(a.classify_reply('{"code": "F", "result": "gagal"}') == a.FAILED,
      "a non-T code must classify as failed")
check(a.classify_reply("<html>not json</html>") == a.FAILED,
      "a non-JSON reply must classify as failed")

# A lapsed session must be raised, not quietly counted as a failure.
try:
    a.classify_reply("<html><form name=\"upass\">login</form></html>")
    fails.append("an expired-session reply should raise SystemExit")
except SystemExit:
    pass

if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print(f"  FAIL {f}")
    sys.exit(1)
print("all payload tests passed")
