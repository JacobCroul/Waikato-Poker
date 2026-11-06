"""Register boats and reset PINs.

Usage:
    python add_boat.py "Boat name" [phone]   add a boat, prints its ID and PIN once
    python add_boat.py --reset B01           new PIN for an existing boat (lost slip)
    python add_boat.py --list                list registered boats

Do registration before the event. Don't run this while draws are being made,
because the server and this script both write data.json.
"""
import argparse
import secrets
import sys

import store


def new_pin():
    return f"{secrets.randbelow(10**6):06d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="boat name")
    ap.add_argument("phone", nargs="?", default="", help="contact phone number (optional)")
    ap.add_argument("--reset", metavar="ID", help="issue a new PIN for this boat ID")
    ap.add_argument("--list", action="store_true", help="list boats")
    args = ap.parse_args()

    with store.LOCK:
        data = store.read_json(store.DATA)
        boats = data.setdefault("boats", [])

        if args.list:
            for b in boats:
                print(f"{b['id']:<6}{b['name']}  {b.get('phone', '')}")
            print(f"{len(boats)} boat(s)")
            return

        if args.reset:
            boat = next((b for b in boats if b["id"] == args.reset.upper()), None)
            if not boat:
                sys.exit(f"No boat with ID {args.reset}.")
            pin = new_pin()
            boat["pin_hash"] = store.hash_pin(pin)
            store.write_json(store.DATA, data)
            print(f"{boat['id']} {boat['name']}: new PIN {pin}")
            return

        if not args.name:
            ap.print_help()
            sys.exit(1)

        numbers = [int(b["id"][1:]) for b in boats if b["id"][1:].isdigit()]
        boat_id = f"B{max(numbers, default=0) + 1:02d}"
        pin = new_pin()
        boats.append({
            "id": boat_id,
            "name": args.name,
            "pin_hash": store.hash_pin(pin),
            "phone": args.phone,
        })
        store.write_json(store.DATA, data)
        print(f"Added {args.name}")
        print(f"  Boat ID: {boat_id}")
        print(f"  PIN:     {pin}   (shown once, only a hash is stored)")


if __name__ == "__main__":
    main()
