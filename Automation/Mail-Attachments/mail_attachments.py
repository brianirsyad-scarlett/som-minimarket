"""
Outlook mail - attachment harvester.

Saves attachments out of the local Outlook desktop profile into the SOM Data
tree, so incoming report files land on disk without anyone clicking "Save As"
thirty times a morning.

Why COM and not a cloud connector: the Microsoft 365 / Gmail connectors are
blocked at the tenant level for this account. This talks to the Outlook client
already running on this PC via MAPI/COM - the same mechanism the Sell In MT/GT
refresh uses. Nothing authenticates to anything; it reads the mailbox that the
logged-in user already has open. No credentials are stored by this script.

Flow:
  1. Attach to Outlook.Application (starts it if not running).
  2. Resolve the configured folder path, e.g. "Inbox/Sales Reports".
  3. Restrict to messages received in the last N days, newest first.
  4. For each real (non-inline) attachment:
       - reject anything on the executable blocklist, unconditionally
       - reject anything not on the configured allowlist
       - reject anything over the size cap
       - save to a temp file, SHA-256 it, and skip if we've saved it before
       - route to a destination subfolder via rules.json, else the catch-all
  5. Record the hash in state.json so re-runs are idempotent.

The mailbox is treated as read-only by default: nothing is deleted, moved, or
marked read unless MAIL_MARK_READ is turned on.

Run with --dry-run first. It prints exactly what it would save and writes
nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

try:
    import pythoncom
    import win32com.client
    from dotenv import load_dotenv
except ImportError as exc:
    sys.exit(f"Missing dependency ({exc.name}). Run setup.ps1 first.")

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
RULES_FILE = HERE / "rules.json"
LOG_FILE = HERE / "run.log"

DEFAULT_DEST = Path(r"D:\SCARLETT_512\SCARLETT-329\SOM\Data\Mail Attachments")

# Outlook enum values (we don't import the typelib, so these are spelled out).
OL_FOLDER_INBOX = 6
OL_MAIL_ITEM = 43

# Anything that Windows can execute, script, or side-load. This is NOT
# configurable on purpose. An unattended job that writes attacker-supplied
# .exe/.dll/.lnk files to a synced drive is a malware delivery pipeline, and
# this project already has one pirated-installer bundle sitting in
# Data\Sent Email that arrived exactly that way.
BLOCKED_EXT = {
    ".exe", ".dll", ".scr", ".com", ".pif", ".bat", ".cmd", ".msi", ".msp",
    ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
    ".cpl", ".reg", ".lnk", ".inf", ".sys", ".drv", ".ocx", ".jar", ".apk",
    ".iso", ".img", ".vhd", ".chm", ".application", ".gadget", ".msc",
}

# Office files that can carry macros. Allowed only if explicitly listed in
# MAIL_ALLOWED_EXT - they are not in the default allowlist.
MACRO_EXT = {".xlsm", ".xlsb", ".docm", ".pptm", ".dotm", ".xltm"}

WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

log = logging.getLogger("mail_attachments")


def setup_logging(verbose: bool) -> None:
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    log.addHandler(stream)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(fmt)
    log.addHandler(handler)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        log.warning("%s=%r is not a number, using %d", name, raw, default)
        return default


def safe_filename(name: str) -> str:
    """Strip anything that could escape the destination folder or break NTFS."""
    name = name.replace("\x00", "")
    # Outlook hands back the display name, but a crafted attachment can still
    # contain separators or traversal - take the last path component only.
    name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r'[<>:"|?*]', "_", name)
    name = "".join(ch for ch in name if ord(ch) >= 32)
    name = name.strip().rstrip(".")
    if not name:
        return "attachment"
    stem = name.split(".")[0].upper()
    if stem in WINDOWS_RESERVED:
        name = f"_{name}"
    return name[:180]


def unique_path(folder: Path, filename: str) -> Path:
    """Never overwrite: foo.xlsx -> foo (2).xlsx -> foo (3).xlsx ..."""
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(2, 1000):
        candidate = folder / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return folder / f"{stem} ({datetime.now():%Y%m%d%H%M%S}){suffix}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s (%s) - starting fresh.", path.name, exc)
        return fallback


def resolve_folder(namespace, folder_path: str):
    """Resolve "Inbox/Sales Reports" against the default message store."""
    parts = [p for p in re.split(r"[\\/]", folder_path) if p.strip()]
    if not parts or parts[0].lower() == "inbox":
        current = namespace.GetDefaultFolder(OL_FOLDER_INBOX)
        parts = parts[1:] if parts else []
    else:
        # Not under Inbox - walk from the store roots instead.
        root_name = parts[0].lower()
        current = None
        for store_root in namespace.Folders:
            if store_root.Name.lower() == root_name:
                current = store_root
                break
        if current is None:
            raise SystemExit(f"No mail folder or store named {parts[0]!r}.")
        parts = parts[1:]

    for part in parts:
        for child in current.Folders:
            if child.Name.lower() == part.lower():
                current = child
                break
        else:
            raise SystemExit(f"Folder {part!r} not found under {current.Name!r}.")
    return current


def iter_folders(folder, include_subfolders: bool):
    yield folder
    if include_subfolders:
        for child in folder.Folders:
            yield from iter_folders(child, True)


def recent_items(folder, cutoff: datetime):
    """Newest-first items received since cutoff.

    Restrict() is far faster on a large mailbox, but its date literal is
    locale-sensitive and throws on some profiles - fall back to a sorted walk
    that breaks out as soon as it passes the cutoff.
    """
    items = folder.Items
    try:
        items.Sort("[ReceivedTime]", True)
    except pythoncom.com_error:
        pass

    try:
        restricted = items.Restrict(
            f"[ReceivedTime] >= '{cutoff:%m/%d/%Y %I:%M %p}'"
        )
        restricted.Sort("[ReceivedTime]", True)
        for item in restricted:
            yield item
        return
    except pythoncom.com_error as exc:
        log.debug("Restrict() unavailable on %r (%s) - walking manually.",
                  folder.Name, exc)

    for item in items:
        try:
            received = item.ReceivedTime
        except (AttributeError, pythoncom.com_error):
            continue
        if received.replace(tzinfo=None) < cutoff:
            break
        yield item


def is_inline(attachment) -> bool:
    """Signature images and pasted screenshots carry a MAPI content-id."""
    try:
        cid = attachment.PropertyAccessor.GetProperty(
            "http://schemas.microsoft.com/mapi/proptag/0x3712001F"
        )
        return bool(cid)
    except pythoncom.com_error:
        return False


def sender_of(item) -> str:
    for attr in ("SenderEmailAddress", "SenderName"):
        try:
            value = getattr(item, attr)
            if value:
                return str(value)
        except (AttributeError, pythoncom.com_error):
            continue
    return ""


def match_rule(rules: list, sender: str, subject: str, filename: str):
    """First matching rule wins; returns its dest subfolder or None."""
    sender_l, subject_l, filename_l = sender.lower(), subject.lower(), filename.lower()
    for rule in rules:
        froms = [f.lower() for f in rule.get("from_contains", [])]
        if froms and not any(f in sender_l for f in froms):
            continue
        subject_re = rule.get("subject_regex")
        if subject_re and not re.search(subject_re, subject_l, re.I):
            continue
        filename_re = rule.get("filename_regex")
        if filename_re and not re.search(filename_re, filename_l, re.I):
            continue
        return rule.get("dest") or None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save Outlook attachments into the SOM Data tree.")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be saved, write nothing")
    parser.add_argument("--days", type=int,
                        help="override MAIL_LOOKBACK_DAYS")
    parser.add_argument("--folder",
                        help="override MAIL_FOLDER, e.g. \"Inbox/Sales Reports\"")
    parser.add_argument("--verbose", action="store_true",
                        help="log every skipped attachment and why")
    args = parser.parse_args()

    setup_logging(args.verbose)
    load_dotenv(HERE / ".env")

    folder_path = args.folder or os.getenv("MAIL_FOLDER", "Inbox")
    lookback = args.days if args.days is not None else env_int("MAIL_LOOKBACK_DAYS", 7)
    include_sub = env_bool("MAIL_INCLUDE_SUBFOLDERS", False)
    skip_inline = env_bool("MAIL_SKIP_INLINE", True)
    mark_read = env_bool("MAIL_MARK_READ", False)
    max_bytes = env_int("MAIL_MAX_MB", 50) * 1024 * 1024

    dest_root = Path(os.getenv("MAIL_DEST_ROOT", "").strip() or DEFAULT_DEST)

    allowed_raw = os.getenv("MAIL_ALLOWED_EXT",
                            "xlsx,xls,csv,pdf,docx,doc,txt,pptx,zip")
    allowed = {
        ("." + e.strip().lstrip(".").lower())
        for e in allowed_raw.split(",") if e.strip()
    }
    # A misconfigured allowlist must never re-enable an executable type.
    collisions = allowed & BLOCKED_EXT
    if collisions:
        log.warning("Ignoring blocked extension(s) in MAIL_ALLOWED_EXT: %s",
                    ", ".join(sorted(collisions)))
        allowed -= BLOCKED_EXT

    rules = load_json(RULES_FILE, [])
    if not isinstance(rules, list):
        log.warning("rules.json is not a list - ignoring it.")
        rules = []

    state = load_json(STATE_FILE, {})
    saved_hashes = set(state.get("saved", {}))

    cutoff = datetime.now() - timedelta(days=lookback)
    log.info("Scanning %r (subfolders=%s) for mail since %s",
             folder_path, include_sub, f"{cutoff:%Y-%m-%d %H:%M}")
    if args.dry_run:
        log.info("DRY RUN - nothing will be written.")

    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")

        # On an unconfigured profile, Accounts does not come back empty - it
        # throws "You are not connected" (0x1000). Treat that the same as zero
        # accounts so the operator gets the fix instead of a COM dump.
        try:
            account_count = len(namespace.Accounts)
        except pythoncom.com_error:
            account_count = 0
        if not account_count:
            log.error("Outlook has no usable mail account.")
            log.error("Either it was never set up (it still opens on the "
                      "first-run wizard), or it is offline. Open Outlook, add "
                      "your work account, let it sync - then re-run. "
                      "See README.md step 1.")
            return 2

        root = resolve_folder(namespace, folder_path)

        saved_count = skipped_count = blocked_count = 0
        new_entries = {}

        for folder in iter_folders(root, include_sub):
            log.debug("Folder: %s", folder.Name)
            for item in recent_items(folder, cutoff):
                try:
                    if item.Class != OL_MAIL_ITEM:
                        continue
                    attachments = item.Attachments
                    if not attachments.Count:
                        continue
                    subject = str(item.Subject or "")
                    sender = sender_of(item)
                except (AttributeError, pythoncom.com_error):
                    continue

                for i in range(1, attachments.Count + 1):
                    attachment = attachments.Item(i)
                    try:
                        raw_name = str(attachment.FileName or "")
                        size = int(attachment.Size or 0)
                    except (AttributeError, pythoncom.com_error):
                        continue
                    if not raw_name:
                        continue

                    filename = safe_filename(raw_name)
                    ext = Path(filename).suffix.lower()

                    if skip_inline and is_inline(attachment):
                        log.debug("  inline, skipped: %s", filename)
                        continue
                    if ext in BLOCKED_EXT:
                        log.warning("  BLOCKED executable %r from %s (%r)",
                                    filename, sender or "unknown", subject[:60])
                        blocked_count += 1
                        continue
                    if ext in MACRO_EXT and ext not in allowed:
                        log.warning("  BLOCKED macro-enabled %r from %s",
                                    filename, sender or "unknown")
                        blocked_count += 1
                        continue
                    if ext not in allowed:
                        log.debug("  not allowlisted (%s): %s", ext, filename)
                        skipped_count += 1
                        continue
                    if size > max_bytes:
                        log.info("  too large (%.1f MB): %s",
                                 size / 1024 / 1024, filename)
                        skipped_count += 1
                        continue

                    subfolder = match_rule(rules, sender, subject, filename)
                    dest_dir = dest_root / subfolder if subfolder else dest_root

                    if args.dry_run:
                        log.info("  WOULD SAVE %s -> %s  (from %s)",
                                 filename, dest_dir, sender or "unknown")
                        saved_count += 1
                        continue

                    # Save to temp first so we can hash before committing - that
                    # is what makes re-runs idempotent regardless of filename.
                    tmp_dir = Path(tempfile.mkdtemp(prefix="mailatt_"))
                    tmp_path = tmp_dir / filename
                    try:
                        attachment.SaveAsFile(str(tmp_path))
                    except pythoncom.com_error as exc:
                        log.error("  could not save %r: %s", filename, exc)
                        _cleanup(tmp_path, tmp_dir)
                        continue

                    digest = sha256_of(tmp_path)
                    if digest in saved_hashes:
                        log.debug("  already saved previously: %s", filename)
                        _cleanup(tmp_path, tmp_dir)
                        continue

                    dest_dir.mkdir(parents=True, exist_ok=True)
                    final = unique_path(dest_dir, filename)
                    tmp_path.replace(final)
                    _cleanup(None, tmp_dir)

                    saved_hashes.add(digest)
                    new_entries[digest] = {
                        "file": str(final),
                        "from": sender,
                        "subject": subject[:200],
                        "saved_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    saved_count += 1
                    log.info("  saved %s -> %s", filename, final)

                if mark_read and not args.dry_run:
                    try:
                        item.UnRead = False
                        item.Save()
                    except pythoncom.com_error:
                        pass

        if new_entries and not args.dry_run:
            state.setdefault("saved", {}).update(new_entries)
            state["last_run"] = datetime.now().isoformat(timespec="seconds")
            STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

        verb = "would save" if args.dry_run else "saved"
        log.info("Done - %d %s, %d skipped, %d blocked.",
                 saved_count, verb, skipped_count, blocked_count)
        if blocked_count:
            log.warning("%d attachment(s) were blocked as executable or "
                        "macro-enabled. Check run.log before trusting the sender.",
                        blocked_count)
        return 0

    except pythoncom.com_error as exc:
        log.error("Outlook COM error: %s", exc)
        log.error("Is Outlook installed and set up? See README.md.")
        return 1
    finally:
        pythoncom.CoUninitialize()


def _cleanup(file_path, folder_path) -> None:
    try:
        if file_path is not None and Path(file_path).exists():
            Path(file_path).unlink()
        if folder_path is not None and Path(folder_path).exists():
            Path(folder_path).rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
