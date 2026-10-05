from pathlib import Path

p = Path.home() / "odoo_export.py"
content = p.read_text()

content = content.replace(
    '"date_order", "name", "partner_id", "commercial_partner_id", "team_id",',
    '"date", "name", "partner_id", "commercial_partner_id", "team_id",',
)

parts = content.rsplit('"date_order": "Order Date",', 1)
if len(parts) == 2:
    content = parts[0] + '"date": "Order Date",' + parts[1]

content = content.replace(
    'sa_domain = [("date_order", ">=", ANALYSIS_START), ("date_order", "<", ANALYSIS_END)]',
    'sa_domain = [("date", ">=", ANALYSIS_START), ("date", "<", ANALYSIS_END)]',
)

p.write_text(content)
print("Fixed.")
