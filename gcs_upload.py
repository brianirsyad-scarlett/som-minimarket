"""Upload a folder of downloaded reports to its draft GCS prefix.

    python gcs_upload.py --chain alfamart --report market_share --src out/ms
    python gcs_upload.py --chain indomaret --report sell_out_store --src "out/Daily Sell Out" --skip-existing

--require-files makes an empty folder a failure. A download step that quietly
produced nothing must not show up as a green run - that exact silent-success
trap hid broken runs for weeks in the earlier b2b_* repos.
"""

import argparse
import sys
from pathlib import Path

from google.cloud import storage

import gcs_paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", required=True, choices=gcs_paths.CHAINS)
    ap.add_argument("--report", required=True, choices=gcs_paths.REPORTS)
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--pattern", default="*", help="glob inside --src (default: every file)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="leave objects that already exist untouched (closed periods never change)")
    ap.add_argument("--require-files", action="store_true", help="fail if --src holds no files")
    a = ap.parse_args()

    files = sorted(p for p in a.src.glob(a.pattern) if p.is_file()) if a.src.exists() else []
    if not files:
        print(f"No files in {a.src} matching {a.pattern!r}.")
        return 1 if a.require_files else 0

    bucket = storage.Client().bucket(gcs_paths.BUCKET)
    prefix = gcs_paths.prefix(a.chain, a.report)
    uploaded = skipped = 0
    for f in files:
        blob = bucket.blob(prefix + f.name)
        if a.skip_existing and blob.exists():
            skipped += 1
            continue
        blob.upload_from_filename(str(f))
        uploaded += 1
        print(f"  uploaded gs://{gcs_paths.BUCKET}/{prefix}{f.name}  ({f.stat().st_size:,} bytes)")
    print(f"--- {a.chain}/{a.report}: {uploaded} uploaded, {skipped} already there ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
