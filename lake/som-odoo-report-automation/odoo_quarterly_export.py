#!/usr/bin/env python3
"""Export the quarterly Odoo Sales Order list and Sales Analysis pivot as .xlsx.

Reproduces the two exports normally done by hand in the Odoo web client:
  Sales > Orders > Orders     -> "<seq>. <year> Q<n> Sales Order.xlsx"
  Sales > Reporting (pivot)   -> "<seq>. <year> Q<n> Sales Analysis.xlsx"

Both files are produced by Odoo's own export endpoints, so the Sales Analysis
layout keeps the 5-space-per-level indentation that odoo_report.py depends on.

Credentials are read from a .env file next to this script and are never stored
in the script itself.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Odoo stores datetimes in UTC; the filters in the web client are evaluated in
# the user's timezone, so quarter bounds are shifted by this offset.
TZ_OFFSET_HOURS = 7  # Asia/Jakarta

# Numbering continues the existing series: 13 = 2026 Q1, 14 = Q2, 15 = Q3.
SEQ_BASE_YEAR = 2026
SEQ_BASE_NUMBER = 13

ORDER_FIELDS = [
    {"name": "name", "label": "Order Reference", "store": True, "type": "char"},
    {"name": "create_date", "label": "Creation Date", "store": True, "type": "datetime"},
    {"name": "partner_id", "label": "Customer", "store": True, "type": "many2one"},
    {"name": "shipping_address", "label": "Delivery Address", "store": False, "type": "char"},
    {"name": "partner_shipping_id", "label": "Delivery Address", "store": True, "type": "many2one"},
    {"name": "amount_total", "label": "Total", "store": True, "type": "monetary"},
    {"name": "invoice_status", "label": "Invoice Status", "store": True, "type": "selection"},
    {"name": "effective_date", "label": "Effective Date", "store": True, "type": "datetime"},
    {"name": "payment_term_id", "label": "Payment Terms", "store": True, "type": "many2one"},
    {"name": "user_id", "label": "Salesperson", "store": True, "type": "many2one"},
    {"name": "state", "label": "Status", "store": True, "type": "selection"},
]

PIVOT_ROW_GROUPBY = [
    "date:day",
    "partner_analytic_level_1",
    "partner_analytic_level_2",
    "state_id",
    "city",
    "partner_id",
    "commercial_partner_id",
    "user_id",
    "name",
    "state",
    "product_tmpl_id",
]
PIVOT_COL_GROUPBY = "team_id"

MEASURES = [
    ("product_uom_qty", "Qty Ordered"),
    ("qty_to_deliver", "Qty To Deliver"),
    ("qty_delivered", "Qty Delivered"),
    ("qty_to_invoice", "Qty To Invoice"),
    ("qty_invoiced", "Qty Invoiced"),
]


def load_env(path=ENV_PATH):
    if not os.path.exists(path):
        sys.exit(
            f"Missing credentials file: {path}\n"
            "Copy .env.example to .env and fill in ODOO_USERNAME and ODOO_PASSWORD."
        )
    env = {}
    with open(path, "r", encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")

    missing = [k for k in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD") if not env.get(k)]
    if missing:
        sys.exit(f"{path} is missing values for: {', '.join(missing)}")
    return env


def quarter_bounds(year, quarter):
    """Return the (start, end) UTC datetime strings for a local-time quarter."""
    start_month = 3 * (quarter - 1) + 1
    local_start = datetime(year, start_month, 1)
    if quarter == 4:
        local_end = datetime(year + 1, 1, 1)
    else:
        local_end = datetime(year, start_month + 3, 1)

    utc_start = local_start - timedelta(hours=TZ_OFFSET_HOURS)
    utc_end = local_end - timedelta(hours=TZ_OFFSET_HOURS, seconds=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return utc_start.strftime(fmt), utc_end.strftime(fmt)


def sequence_number(year, quarter):
    return SEQ_BASE_NUMBER + (year - SEQ_BASE_YEAR) * 4 + (quarter - 1)


def current_quarter(today=None):
    today = today or datetime.now()
    return today.year, (today.month - 1) // 3 + 1


def previous_quarter(year, quarter):
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def parse_quarter(text):
    text = text.strip()
    if text.lower() == "current":
        return current_quarter()
    if text.lower() == "previous":
        return previous_quarter(*current_quarter())
    match = re.fullmatch(r"(\d{4})[-\s]?[Qq]([1-4])", text)
    if not match:
        sys.exit(f"Could not read quarter '{text}'. Use a form like 2026Q3, 'current' or 'previous'.")
    return int(match.group(1)), int(match.group(2))


class OdooClient:
    def __init__(self, url, db, username, password):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.uid = None
        self.context = {}
        self.csrf_token = None

    def login(self):
        response = self.session.post(
            f"{self.url}/web/session/authenticate",
            json={
                "jsonrpc": "2.0",
                "method": "call",
                "params": {"db": self.db, "login": self.username, "password": self.password},
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            message = payload["error"].get("data", {}).get("message") or payload["error"].get("message")
            sys.exit(f"Odoo login failed: {message}")

        result = payload.get("result") or {}
        self.uid = result.get("uid")
        if not self.uid:
            sys.exit("Odoo login failed: no user id returned (check database, username and password).")

        self.context = {
            "lang": result.get("user_context", {}).get("lang", "en_US"),
            "tz": result.get("user_context", {}).get("tz", "Asia/Jakarta"),
            "uid": self.uid,
            "allowed_company_ids": result.get("user_companies", {}).get("allowed_companies")
            and list(result["user_companies"]["allowed_companies"])
            or [1],
        }
        if not isinstance(self.context["allowed_company_ids"][0], int):
            self.context["allowed_company_ids"] = [
                int(cid) for cid in self.context["allowed_company_ids"]
            ]

        page = self.session.get(f"{self.url}/web", timeout=120)
        page.raise_for_status()
        token = re.search(r'csrf_token["\s:=]+"([^"]+)"', page.text)
        if not token:
            sys.exit("Could not find the CSRF token on /web; the Odoo version may have changed.")
        self.csrf_token = token.group(1)
        return self

    def call_kw(self, model, method, args=None, kwargs=None):
        response = self.session.post(
            f"{self.url}/web/dataset/call_kw/{model}/{method}",
            json={
                "jsonrpc": "2.0",
                "method": "call",
                "params": {
                    "model": model,
                    "method": method,
                    "args": args or [],
                    "kwargs": kwargs or {},
                },
            },
            timeout=600,
        )
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            message = payload["error"].get("data", {}).get("message") or payload["error"].get("message")
            raise RuntimeError(f"{model}.{method} failed: {message}")
        return payload["result"]

    def post_export(self, endpoint, data):
        response = self.session.post(
            f"{self.url}{endpoint}",
            files={
                "data": (None, json.dumps(data)),
                "token": (None, "dummy-because-api-expects-one"),
                "csrf_token": (None, self.csrf_token),
            },
            timeout=900,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "spreadsheetml" not in content_type:
            raise RuntimeError(
                f"{endpoint} returned {content_type} instead of an xlsx file "
                f"(first 300 bytes: {response.content[:300]!r})"
            )
        return response.content


def write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    print(f"  saved {path} ({len(content):,} bytes)")


def export_sales_orders(client, start, end, out_path):
    print("Exporting Sales > Orders ...")
    data = {
        "import_compat": False,
        "context": client.context,
        "domain": [
            "&",
            ["state", "not in", ["draft", "sent", "cancel"]],
            "&",
            ["date_order", ">=", start],
            ["date_order", "<=", end],
        ],
        "fields": ORDER_FIELDS,
        "groupby": [],
        "ids": False,
        "model": "sale.order",
    }
    write_file(out_path, client.post_export("/web/export/xlsx", data))


def group_label(value, selection_map):
    """Render a read_group key the way the pivot view labels it."""
    if value is False or value is None:
        return "None"
    if isinstance(value, (list, tuple)):
        return value[1] if len(value) > 1 else str(value[0])
    if selection_map and value in selection_map:
        return selection_map[value]
    return str(value)


def export_sales_analysis(client, start, end, out_path):
    print("Exporting Sales > Reporting (pivot) ...")
    domain = [
        "&",
        ["state", "!=", "cancel"],
        "&",
        "&",
        ["state", "not in", ["draft", "cancel", "sent"]],
        "&",
        ["partner_analytic_level_1", "ilike", "Offline"],
        ["partner_id", "not ilike", "opto"],
        "&",
        ["date", ">=", start],
        ["date", "<=", end],
    ]

    base_fields = [f.split(":")[0] for f in PIVOT_ROW_GROUPBY] + [PIVOT_COL_GROUPBY]
    descriptions = client.call_kw(
        "sale.report", "fields_get", [base_fields, ["type", "selection"]]
    )
    selection_maps = {
        field: dict(info["selection"])
        for field, info in descriptions.items()
        if info.get("type") == "selection" and info.get("selection")
    }

    groupby = [PIVOT_COL_GROUPBY] + PIVOT_ROW_GROUPBY
    print(f"  reading {len(groupby)} grouping levels ...")
    leaves = client.call_kw(
        "sale.report",
        "read_group",
        [],
        {
            "domain": domain,
            "fields": [f"{name}:sum" for name, _ in MEASURES],
            "groupby": groupby,
            "lazy": False,
            "context": dict(client.context, group_by_no_leaf=1, fill_temporal=True),
        },
    )
    print(f"  {len(leaves):,} leaf groups returned")

    # Column groups (sales teams), in order of first appearance.
    teams = []
    for leaf in leaves:
        label = group_label(leaf.get(PIVOT_COL_GROUPBY), selection_maps.get(PIVOT_COL_GROUPBY))
        if label not in teams:
            teams.append(label)

    n_measures = len(MEASURES)
    n_cols = len(teams) * n_measures

    # Accumulate every prefix of the row hierarchy so each level carries its subtotal.
    totals = {}
    order = []

    def accumulate(key, team, values):
        if key not in totals:
            totals[key] = {t: [0] * n_measures for t in teams}
            order.append(key)
        bucket = totals[key][team]
        for i, value in enumerate(values):
            bucket[i] += value

    for leaf in leaves:
        team = group_label(leaf.get(PIVOT_COL_GROUPBY), selection_maps.get(PIVOT_COL_GROUPBY))
        values = [leaf.get(name) or 0 for name, _ in MEASURES]
        path = []
        for field in PIVOT_ROW_GROUPBY:
            path.append(group_label(leaf.get(field), selection_maps.get(field.split(":")[0])))
            accumulate(tuple(path), team, values)
        accumulate((), team, values)

    def cells(key, bold):
        row = []
        for team in teams:
            for value in totals[key][team]:
                row.append({"is_bold": bold, "value": value})
        return row

    rows = [{"title": "Total", "indent": 0, "values": cells((), True)}]
    for key in order:
        if not key:
            continue
        rows.append({"title": key[-1], "indent": len(key), "values": cells(key, False)})

    col_group_headers = [
        [{"title": "Total", "width": n_cols, "height": 1, "is_bold": False}],
        [{"title": team, "width": n_measures, "height": 1, "is_bold": False} for team in teams],
    ]
    measure_headers = [
        {"title": label, "width": 1, "height": 1, "is_bold": False}
        for _ in teams
        for _, label in MEASURES
    ]

    data = {
        "model": "sale.report",
        "title": "Sales Analysis",
        "col_group_headers": col_group_headers,
        "measure_headers": measure_headers,
        "origin_headers": [],
        "rows": rows,
        "measure_count": n_measures,
        "origin_count": 1,
    }
    write_file(out_path, client.post_export("/web/pivot/export_xlsx", data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quarter",
        help="Quarter to export: 2026Q3, 'current' (default) or 'previous'.",
    )
    parser.add_argument(
        "--include-previous",
        action="store_true",
        default=True,  # always two quarters; kept as a flag for old callers
        help="Also refresh the quarter before the target one, so a quarter that "
        "just closed gets a final export.",
    )
    parser.add_argument(
        "--out-dir",
        default=BASE_DIR,
        help="Directory to write the two xlsx files into.",
    )
    parser.add_argument("--current-only", action="store_true", help="Skip the previous quarter (rare).")
    parser.add_argument("--skip-orders", action="store_true", help="Only export Sales Analysis.")
    parser.add_argument("--skip-analysis", action="store_true", help="Only export Sales Orders.")
    args = parser.parse_args()

    targets = [parse_quarter(args.quarter) if args.quarter else current_quarter()]
    if args.include_previous and not args.current_only:
        targets.insert(0, previous_quarter(*targets[0]))

    env = load_env()
    client = OdooClient(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USERNAME"], env["ODOO_PASSWORD"]).login()
    print(f"Logged in to {env['ODOO_DB']} as uid {client.uid}")

    for year, quarter in targets:
        start, end = quarter_bounds(year, quarter)
        prefix = f"{sequence_number(year, quarter)}. {year} Q{quarter}"
        print(f"\n{year} Q{quarter} - file prefix '{prefix}'")
        print(f"  date range (UTC): {start} .. {end}")

        if not args.skip_orders:
            export_sales_orders(client, start, end, os.path.join(args.out_dir, f"{prefix} Sales Order.xlsx"))
        if not args.skip_analysis:
            export_sales_analysis(client, start, end, os.path.join(args.out_dir, f"{prefix} Sales Analysis.xlsx"))

    print("\nDone.")


if __name__ == "__main__":
    main()
