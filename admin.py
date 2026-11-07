"""Admin panel, mounted by main.py at /admin.

- Separate admin password (hash in config.json as "admin_hash", set with set_admin.py).
- Event open/close switch (the hard lockout in config.json -> event.status).
- Per checkpoint enable/disable switch.
- Manual roll: boat ID + the 5 character code printed under a QR. Goes through
  draw.draw_card, so it follows exactly the same rules as a normal scan.
- Boats overview (with PIN reset) and a log of every draw.
"""
import html
import secrets
import time
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import draw
import store

router = APIRouter(prefix="/admin")

ADMIN_HOURS = 12     # admin must log in again after this long
MAX_FAILS = 5        # wrong admin passwords before a short lockout
LOCK_SECS = 60
FAIL = {"count": 0, "until": 0.0}

MESSAGES = {
    "opened": "Event opened.",
    "closed": "Event closed. Nobody can draw new cards until you open it again.",
    "cp_on": "Checkpoint enabled.",
    "cp_off": "Checkpoint disabled.",
}

RANK_NAMES = {"A": "Ace", "T": "10", "J": "Jack", "Q": "Queen", "K": "King"}
SUIT_NAMES = {"S": "Spades", "H": "Hearts", "D": "Diamonds", "C": "Clubs"}

CSS = """
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#f4f6f8;color:#111}
main{max-width:900px;margin:0 auto}
h1{font-size:1.5rem;margin:.2em 0 .4em}
h2{font-size:1.15rem;margin:0 0 .6em}
.card{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 4px #0002;margin-bottom:16px}
.top{display:flex;justify-content:space-between;align-items:center;gap:8px}
label{display:block;margin:10px 0 4px;font-weight:600}
input[type=text],input[type=password]{width:100%;font-size:18px;padding:10px;border:1px solid #bbb;border-radius:8px}
button{font-size:16px;padding:10px 14px;border:0;border-radius:8px;background:#0b5fff;color:#fff;font-weight:600}
button.full{width:100%;margin-top:12px;font-size:18px;padding:14px}
button.plain{background:#e3e6ea;color:#111}
button.warn{background:#c62828}
button.small{font-size:14px;padding:6px 10px}
.err{background:#fde8e8;color:#9b1c1c;padding:10px 12px;border-radius:8px;margin-bottom:8px}
.ok{background:#e6f4ea;color:#1e6b34;padding:10px 12px;border-radius:8px;margin-bottom:12px}
.muted{color:#666;font-size:.9rem}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.95rem}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #e3e6ea;white-space:nowrap}
.mono{font-family:ui-monospace,Consolas,monospace;font-weight:700;letter-spacing:.1em}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-weight:700;font-size:.85rem}
.pill.on{background:#e6f4ea;color:#1e6b34}
.pill.off{background:#fde8e8;color:#9b1c1c}
.playing{display:inline-block;min-width:2.8em;text-align:center;font-weight:700;background:#fff;border:1px solid #bbb;border-radius:6px;padding:1px 6px;margin-right:3px;color:#111}
.playing.red{color:#c62828}
.bigcard{display:block;width:150px;margin:14px auto;text-align:center;font-weight:700;font-size:56px;background:#fff;border:2px solid #333;border-radius:14px;padding:26px 0;color:#111}
.bigcard.red{color:#c62828}
form.inline{display:inline}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def page(title, body, status=200):
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex'>"
        f"<title>{esc(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{body}</main></body></html>",
        status_code=status,
    )


def card_html(card, big=False):
    cls = "bigcard" if big else "playing"
    red = " red" if draw.is_red(card) else ""
    return f"<span class='{cls}{red}'>{esc(draw.pretty(card))}</span>"


def spoken(card):
    """'TH' -> '10 of Hearts', handy for texting back."""
    if not card or len(card) < 2:
        return str(card or "")
    return f"{RANK_NAMES.get(card[0], card[0])} of {SUIT_NAMES.get(card[1], card[1])}"


def fmt_time(iso):
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d %b %H:%M:%S")
    except (TypeError, ValueError):
        return iso or ""


# ---------------------------------------------------------------- auth

def is_admin(request):
    ts = request.session.get("admin_at")
    return bool(ts) and (time.time() - ts) < ADMIN_HOURS * 3600


def need_admin(request):
    """Returns a redirect if not logged in as admin, otherwise None."""
    if is_admin(request):
        return None
    return RedirectResponse("/admin/login", status_code=303)


def login_page(error="", status=200):
    err = f"<div class='err'>{esc(error)}</div>" if error else ""
    body = f"""
<h1>Admin login</h1>
<div class="card">
  {err}
  <form method="post" action="/admin/login">
    <label for="password">Admin password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button class="full" type="submit">Log in</button>
  </form>
</div>"""
    return page("Admin login", body, status)


@router.get("/login")
def login_form(request: Request):
    if is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    if not store.read_json(store.CONFIG).get("admin_hash"):
        return login_page("No admin password is set yet. Run: python set_admin.py", 503)
    return login_page()


@router.post("/login")
def login(request: Request, password: str = Form("")):
    stored = store.read_json(store.CONFIG).get("admin_hash")
    if not stored:
        return login_page("No admin password is set yet. Run: python set_admin.py", 503)

    wait = int(FAIL["until"] - time.time()) + 1
    if FAIL["until"] and wait > 0:
        return login_page(f"Too many tries. Wait {wait} seconds.", 429)
    if FAIL["until"]:
        FAIL.update(count=0, until=0.0)

    if not store.verify_pin(password, stored):
        FAIL["count"] += 1
        if FAIL["count"] >= MAX_FAILS:
            FAIL["until"] = time.time() + LOCK_SECS
        return login_page("Wrong password.", 401)

    FAIL.update(count=0, until=0.0)
    request.session["admin_at"] = time.time()
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.pop("admin_at", None)
    return RedirectResponse("/admin/login", status_code=303)


# ---------------------------------------------------------------- data helpers

def all_draws(data):
    names = {cp["id"]: cp["name"] for cp in data.get("checkpoints", [])}
    boats = {b["id"]: b["name"] for b in data.get("boats", [])}
    rows = []
    for boat_id, hand in data.get("hands", {}).items():
        for d in hand:
            rows.append({
                "time": d.get("time", ""),
                "boat_id": boat_id,
                "boat": boats.get(boat_id, "?"),
                "checkpoint": names.get(d.get("checkpoint"), "?"),
                "card": d.get("card", ""),
                "method": d.get("method", ""),
            })
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows


def draws_table(rows):
    if not rows:
        return "<p class='muted'>No draws yet.</p>"
    body = "".join(
        f"<tr><td>{esc(fmt_time(r['time']))}</td><td>{esc(r['boat_id'])} {esc(r['boat'])}</td>"
        f"<td>{esc(r['checkpoint'])}</td><td>{card_html(r['card'])}</td>"
        f"<td>{'<strong>manual</strong>' if r['method'] == 'manual' else esc(r['method'])}</td></tr>"
        for r in rows
    )
    return (
        "<div class='scroll'><table><tr><th>Time</th><th>Boat</th><th>Checkpoint</th>"
        f"<th>Card</th><th>How</th></tr>{body}</table></div>"
    )


# ---------------------------------------------------------------- dashboard

@router.get("")
def dashboard(request: Request, m: str = ""):
    gate = need_admin(request)
    if gate:
        return gate

    config = store.read_json(store.CONFIG)
    data = store.read_json(store.DATA)
    checkpoints = data.get("checkpoints", [])
    boats = data.get("boats", [])
    hands = data.get("hands", {})

    flash = f"<div class='ok'>{esc(MESSAGES[m])}</div>" if m in MESSAGES else ""

    # Event card
    is_open, _, why = draw.event_state(config)
    switch = str((config.get("event") or {}).get("status", "open")).lower()
    state_pill = "<span class='pill on'>OPEN</span>" if is_open else "<span class='pill off'>CLOSED</span>"
    why_txt = f" <span class='muted'>{esc(why)}</span>" if why else ""
    if switch == "closed":
        event_btn = "<button class='small' name='action' value='open'>Open event</button>"
    else:
        event_btn = "<button class='small warn' name='action' value='close'>Close event (lockout)</button>"
    event_card = f"""
<div class="card">
  <h2>Event</h2>
  <p>{state_pill}{why_txt}</p>
  <form method="post" action="/admin/event">{event_btn}</form>
  <p class="muted">Closing stops all new draws. Boats can still see cards they already have.</p>
</div>"""

    # Manual roll card
    roll_card = """
<div class="card">
  <h2>Manual roll</h2>
  <form method="post" action="/admin/roll" autocomplete="off">
    <label for="boat_id">Boat ID</label>
    <input id="boat_id" type="text" name="boat_id" placeholder="B01" autocapitalize="characters" autocorrect="off" required>
    <label for="code">Checkpoint code (under the QR)</label>
    <input id="code" type="text" name="code" class="mono" placeholder="ABCDE" autocapitalize="characters" autocorrect="off" required>
    <button class="full" type="submit">Roll card</button>
  </form>
</div>"""

    # Checkpoints card
    counts = {cp["id"]: 0 for cp in checkpoints}
    for hand in hands.values():
        for d in hand:
            if d.get("checkpoint") in counts:
                counts[d["checkpoint"]] += 1
    cp_rows = ""
    for cp in checkpoints:
        on = cp.get("enabled", True)
        pill = "<span class='pill on'>on</span>" if on else "<span class='pill off'>off</span>"
        label = "Disable" if on else "Enable"
        btn_cls = "small warn" if on else "small"
        cp_rows += (
            f"<tr><td>{esc(cp['id'])}</td><td>{esc(cp['name'])}</td>"
            f"<td class='mono'>{esc(cp.get('short_code', ''))}</td>"
            f"<td>{counts.get(cp['id'], 0)}</td><td>{pill}</td>"
            f"<td><form class='inline' method='post' action='/admin/checkpoint/{esc(cp['id'])}/toggle'>"
            f"<button class='{btn_cls}'>{label}</button></form></td></tr>"
        )
    cp_card = f"""
<div class="card">
  <h2>Checkpoints</h2>
  <div class="scroll"><table>
    <tr><th>#</th><th>Name</th><th>Code</th><th>Draws</th><th>Status</th><th></th></tr>
    {cp_rows}
  </table></div>
</div>"""

    # Boats card
    boat_rows = ""
    for b in boats:
        hand = hands.get(b["id"], [])
        cards = "".join(card_html(d.get("card", "")) for d in hand) or "<span class='muted'>none</span>"
        boat_rows += (
            f"<tr><td>{esc(b['id'])}</td><td>{esc(b['name'])}</td><td>{esc(b.get('phone', ''))}</td>"
            f"<td>{len(hand)}/{len(checkpoints)}</td><td>{cards}</td>"
            f"<td><form class='inline' method='post' action='/admin/boat/{esc(b['id'])}/reset-pin' "
            "onsubmit=\"return confirm('Reset this boat PIN? The old PIN stops working.')\">"
            f"<button class='small plain'>Reset PIN</button></form></td></tr>"
        )
    boats_body = (
        f"<div class='scroll'><table><tr><th>ID</th><th>Name</th><th>Phone</th><th>Cards</th>"
        f"<th>Hand</th><th></th></tr>{boat_rows}</table></div>"
        if boats else "<p class='muted'>No boats registered. Use add_boat.py.</p>"
    )
    boats_card = f"<div class='card'><h2>Boats ({len(boats)})</h2>{boats_body}</div>"

    # Recent draws
    recent = all_draws(data)[:15]
    draws_card = (
        f"<div class='card'><h2>Recent draws</h2>{draws_table(recent)}"
        "<p><a href='/admin/draws'>All draws</a></p></div>"
    )

    body = f"""
<div class="top"><h1>Admin</h1>
  <form method="post" action="/admin/logout"><button class="plain small">Log out</button></form></div>
{flash}
{event_card}
{roll_card}
{cp_card}
{boats_card}
{draws_card}"""
    return page("Admin", body)


# ---------------------------------------------------------------- actions

@router.post("/event")
def set_event(request: Request, action: str = Form("")):
    gate = need_admin(request)
    if gate:
        return gate
    with store.LOCK:
        config = store.read_json(store.CONFIG)
        event = config.get("event")
        if not isinstance(event, dict):
            event = config["event"] = {}
        event["status"] = "closed" if action == "close" else "open"
        store.write_json(store.CONFIG, config)
    return RedirectResponse(f"/admin?m={'closed' if action == 'close' else 'opened'}", status_code=303)


@router.post("/checkpoint/{cp_id}/toggle")
def toggle_checkpoint(request: Request, cp_id: int):
    gate = need_admin(request)
    if gate:
        return gate
    message = ""
    with store.LOCK:
        data = store.read_json(store.DATA)
        for cp in data.get("checkpoints", []):
            if cp.get("id") == cp_id:
                cp["enabled"] = not cp.get("enabled", True)
                message = "cp_on" if cp["enabled"] else "cp_off"
        if message:
            store.write_json(store.DATA, data)
    return RedirectResponse(f"/admin?m={message}" if message else "/admin", status_code=303)


@router.post("/roll")
def roll(request: Request, boat_id: str = Form(""), code: str = Form("")):
    gate = need_admin(request)
    if gate:
        return gate

    bid = boat_id.strip().upper()
    norm = "".join(ch for ch in code.upper() if ch.isalnum())  # ignore spaces and dashes
    data = store.read_json(store.DATA)
    boat = next((b for b in data.get("boats", []) if b.get("id") == bid), None)
    cp = next((c for c in data.get("checkpoints", []) if str(c.get("short_code", "")).upper() == norm), None)

    back = "<p><a href='/admin'>Back to admin</a></p>"
    if not boat:
        return page("Manual roll", f"<h1>Manual roll</h1><div class='card'><div class='err'>No boat with ID {esc(bid)}.</div>{back}</div>", 404)
    if not cp:
        return page("Manual roll", f"<h1>Manual roll</h1><div class='card'><div class='err'>No checkpoint has the code {esc(norm)}.</div>{back}</div>", 404)

    result = draw.draw_card(boat["id"], cp["id"], method="manual")
    who = f"<p><strong>{esc(boat['name'])}</strong> ({esc(boat['id'])}) {esc(boat.get('phone', ''))}<br>{esc(cp['name'])}</p>"
    if result["ok"]:
        note = "New card rolled." if result["new"] else "This boat had already drawn here. Same card as before."
        reply = f"Your card at {cp['name']} is the {spoken(result['card'])}"
        inner = (
            f"{who}<p>{esc(note)}</p>{card_html(result['card'], big=True)}"
            f"<p class='muted'>Text back: {esc(reply)}</p>"
        )
        status = 200
    else:
        inner = f"{who}<div class='err'>{esc(result['message'])}</div>"
        status = 403
    return page("Manual roll", f"<h1>Manual roll</h1><div class='card'>{inner}{back}</div>", status)


@router.post("/boat/{boat_id}/reset-pin")
def reset_pin(request: Request, boat_id: str):
    gate = need_admin(request)
    if gate:
        return gate
    with store.LOCK:
        data = store.read_json(store.DATA)
        boat = next((b for b in data.get("boats", []) if b.get("id") == boat_id), None)
        if not boat:
            return page("Reset PIN", "<h1>Reset PIN</h1><div class='card'><div class='err'>Unknown boat.</div><p><a href='/admin'>Back to admin</a></p></div>", 404)
        pin = f"{secrets.randbelow(10**6):06d}"
        boat["pin_hash"] = store.hash_pin(pin)
        store.write_json(store.DATA, data)
    body = f"""
<h1>New PIN</h1>
<div class="card">
  <p><strong>{esc(boat['name'])}</strong> ({esc(boat['id'])})</p>
  <p class="mono" style="font-size:2.4rem">{esc(pin)}</p>
  <p class="muted">Shown once. Only a hash is stored, so write it down or send it now.</p>
  <p><a href="/admin">Back to admin</a></p>
</div>"""
    return page("New PIN", body)


@router.get("/draws")
def draws_page(request: Request):
    gate = need_admin(request)
    if gate:
        return gate
    rows = all_draws(store.read_json(store.DATA))
    body = (
        "<div class='top'><h1>All draws</h1><a href='/admin'>Back</a></div>"
        f"<div class='card'><p class='muted'>{len(rows)} draw(s), newest first.</p>{draws_table(rows)}</div>"
    )
    return page("All draws", body)
