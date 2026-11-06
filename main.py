import html
import secrets
import time
from urllib.parse import quote

import fastapi
import uvicorn
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

import draw
import store

SESSION_DAYS = 7   # how long a login lasts, survives refreshes and closing the browser
MAX_FAILS = 5      # wrong PINs before a boat is locked out briefly
LOCK_SECS = 60


def load_config():
    """Load the configuration from the config.json file."""
    return store.read_json(store.CONFIG)


def load_data():
    """Load the data from the data.json file."""
    return store.read_json(store.DATA)


def get_secret():
    """Signing key for login cookies. Saved in config.json so logins survive a server restart."""
    with store.LOCK:
        config = load_config()
        if not config.get("secret_key"):
            config["secret_key"] = secrets.token_hex(32)
            store.write_json(store.CONFIG, config)
        return config["secret_key"]


app = fastapi.FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=get_secret(),
    max_age=SESSION_DAYS * 86400,
    same_site="lax",
    https_only=bool(load_config().get("https_only", False)),  # set true in config if served over HTTPS
)

# ---------------------------------------------------------------- helpers

FAILS = {}  # boat id -> (failed count, locked until timestamp)


def locked_for(key):
    count, until = FAILS.get(key, (0, 0))
    left = until - time.time()
    if until and left <= 0:
        FAILS.pop(key, None)
        return 0
    return int(left) + 1 if left > 0 else 0


def record_fail(key):
    count, _ = FAILS.get(key, (0, 0))
    count += 1
    FAILS[key] = (count, time.time() + LOCK_SECS if count >= MAX_FAILS else 0)


def find_boat(data, boat_id):
    for boat in data.get("boats", []):
        if boat.get("id") == boat_id:
            return boat
    return None


def current_boat(request):
    boat_id = request.session.get("boat_id")
    return find_boat(load_data(), boat_id) if boat_id else None


def safe_next(url):
    """Only allow redirects to paths on this site."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return "/"


CSS = """
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;margin:0;padding:20px;background:#f4f6f8;color:#111}
main{max-width:420px;margin:0 auto}
h1{font-size:1.6rem;margin:.2em 0 .6em}
.card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px #0002}
label{display:block;margin:14px 0 6px;font-weight:600}
input{width:100%;font-size:18px;padding:12px;border:1px solid #bbb;border-radius:8px}
button{width:100%;font-size:18px;padding:14px;margin-top:18px;border:0;border-radius:8px;background:#0b5fff;color:#fff;font-weight:600}
button.plain{background:#e3e6ea;color:#111}
.err{background:#fde8e8;color:#9b1c1c;padding:10px 12px;border-radius:8px;margin-bottom:8px}
.muted{color:#666;font-size:.9rem}
ul{padding-left:20px}
.playing{display:inline-block;min-width:3.2em;text-align:center;font-weight:700;font-size:1.1em;background:#fff;border:1px solid #bbb;border-radius:6px;padding:2px 8px;color:#111}
.playing.red{color:#c62828}
.bigcard{display:block;width:150px;margin:18px auto;text-align:center;font-weight:700;font-size:56px;background:#fff;border:2px solid #333;border-radius:14px;padding:26px 0;color:#111}
.bigcard.red{color:#c62828}
"""


def page(title, body, status=200):
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{body}</main></body></html>",
        status_code=status,
    )


def card_html(card, big=False):
    cls = "bigcard" if big else "playing"
    red = " red" if draw.is_red(card) else ""
    return f"<span class='{cls}{red}'>{html.escape(draw.pretty(card))}</span>"


def login_page(error="", next_url="/", boat_id="", status=200):
    err = f"<div class='err'>{html.escape(error)}</div>" if error else ""
    body = f"""
<h1>Poker Run</h1>
<div class="card">
  {err}
  <form method="post" action="/login">
    <input type="hidden" name="next" value="{html.escape(next_url, quote=True)}">
    <label for="boat_id">Boat ID</label>
    <input id="boat_id" name="boat_id" value="{html.escape(boat_id, quote=True)}"
           autocomplete="username" autocapitalize="characters" autocorrect="off" required>
    <label for="pin">PIN</label>
    <input id="pin" name="pin" type="password" inputmode="numeric"
           autocomplete="current-password" required>
    <button type="submit">Log in</button>
  </form>
  <p class="muted">Your boat ID and PIN are on your registration slip.</p>
</div>"""
    return page("Log in", body, status)


# ---------------------------------------------------------------- routes

@app.get("/login")
def login_form(request: Request, next: str = "/"):
    if current_boat(request):
        return RedirectResponse(safe_next(next), status_code=303)
    return login_page(next_url=safe_next(next))


@app.post("/login")
def login(request: Request, boat_id: str = Form(""), pin: str = Form(""), next: str = Form("/")):
    next_url = safe_next(next)
    bid = boat_id.strip().upper()

    wait = locked_for(bid)
    if wait:
        return login_page(f"Too many tries. Wait {wait} seconds and try again.", next_url, bid, 429)

    boat = find_boat(load_data(), bid)
    ok = store.verify_pin(pin.strip(), boat["pin_hash"] if boat else store.DUMMY_HASH)
    if not (boat and ok):
        record_fail(bid)
        return login_page("Wrong boat ID or PIN.", next_url, bid, 401)

    FAILS.pop(bid, None)
    request.session.clear()
    request.session["boat_id"] = boat["id"]
    return RedirectResponse(next_url, status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/")
def home(request: Request):
    boat = current_boat(request)
    if not boat:
        return RedirectResponse("/login", status_code=303)

    data = load_data()
    names = {cp["id"]: cp["name"] for cp in data.get("checkpoints", [])}
    hand = data.get("hands", {}).get(boat["id"], [])
    if hand:
        rows = "".join(
            f"<li>{html.escape(str(names.get(d.get('checkpoint'), '?')))}: "
            f"{card_html(d.get('card', ''))}</li>"
            for d in hand
        )
        hand_html = f"<ul>{rows}</ul>"
    else:
        hand_html = "<p class='muted'>No cards yet. Scan a checkpoint QR code to draw one.</p>"

    body = f"""
<h1>{html.escape(boat['name'])}</h1>
<div class="card">
  <p class="muted">Boat {html.escape(boat['id'])}</p>
  <h2>Your hand</h2>
  {hand_html}
  <form method="post" action="/logout"><button class="plain" type="submit">Log out</button></form>
</div>"""
    return page(boat["name"], body)


def find_checkpoint(token):
    for cp in load_data().get("checkpoints", []):
        if secrets.compare_digest(str(cp.get("token", "")).encode(), token.encode()):
            return cp
    return None


def card_result_page(checkpoint, boat, result, status=200):
    """Shows the boat's card for this checkpoint, or why it can't draw."""
    if result["ok"]:
        note = "Here's your card." if result["new"] else "You've already drawn at this checkpoint. This is your card."
        inner = f"<p>{note}</p>{card_html(result['card'], big=True)}"
    else:
        inner = f"<div class='err'>{html.escape(result['message'])}</div>"
    body = f"""
<h1>{html.escape(checkpoint['name'])}</h1>
<div class="card">
  <p class="muted">{html.escape(boat['name'])}</p>
  {inner}
  <a href="/">See my whole hand</a>
</div>"""
    return page(checkpoint["name"], body, status)


@app.get("/s/{token}")
def scan(request: Request, token: str):
    """Landing page for a checkpoint QR. Does NOT draw by itself (so a link preview or
    accidental reload can't use up a draw); the boat presses the button to draw."""
    boat = current_boat(request)
    if not boat:
        return RedirectResponse(f"/login?next={quote(f'/s/{token}')}", status_code=303)

    checkpoint = find_checkpoint(token)
    if not checkpoint:
        return page("Unknown code", "<h1>Unknown code</h1><div class='card'><p>That QR code isn't recognised.</p></div>", 404)

    # Already drawn here? Show the card straight away.
    for d in load_data().get("hands", {}).get(boat["id"], []):
        if d.get("checkpoint") == checkpoint["id"]:
            return card_result_page(checkpoint, boat, {"ok": True, "card": d["card"], "new": False})

    is_open, _, message = draw.event_state(load_config())
    if not is_open:
        return card_result_page(checkpoint, boat, {"ok": False, "message": message})

    body = f"""
<h1>{html.escape(checkpoint['name'])}</h1>
<div class="card">
  <p>Scanned as <strong>{html.escape(boat['name'])}</strong>.</p>
  <form method="post" action="/s/{html.escape(token, quote=True)}/draw">
    <button type="submit">Draw my card</button>
  </form>
  <p class="muted">One card per checkpoint.</p>
</div>"""
    return page(checkpoint["name"], body)


@app.post("/s/{token}/draw")
def scan_draw(request: Request, token: str):
    boat = current_boat(request)
    if not boat:
        return RedirectResponse(f"/login?next={quote(f'/s/{token}')}", status_code=303)

    checkpoint = find_checkpoint(token)
    if not checkpoint:
        return page("Unknown code", "<h1>Unknown code</h1><div class='card'><p>That QR code isn't recognised.</p></div>", 404)

    result = draw.draw_card(boat["id"], checkpoint["id"], method="scan")
    return card_result_page(checkpoint, boat, result, 200 if result["ok"] else 403)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
