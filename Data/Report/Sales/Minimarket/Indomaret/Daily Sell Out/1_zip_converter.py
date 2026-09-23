import zipfile
import shutil
from pathlib import Path

def is_valid_zip(file_path):
    """Return True if Python's zipfile module can safely process the archive."""
    try:
        return zipfile.is_zipfile(file_path)
    except Exception:
        return False

def extract_zip(zip_path):
    """Extract a ZIP file into a folder named after it (without extension)."""
    target = zip_path.parent / zip_path.stem
    target.mkdir(exist_ok=True)
    print(f"  Extracting: {zip_path.name} -> {target.name}/")
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(target)
        zip_path.unlink()  # Delete original archive after successful extraction
        return True
    except (zipfile.BadZipFile, Exception) as e:
        print(f"  ❌ Skipping broken file: {zip_path.name} ({e})")
        # Optional: move or rename broken files so they aren't re-processed endlessly
        # zip_path.rename(zip_path.with_suffix('.corrupted'))
        return False

def extract_all_until_done(root_dir):
    """Keep scanning for any file that is a valid ZIP and extract it until none remain."""
    root = Path(root_dir).resolve()
    total = 0
    iteration = 0
    
    while True:
        iteration += 1
        # Find all files that zipfile validates
        zips = [f for f in root.rglob('*') if f.is_file() and is_valid_zip(f)]
        
        if not zips:
            break
            
        print(f"\n[Pass {iteration}] Found {len(zips)} ZIP(s).")
        extracted_in_pass = 0
        
        for z in zips:
            if extract_zip(z):
                total += 1
                extracted_in_pass += 1
                
        # Guard against infinite loops if a file cannot be unlinked or repaired
        if extracted_in_pass == 0:
            print("\n⚠️ No further files could be extracted. Stopping to prevent an infinite loop.")
            break

    print(f"\n✅ Done. Extracted {total} ZIP archive(s).")
    return total

def main():
    target = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret\Daily Sell Out"
    print("=" * 70)
    print("PURE ZIP EXTRACTOR – Recursively extracts ALL nested ZIPs")
    print(f"Target: {target}")
    print("\n⚠️  WARNING: Every valid ZIP file will be DELETED after extraction.")
    print("Make a backup if needed.")
    print("=" * 70)
    
    confirm = input("Type YES to continue: ")
    if confirm != "YES":
        return

    extract_all_until_done(target)
    print("\nAll valid nested ZIPs have been extracted.")

if __name__ == "__main__":
    main()