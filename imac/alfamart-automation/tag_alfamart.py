#!/usr/bin/env python3
"""Apply the macOS Finder tag "Alfamart" to every file/folder belonging to this
pipeline, so they're all findable from the Finder tag sidebar."""

import os
import plistlib
import subprocess

TAG = "Alfamart"
HOME = os.path.expanduser("~")
ONEDRIVE = os.path.join(
    HOME, "Library/CloudStorage/OneDrive-PT.OptoLumbungSejahtera/SOM_iMac/iMac_Sales Ops"
)

TARGETS = [
    os.path.join(HOME, "alfamart-automation"),
    os.path.join(HOME, "Library/LaunchAgents/com.salesops.alfamart-daily.plist"),
    os.path.join(HOME, ".alfamart.env"),
    os.path.join(ONEDRIVE, "Alfamart"),
    os.path.join(ONEDRIVE, "_Junk (safe to delete)/b2b.alfamart.co.id.har"),
    os.path.join(ONEDRIVE, "_Junk (safe to delete)/b2b-np.alfamart.co.id.har"),
]

# Everything inside these directories gets tagged too (one level, files only).
RECURSE_DIRS = [
    os.path.join(HOME, "alfamart-automation"),
    os.path.join(ONEDRIVE, "Alfamart"),
]


def set_tag(path):
    payload = plistlib.dumps([TAG], fmt=plistlib.FMT_BINARY)
    r = subprocess.run(
        ["xattr", "-w", "-x", "com.apple.metadata:_kMDItemUserTags",
         payload.hex(), path],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def main():
    paths = []
    for t in TARGETS:
        if os.path.exists(t):
            paths.append(t)
    for d in RECURSE_DIRS:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name.startswith(".") or name == "browser_profile":
                continue
            p = os.path.join(d, name)
            if os.path.isfile(p):
                paths.append(p)

    ok = fail = 0
    for p in paths:
        if set_tag(p):
            ok += 1
        else:
            fail += 1
            print(f"  could not tag: {p}")
    print(f"tagged {ok} item(s) with {TAG!r}" + (f", {fail} failed" if fail else ""))


if __name__ == "__main__":
    main()
