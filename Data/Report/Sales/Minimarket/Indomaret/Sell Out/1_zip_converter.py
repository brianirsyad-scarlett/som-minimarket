import zipfile
import shutil
from pathlib import Path

def is_zip(file_path):
    """Return True if file starts with PK (ZIP magic)."""
    try:
        with open(file_path, 'rb') as f:
            return f.read(4) == b'PK\x03\x04'
    except:
        return False

def extract_zip(zip_path):
    """Extract a ZIP file into a folder named after it (without extension)."""
    target = zip_path.parent / zip_path.stem
    target.mkdir(exist_ok=True)
    print(f"  Extracting: {zip_path.name} -> {target.name}/")
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(target)
    zip_path.unlink()  # delete original to avoid reprocessing
    return target

def extract_all_until_done(root_dir):
    """Keep scanning for any file that is a ZIP and extract it until none remain."""
    root = Path(root_dir).resolve()
    total = 0
    iteration = 0
    while True:
        iteration += 1
        # Find all files (any name) that are ZIP archives
        zips = [f for f in root.rglob('*') if f.is_file() and is_zip(f)]
        if not zips:
            break
        print(f"\n[Pass {iteration}] Found {len(zips)} ZIP(s).")
        for z in zips:
            extract_zip(z)
            total += 1
    print(f"\n✅ Done. Extracted {total} ZIP archive(s).")
    return total

def main():
    target = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret\Sell Out"
    print("=" * 70)
    print("PURE ZIP EXTRACTOR – Recursively extracts ALL nested ZIPs (by magic bytes)")
    print(f"Target: {target}")
    print("\n⚠️  WARNING: Every file that is a ZIP (including those with no extension) will be DELETED after extraction.")
    print("Make a backup if needed.")
    print("=" * 70)
    confirm = input("Type YES to continue: ")
    if confirm != "YES":
        return

    extract_all_until_done(target)
    print("\nAll nested ZIPs have been extracted. The final files (CSV or others) are now in subfolders.")

if __name__ == "__main__":
    main()