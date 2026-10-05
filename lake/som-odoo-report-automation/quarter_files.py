"""Single source of truth for which quarter/year Odoo Report files exist.

Reports before this numbering scheme started are a fixed legacy file
(2025, combined by hand into one annual export outside this pipeline's
reach). Every quarter from 2026 Q1 onward follows "<seq>. <year> Qn" and is
computed from the calendar date, so nothing here needs editing when a new
quarter starts.
"""
from datetime import datetime

SEQ_BASE_YEAR = 2026
SEQ_BASE_NUMBER = 13
LEGACY_ANNUAL_FILE = "12. 2025 Odoo Report.xlsx"


def quarter_prefix(year, quarter):
    seq = SEQ_BASE_NUMBER + (year - SEQ_BASE_YEAR) * 4 + (quarter - 1)
    return f"{seq}. {year} Q{quarter}"


def current_quarter():
    today = datetime.now()
    return today.year, (today.month - 1) // 3 + 1


def quarters_through(end_year, end_quarter, start_year=SEQ_BASE_YEAR, start_quarter=1):
    """Yield (year, quarter) tuples from start to end, inclusive."""
    year, quarter = start_year, start_quarter
    while (year, quarter) <= (end_year, end_quarter):
        yield (year, quarter)
        quarter += 1
        if quarter > 4:
            quarter = 1
            year += 1


def master_source_files(end_year=None, end_quarter=None):
    """Every "<prefix> Odoo Report.xlsx" filename the master report combines,
    in order, up through end_year/end_quarter (defaults to the current quarter).
    """
    if end_year is None or end_quarter is None:
        end_year, end_quarter = current_quarter()
    files = [LEGACY_ANNUAL_FILE]
    for year, quarter in quarters_through(end_year, end_quarter):
        files.append(f"{quarter_prefix(year, quarter)} Odoo Report.xlsx")
    return files
