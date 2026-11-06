"""Generate checkpoint tokens, short codes and printable QR pages.

Reads config.json for the base URL and checkpoint names, writes the
checkpoints (token + 5 char fallback code) into data.json, then builds
one printable HTML page per checkpoint in ./qr_pages.

Usage:
    python generate_qr.py            # create checkpoints (keeps existing ones), rebuild pages
    python generate_qr.py --pages    # rebuild pages only, change nothing in data.json
    python generate_qr.py --force    # wipe ALL checkpoints and regenerate (invalidates printed QRs!)

config.json needs:
    "base_url": "https://your-host"          where phones will reach the app
    "checkpoints": ["Name 1", "Name 2", ...] one entry per checkpoint
    "admin_phone": "021 000 0000"            optional, printed on the pages for the text fallback
"""
import argparse
import html
import json
import os
import secrets
import sys

import segno

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.json")
DATA = os.path.join(BASE, "data.json")
OUT = os.path.join(BASE, "qr_pages")

# Uppercase letters and digits with look-alikes removed (no 0/O, 1/I/L).
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 5


def load_json(path, default):
    """Load JSON, treating a missing or empty file as `default`."""
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    return json.loads(text) if text else default


def save_json(path, obj):
    """Write atomically so a crash can't leave a half written file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def new_token():
    return secrets.token_urlsafe(16)  # 128 bits, unguessable


def new_code(used):
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
        if code not in used:
            return code


def make_checkpoint(cp_id, name, used_codes):
    code = new_code(used_codes)
    used_codes.add(code)
    return {
        "id": cp_id,
        "name": name,
        "token": new_token(),
        "short_code": code,
        "enabled": True,
    }


def page_body(cp, base_url, admin_phone):
    url = f"{base_url}/s/{cp['token']}"
    svg = segno.make(url, error="m").svg_inline(scale=12, border=2)
    code = cp["short_code"]
    help_line = "Can't scan? Text your boat ID and this code"
    if admin_phone:
        help_line += f" to {html.escape(admin_phone)}"
    return f"""
<section class="page">
  <h1>{html.escape(cp['name'])}</h1>
  <div class="qr">{svg}</div>
  <p class="help">{help_line}</p>
  <p class="code">{html.escape(code)}</p>
</section>"""


PAGE_CSS = """
@page { size: A4; margin: 15mm; }
body { font-family: system-ui, sans-serif; margin: 0; }
.page { text-align: center; page-break-after: always; padding-top: 10mm; }
.page:last-child { page-break-after: auto; }
h1 { font-size: 36pt; margin: 0 0 8mm; }
.qr svg { width: 120mm; height: 120mm; }
.help { font-size: 16pt; margin: 8mm 0 2mm; }
.code { font-family: ui-monospace, Consolas, monospace; font-size: 56pt;
        letter-spacing: 0.25em; margin: 0; font-weight: 700; }
"""


def write_pages(checkpoints, base_url, admin_phone):
    os.makedirs(OUT, exist_ok=True)
    bodies = []
    for cp in checkpoints:
        body = page_body(cp, base_url, admin_phone)
        bodies.append(body)
        path = os.path.join(OUT, f"checkpoint_{cp['id']}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(wrap(cp["name"], body))
    with open(os.path.join(OUT, "all_checkpoints.html"), "w", encoding="utf-8") as f:
        f.write(wrap("All checkpoints", "".join(bodies)))
    print(f"Wrote {len(checkpoints)} page(s) + all_checkpoints.html to {OUT}")


def wrap(title, body):
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{PAGE_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", action="store_true", help="rebuild pages only, no data changes")
    ap.add_argument("--force", action="store_true", help="regenerate ALL tokens and codes")
    args = ap.parse_args()

    config = load_json(CONFIG, {})
    base_url = str(config.get("base_url", "")).rstrip("/")
    names = config.get("checkpoints", [])
    admin_phone = config.get("admin_phone", "")

    if not base_url:
        sys.exit("config.json is missing 'base_url'.")
    if "localhost" in base_url or "127.0.0.1" in base_url:
        print("Warning: base_url points at localhost, phones will not be able to reach it.")

    data = load_json(DATA, {})
    existing = [] if args.force else data.get("checkpoints", [])

    if not args.pages:
        if not names:
            sys.exit("config.json is missing a 'checkpoints' list.")
        used = {cp["short_code"] for cp in existing}
        checkpoints = list(existing)
        for i, name in enumerate(names, start=1):
            if i > len(checkpoints):
                checkpoints.append(make_checkpoint(i, name, used))
            else:
                checkpoints[i - 1]["name"] = name  # rename only, keep token and code
        data["checkpoints"] = checkpoints
        save_json(DATA, data)
        print(f"data.json now has {len(checkpoints)} checkpoint(s).")
    else:
        checkpoints = existing
        if not checkpoints:
            sys.exit("No checkpoints in data.json yet. Run without --pages first.")

    write_pages(checkpoints, base_url, admin_phone)
    print("Keep qr_pages/ and data.json private: they contain the secret tokens.")


if __name__ == "__main__":
    main()
