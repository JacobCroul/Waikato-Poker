"""Set (or change) the admin panel password.

Usage:
    python set_admin.py

Stores only a hash of the password in config.json as "admin_hash".
"""
import getpass
import sys

import store


def main():
    pw = getpass.getpass("New admin password (min 8 characters): ")
    if len(pw) < 8:
        sys.exit("Too short. Nothing changed.")
    if pw != getpass.getpass("Repeat it: "):
        sys.exit("Passwords did not match. Nothing changed.")
    with store.LOCK:
        config = store.read_json(store.CONFIG)
        config["admin_hash"] = store.hash_pin(pw)
        store.write_json(store.CONFIG, config)
    print("Admin password saved to config.json. Restart is not needed.")


if __name__ == "__main__":
    main()
