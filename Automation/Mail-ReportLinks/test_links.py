r"""
Regression tests for link extraction. Run with:

    .\.venv\Scripts\python.exe test_links.py

The noise filter is the risky part of this script: too loose and it downloads
banner images, too tight and it silently discards the actual report - which is
much worse, because nothing looks broken. These pin both edges.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_links as r  # noqa: E402

NOISE_CASES = [
    # (url, expected_is_noise)
    ("https://drive.google.com/file/d/1AbC/view", False),
    ("https://storage.googleapis.com/x/report.xlsx?a=1&b=2", False),
    ("https://b2b.alfamidi.co.id/export/rpt_2609.xlsx", False),
    ("https://b2b.alfamart.co.id/marketshare/download?id=77", False),
    ("https://b2b.alfamart.co.id/unsubscribe?id=99201", True),
    ("https://b2b.alfamart.co.id/img/banner-footer.png", True),
    ("https://www.facebook.com/alfamart", True),
    ("https://fonts.googleapis.com/css?family=Roboto", True),
    ("https://twitter.com/alfamart", True),
]

SAMPLE_EMAIL = """
<html><body>
<p>Laporan siap.</p>
<a href="https://storage.googleapis.com/rpt/ms_202609.xlsx?X-Goog-Expires=86400&amp;Signature=abc">Download</a>
<a href="https://www.instagram.com/alfamart">Instagram</a>
<a href="https://b2b.alfamart.co.id/unsubscribe?id=1">Berhenti</a>
</body></html>
"""


def check_routing() -> list[str]:
    """Each report type must land in the folder it already lived in.

    These folders predate the automation: `Daily Sell Out` holds ~230 All_Store
    files that 3_upload_and_distribute.py globs and pushes to GCS, and `Stock`
    holds the by_branch Stok pair. Mis-filing here either hides files from that
    pipeline or feeds it the wrong shape.

    Rule order is load-bearing: `by_branch_Stok` must be tested before the bare
    `by_branch`, or Stok reports land in Sell Out.
    """
    import json

    source = json.loads((Path(__file__).resolve().parent / "sources.json")
                        .read_text(encoding="utf-8-sig"))[0]
    root = Path(r"X:\root")
    base = "https://example-b2b-bucket.storage.googleapis.com/excel/report/"

    cases = [
        ("detail_performance_Selling_Out_Qty_BODY_LOTION_All_Item_NASIONAL_"
         "All_Store_S-0000_2026-09-11_sd_2026-09-18.csv",
         r"Alfamidi\Daily Sell Out"),
        ("detail_performance_by_branch_Stok_Value_All_Category_All_Item_"
         "NASIONAL_S-0000_2026-09-01_sd_2026-09-18.csv",
         r"Alfamidi\Stock"),
        ("detail_performance_by_branch_Selling_Out_Qty_All_Category_All_Item_"
         "NASIONAL_S-0000_2026-09-01_sd_2026-09-18.csv",
         r"Alfamidi\Sell Out"),
    ]

    problems = []
    for name, want in cases:
        got = r.resolve_dest(source, base + name + "?X-Goog-Expires=86400", root)
        if got != root / want:
            problems.append(f"{name[:52]}…\n     routed to {got}\n     want      {root / want}")

    # Nothing recognisable must not be silently filed with real reports.
    got = r.resolve_dest(source, base + "something_else.csv", root)
    if got != root / r"Alfamidi\Unsorted":
        problems.append(f"unrecognised report should go to Unsorted, got {got}")

    return problems


def check_safelinks() -> list[str]:
    """Defender Safe Links must be unwrapped back to the real signed URL.

    Taken from a real Alfamidi report email (2026-09-18). Every inbound link is
    rewritten to safelinks.protection.outlook.com with the true URL buried,
    double-encoded, in `url=`. Fetching the wrapper instead of the target is the
    difference between a CSV and a tenant redirect page.

    The decoding depth matters: the inner URL is double-encoded, so exactly one
    decode must happen. Decode twice and `%2F`/`%40` inside the GCS credential
    turn into literal `/` and `@`, invalidating the signature.
    """
    real = ("https://example-b2b-bucket.storage.googleapis.com/excel/report/"
            "detail_performance_by_branch_Selling_Out_Qty_All_Category_All_Item_"
            "NASIONAL_S-0000_2026-09-01_sd_2026-09-18.csv"
            "?X-Goog-Algorithm=GOOG4-RSA-SHA256"
            "&X-Goog-Credential=sa-example%40example-project.iam.gserviceaccount.com"
            "%2F20260918%2Fauto%2Fstorage%2Fgoog4_request"
            "&X-Goog-Date=20260918T043942Z&X-Goog-Expires=86400"
            "&X-Goog-SignedHeaders=host&x-goog-signature=460c210639c1993c")

    wrapped = ("https://idn01.safelinks.protection.outlook.com/?url="
               "https%3A%2F%2Fexample-b2b-bucket.storage.googleapis.com%2Fexcel"
               "%2Freport%2Fdetail_performance_by_branch_Selling_Out_Qty_All_"
               "Category_All_Item_NASIONAL_S-0000_2026-09-01_sd_2026-09-18.csv"
               "%3FX-Goog-Algorithm%3DGOOG4-RSA-SHA256"
               "%26X-Goog-Credential%3Dsa-example%2540example-project.iam."
               "gserviceaccount.com%252F20260918%252Fauto%252Fstorage"
               "%252Fgoog4_request%26X-Goog-Date%3D20260918T043942Z"
               "%26X-Goog-Expires%3D86400%26X-Goog-SignedHeaders%3Dhost"
               "%26x-goog-signature%3D460c210639c1993c"
               "&data=05%7C02%7Cbrian.rinaldy%40lmbg.co.id&reserved=0")

    problems = []
    got = r.unwrap_safelinks(wrapped)
    if got != real:
        problems.append("Safe Links unwrap produced the wrong URL\n"
                        f"     got  {got}\n     want {real}")

    # A plain URL must pass through untouched.
    if r.unwrap_safelinks(real) != real:
        problems.append("a non-wrapped URL must be returned unchanged")

    # And the wrapper must be unwrapped when it appears as an href, with
    # `originalsrc` preferred when Outlook supplies it.
    html_href = f'<html><body><a href="{wrapped.replace("&", "&amp;")}">Download File</a></body></html>'
    links = r.extract_links(html_href)
    if links != [real]:
        problems.append(f"href unwrapping failed, got {links}")

    html_orig = (f'<html><body><a href="{wrapped.replace("&", "&amp;")}" '
                 f'originalsrc="{real.replace("&", "&amp;")}">Download</a></body></html>')
    links = r.extract_links(html_orig)
    if links != [real]:
        problems.append(f"originalsrc should be preferred, got {links}")

    return problems


def check_eml_roundtrip() -> list[str]:
    """A saved .eml must survive quoted-printable encoding intact.

    This is the realistic failure: Outlook saves messages quoted-printable, so a
    long signed URL gets chopped across lines with '=' soft breaks and every '='
    in the query string becomes '=3D'. Read as plain text the link comes back
    truncated - plausible-looking in the log, 404 on fetch.
    """
    import tempfile
    from email.message import EmailMessage

    url = ("https://storage.googleapis.com/alfamidi-reports/exports/"
           "detail_performance_Selling_Out_Qty_BODY_LOTION_All_Item_NASIONAL_"
           "All_Store_20260901_20260918_645794.xlsx"
           "?X-Goog-Algorithm=GOOG4-RSA-SHA256"
           "&X-Goog-Expires=86400"
           "&X-Goog-Signature=a1b2c3d4e5f60718293a4b5c6d7e8f90"
           "112233445566778899aabbccddeeff00")

    msg = EmailMessage()
    msg["From"] = "noreply@alfamidiku.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Download Report Performance Sales"
    msg.set_content("plain text fallback")
    msg.add_alternative(
        f'<html><body><p>Laporan siap.</p>'
        f'<a href="{url}">Download</a>'
        f'<a href="https://www.facebook.com/alfamidi">Facebook</a>'
        f'</body></html>',
        subtype="html", cte="quoted-printable")

    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.eml"
        path.write_bytes(msg.as_bytes())

        raw = path.read_text(encoding="utf-8", errors="replace")
        if "=\n" not in raw and "=\r\n" not in raw:
            problems.append("test setup: the .eml is not quoted-printable, "
                            "so this case proves nothing")

        body, sender = r.read_body(path)
        if "alfamidiku.com" not in sender:
            problems.append(f"sender not parsed from .eml: {sender!r}")

        links = r.extract_links(body)
        if url not in links:
            got = links[0] if links else "(none)"
            problems.append("signed URL did not survive the .eml round-trip\n"
                            f"     got  {got}\n     want {url}")
        if len(links) != 1:
            problems.append(f"expected 1 link from the .eml, got {len(links)}")

    return problems


def main() -> int:
    fails = 0

    for url, want in NOISE_CASES:
        got = r.is_noise(url)
        if got != want:
            fails += 1
            print(f"FAIL is_noise({url}) -> {got}, wanted {want}")

    links = r.extract_links(SAMPLE_EMAIL)
    if len(links) != 1:
        fails += 1
        print(f"FAIL expected exactly 1 link from the sample, got {links}")
    elif "&amp;" in links[0]:
        # Power Automate writes the body verbatim; a URL still carrying &amp;
        # fetches as a signature error rather than a file.
        fails += 1
        print(f"FAIL HTML entity was not unescaped: {links[0]}")
    elif "Signature=abc" not in links[0]:
        fails += 1
        print(f"FAIL query string mangled: {links[0]}")

    for problem in check_eml_roundtrip():
        fails += 1
        print(f"FAIL {problem}")

    for problem in check_safelinks():
        fails += 1
        print(f"FAIL {problem}")

    for problem in check_routing():
        fails += 1
        print(f"FAIL {problem}")

    total = len(NOISE_CASES) + 4
    print(f"{total - fails}/{total} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
