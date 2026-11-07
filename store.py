"""Shared helpers for main.py and the admin scripts: JSON files and PIN hashing."""
import hashlib
import hmac
import json
import os
import secrets
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.json")
DATA = os.path.join(BASE, "data.json")

# Hold this around any read, change, write of a JSON file so two requests
# can't overwrite each other's changes (only protects within one process).
LOCK = threading.RLock()


def read_json(path):
    """Load JSON, treating a missing or empty file as {}."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    return json.loads(text) if text else {}


def write_json(path, obj):
    """Write atomically so a crash can't leave a half written file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def hash_pin(pin):
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${digest}"


def verify_pin(pin, stored):
    try:
        salt, digest = stored.split("$", 1)
    except (ValueError, AttributeError):
        return False
    check = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 200_000).hex()
    return hmac.compare_digest(check, digest)


# Used so an unknown boat ID takes as long to reject as a wrong PIN.
DUMMY_HASH = hash_pin("not-a-real-pin")
