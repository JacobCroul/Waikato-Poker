"""Card drawing. The ONE place cards are handed out, so a normal scan and the
admin manual roll (later) go through exactly the same checks.

draw_card(boat_id, checkpoint_id, method) returns a dict:
    {"ok": True,  "card": "AS", "new": True/False, "time": "..."}
    {"ok": False, "error": "closed" | "not_started" | "ended" | "disabled" |
                           "no_boat" | "no_checkpoint" | "deck_empty", "message": "..."}
If the boat already drew at that checkpoint, the existing card is returned
with new=False (never a second draw).

Event window (optional, in config.json). No "event" key = always open:
    "event": {"status": "open" | "closed", "start": "2026-12-05T09:00", "end": "2026-12-05T16:00"}
    status "closed" is a hard lockout. start/end are server local time unless they include an offset.
"""
import secrets
from datetime import datetime, timezone

import store

RANKS = "A23456789TJQK"
SUITS = "SHDC"
FULL_DECK = [r + s for s in SUITS for r in RANKS]  # 52 cards, e.g. "AS", "TH"

SUIT_SYMBOL = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}


def pretty(card):
    """'TH' -> '10♥' for display."""
    if not card or len(card) < 2:
        return str(card or "")
    rank = "10" if card[0] == "T" else card[0]
    return rank + SUIT_SYMBOL.get(card[1], card[1])


def is_red(card):
    return bool(card) and card[-1] in "HD"


def _parse(text):
    dt = datetime.fromisoformat(text)
    return dt.astimezone() if dt.tzinfo is None else dt  # naive = server local time


def event_state(config, now=None):
    """Returns (is_open, error_code, message)."""
    event = config.get("event") or {}
    now = now or datetime.now(timezone.utc)
    if str(event.get("status", "open")).lower() == "closed":
        return False, "closed", "The event isn't open right now."
    if event.get("start") and now < _parse(event["start"]):
        return False, "not_started", "The event hasn't started yet."
    if event.get("end") and now > _parse(event["end"]):
        return False, "ended", "The event has finished."
    return True, "", ""


def _fail(code, message):
    return {"ok": False, "error": code, "message": message}


def draw_card(boat_id, checkpoint_id, method="scan"):
    with store.LOCK:  # read, change, write must not interleave with another draw
        config = store.read_json(store.CONFIG)
        data = store.read_json(store.DATA)

        if not any(b.get("id") == boat_id for b in data.get("boats", [])):
            return _fail("no_boat", "Unknown boat.")
        cp = next((c for c in data.get("checkpoints", []) if c.get("id") == checkpoint_id), None)
        if not cp:
            return _fail("no_checkpoint", "Unknown checkpoint.")

        hand = data.setdefault("hands", {}).setdefault(boat_id, [])

        # Already drew here: show the same card (also covers refresh / double scan).
        # Checked before the event window so a boat can still look at its own card.
        for d in hand:
            if d.get("checkpoint") == checkpoint_id:
                return {"ok": True, "card": d["card"], "new": False, "time": d.get("time", "")}

        is_open, code, msg = event_state(config)
        if not is_open:
            return _fail(code, msg)
        if not cp.get("enabled", True):
            return _fail("disabled", "This checkpoint isn't active right now.")

        held = {d["card"] for d in hand}  # never give a hand a card it already holds

        if config.get("infinite_cards", True):
            pool = [c for c in FULL_DECK if c not in held]
        else:
            # One fresh deck per checkpoint, cards flagged unavailable once given out.
            deck = data.setdefault("decks", {}).setdefault(
                str(checkpoint_id), [{"card": c, "available": True} for c in FULL_DECK]
            )
            pool = [e["card"] for e in deck if e.get("available", True) and e["card"] not in held]

        if not pool:
            return _fail("deck_empty", "No cards left to draw here. Please contact the organiser.")

        card = secrets.choice(pool)  # server side, cryptographically random

        if not config.get("infinite_cards", True):
            for e in data["decks"][str(checkpoint_id)]:
                if e["card"] == card:
                    e["available"] = False

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        hand.append({"checkpoint": checkpoint_id, "card": card, "time": now, "method": method})
        store.write_json(store.DATA, data)
        return {"ok": True, "card": card, "new": True, "time": now}
