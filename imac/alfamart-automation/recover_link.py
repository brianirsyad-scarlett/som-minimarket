"""Recover a download link whose signature lost its first byte to the mail
tool's double quoted-printable decode, then download the file.

The mail reader decoded QP twice, so the original "=XY" (first two hex chars of
the signature) collapsed into a single byte. When that byte was >0x7F it became
U+FFFD and the two hex chars are unrecoverable by inspection -- but they're
constrained to 0x80..0xFF, so 128 candidates can just be tested directly.

A V4 signed URL commits to the HTTP method, so probing must use GET; a 1-byte
Range request keeps each probe cheap without affecting the signature.
"""
import sys
import urllib.error
import urllib.request

BASE = "https://b2bsat-bucket.storage.googleapis.com/excel/report/"


def build(filename, date_stamp, sig_tail, hex_prefix):
    return (
        f"{BASE}{filename}"
        "?X-Goog-Algorithm=GOOG4-RSA-SHA256"
        "&X-Goog-Credential=b2b-sat-production%40appspot.gserviceaccount.com"
        f"%2F{date_stamp[:8]}%2Fauto%2Fstorage%2Fgoog4_request"
        f"&X-Goog-Date={date_stamp}"
        "&X-Goog-Expires=86400"
        "&X-Goog-SignedHeaders=host"
        f"&x-goog-signature={hex_prefix}{sig_tail}"
    )


def probe(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 206)
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


def recover(filename, date_stamp, sig_tail):
    # Full 0x00..0xFF range: bytes >0x7F come through as U+FFFD (lost), but
    # control chars and plain ASCII survive visibly -- either way the two hex
    # chars they stand for are excluded from sig_tail, so probe all of them.
    for b in range(0x00, 0x100):
        hex_prefix = format(b, "02x")
        url = build(filename, date_stamp, sig_tail, hex_prefix)
        if probe(url):
            print(f"recovered signature prefix: {hex_prefix}")
            return url
    return None


def download(url, out_path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    total = 0
    with urllib.request.urlopen(req, timeout=1800) as resp, open(out_path, "wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    return total


if __name__ == "__main__":
    filename, date_stamp, sig_tail, out_path = sys.argv[1:5]
    url = recover(filename, date_stamp, sig_tail)
    if not url:
        sys.exit("could not recover signature prefix")
    size = download(url, out_path)
    print(f"saved {out_path} ({size/1024/1024:.1f} MB)")
