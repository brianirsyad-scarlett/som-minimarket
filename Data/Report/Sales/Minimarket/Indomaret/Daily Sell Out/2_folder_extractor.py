import os
import shutil

# Source directory (where the subfolders are)
base_dir = r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Report\Sales\Minimarket\Indomaret\Daily Sell Out"

# Loop through every item inside base_dir
for item in os.listdir(base_dir):
    item_path = os.path.join(base_dir, item)
    
    # Only process subfolders (skip files)
    if os.path.isdir(item_path):
        print(f"Processing folder: {item}")
        
        # Move all files from the subfolder up to base_dir
        for file_name in os.listdir(item_path):
            src_file = os.path.join(item_path, file_name)
            dst_file = os.path.join(base_dir, file_name)
            
            # Only move files (skip any sub-subfolders inside)
            if os.path.isfile(src_file):
                # Handle potential filename conflicts
                if os.path.exists(dst_file):
                    print(f"  Warning: {file_name} already exists in destination. Skipping.")
                else:
                    shutil.move(src_file, dst_file)
                    print(f"  Moved: {file_name}")
        
        # After moving files, try to delete the empty folder
        try:
            os.rmdir(item_path)   # Only works if folder is empty
            print(f"  Deleted folder: {item}")
        except OSError:
            print(f"  Folder {item} not empty (maybe had subfolders). Skipping deletion.")