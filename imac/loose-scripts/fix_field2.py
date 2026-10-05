from pathlib import Path

p = Path.home() / "odoo_export.py"
content = p.read_text()

content = content.replace(
    '    "product_uom_qty", "qty_delivered", "qty_invoiced", "qty_to_invoice",\n    "price_total", "price_subtotal",\n]',
    '    "product_uom_qty", "qty_delivered", "qty_invoiced", "qty_to_invoice",\n]',
)

content = content.replace(
    '    "qty_to_invoice": "Qty To Invoice",\n    "price_total": "Total",\n    "price_subtotal": "Untaxed Total",\n}',
    '    "qty_to_invoice": "Qty To Invoice",\n}',
)

p.write_text(content)
print("Fixed.")
