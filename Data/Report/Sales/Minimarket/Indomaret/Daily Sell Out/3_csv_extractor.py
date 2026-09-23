import os
import shutil
import tempfile
import gzip
import patoolib
from patoolib.util import PatoolError

base_dir = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret\Daily Sell Out"

def is_archive_file(file_path):
    """Check if a file is a valid archive using patool (by content, not extension)."""
    try:
        patoolib.util.test_archive(file_path)
        return True
    except PatoolError:
        return False
    except Exception:
        return False

def extract_gz(file_path, dest_dir):
    """Decompress a .gz file into dest_dir."""
    try:
        out_name = os.path.basename(file_path)
        if out_name.lower().endswith('.gz'):
            out_name = out_name[:-3]
        if not out_name:
            out_name = "decompressed"

        out_path = os.path.join(dest_dir, out_name)

        with gzip.open(file_path, 'rb') as f_in:
            with open(out_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        return out_path
    except Exception as e:
        print(f"    GZIP decompression failed: {e}")
        return None

def extract_archive(archive_path, dest_dir):
    """Extract archive to dest_dir using patool, return True if successful."""
    try:
        patoolib.extract_archive(archive_path, outdir=dest_dir, interactive=False)
        return True
    except Exception as e:
        print(f"    Extraction failed: {e}")
        return False

def move_contents(src_dir, dest_dir):
    """Move all files from src_dir to dest_dir, handling name conflicts."""
    for root, dirs, files in os.walk(src_dir):
        for file in files:
            src_file = os.path.join(root, file)
            dst_file = os.path.join(dest_dir, file)

            if os.path.exists(dst_file):
                base, ext = os.path.splitext(file)
                counter = 1
                while os.path.exists(os.path.join(dest_dir, f"{base}_{counter}{ext}")):
                    counter += 1
                dst_file = os.path.join(dest_dir, f"{base}_{counter}{ext}")
                print(f"      Conflict: {file} → renamed to {os.path.basename(dst_file)}")

            shutil.move(src_file, dst_file)

        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            try:
                os.rmdir(dir_path)
            except OSError:
                pass

def main():
    for item in os.listdir(base_dir):
        item_path = os.path.join(base_dir, item)
        if not os.path.isfile(item_path):
            continue

        print(f"\nChecking: {item}")

        # Special handling for .gz files
        if item.lower().endswith('.gz'):
            print("  Detected .gz extension – using gzip module.")
            with tempfile.TemporaryDirectory() as tmp_dir:
                decompressed_path = extract_gz(item_path, tmp_dir)
                if decompressed_path:
                    print("  Decompression successful.")
                    move_contents(tmp_dir, base_dir)
                    os.remove(item_path)
                    print(f"  Deleted original .gz file: {item}")
                else:
                    print("  Decompression failed. File left untouched.")
            continue

        # Try patool content detection for RAR, 7z, TAR, etc.
        if not is_archive_file(item_path):
            print("  Not an archive (or unsupported format). Skipping.")
            continue

        print(f"  ✓ Recognised as archive (disguised as {os.path.splitext(item)[1] or 'no extension'})")

        with tempfile.TemporaryDirectory() as tmp_dir:
            if extract_archive(item_path, tmp_dir):
                print("  Extraction successful.")
                move_contents(tmp_dir, base_dir)
                os.remove(item_path)
                print(f"  Deleted original archive: {item}")
            else:
                print("  Extraction failed. Archive left untouched.")

    print("\n✅ All done.")

if __name__ == "__main__":
    main()