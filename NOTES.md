# Poker Run Notes

Living notes for the digital poker run project. Updated as we go, newest log entries at the bottom.

## Concept
- Digital version of a boating poker run.
- Checkpoints each have a QR code. Scanning opens a webpage.
- Users have their own login (one per boat).
- Each QR has a hidden ID (long random token) in the background to prevent cheating.
- All cards are assigned to the user account.
- The webapp picks a random card from a list (server side only).
- Admin can view all draws to verify.
- Cards in the list can be set to false when given to a user.
- QRs stay hidden until the event starts. A lockout can be set server side.
- Prizes: undecided (fun vs prizes). Build the audit log either way.
- Fallback if a boat cannot scan: each checkpoint also has a 5 character code printed under its QR (cryptographically random). The boat texts the admin (IT guy) their boat ID + the code (or a photo of the code if service is poor), and the admin rolls the card for them from the admin page.

## Decisions so far
- Stack: Python + FastAPI (see main.py).
- Storage: keep it simple, store what we can in JSON files for now.
  - config.json: static settings (event start/end, admin credentials, etc.)
  - data.json: boats, draws, checkpoints, decks
- Server side is the source of truth. Client never picks a card.
- One hand per boat per run (decided). The boat is the UID, so one account = one boat = one hand.
- Registration is always ahead of time (decided). Admin pre-registers boats, no sign up on the day, no on-the-water account creation.
- A standard 52 card deck will not scale if more people join, so add an `infinite_cards` bool in config.json (idea, leaning yes).
  - true: every draw picks from the full 52, ignoring the per card `available` flag. Same odds for everyone, no depletion.
  - false: finite decks, cards flagged unavailable once given out.
  - Either way, never give a hand a card it already holds (no duplicate cards inside one hand).

## Open decisions
- Boat login credential: boat ID + short PIN, or a magic link / QR on a registration slip. (Registration itself is settled, see above.)
- Number of checkpoints (5 is common) and whether they are attended.
- Deck model: confirm infinite_cards true as the default. If finite, one fresh deck per checkpoint (each supports up to 52 players, one card each), not one shared deck.
  - Finite downside: later players get worse odds as cards deplete, and the last draws can get restricted.
- Reveal timing: show card immediately or hide until the finish.
- Swaps allowed or not.
- Backup admin for the text line, so one person with a dead phone or no signal is not a single point of failure.
- Tie break rule (e.g. high card draw).

## Planned rules and lockouts
- One draw per hand per checkpoint (enforce in code since JSON has no constraints).
- Event open/close window.
- Per checkpoint enable/disable switch and token rotation.
- Optional: minimum travel time between checkpoints.
- Admin manual roll (the text-in fallback above, also for failed scans or dead phones). It must run through the same server checks as a normal scan: event window, checkpoint enabled, one draw per boat per checkpoint, no duplicate card in the hand. If the boat already drew at that checkpoint, show the existing card.
- Short codes: separate secret from the QR token, case insensitive, no look alike characters (no 0/O, 1/I/L), unique per checkpoint, and only usable via the admin tool (not a public endpoint).
- Log every draw with a method field (scan or manual) so the admin view shows which were rolled by hand.
- Admin page should work on a phone, since the admin may be on the water.
- Optional: store each boat's phone number at registration so the admin can check a text came from the right boat.

## Rough JSON shape (draft, not final)
```
config.json
{ "event": {"start": "", "end": "", "status": "closed"},
  "infinite_cards": true,
  "admin": {"user": "", "password_hash": ""} }

data.json
{ "boats": [{"id": "", "name": "", "pin_hash": "", "phone": ""}],
  "checkpoints": [{"id": 1, "name": "", "token": "", "short_code": "", "enabled": true}],
  "hands": {"<boat_id>": [{"checkpoint": 1, "card": "AS", "time": "", "method": "scan"}]},
  "decks": {"1": [{"card": "AS", "available": true}]} }
```

## Known risks
- Empty config.json and data.json crash the loaders in main.py (json.load on 0 bytes). Put {} in them.
- JSON file storage has race conditions if two scans land at the same moment. Use a lock around read/modify/write at minimum. Consider SQLite later if it becomes a problem.
- Static QR codes can be photographed and shared once visible. Rotating tokens or geolocation would fix this if prizes are involved.
- Mobile signal on the water is patchy. Test at checkpoint locations and keep a paper backup plan.
- Legal: if there is a paid entry and a prize decided by chance, check local gambling rules.

## Status
- main.py: FastAPI app. Routes: GET/POST /login, POST /logout, GET / (boat home, shows hand), GET /s/{token} (scan landing, stub: needs login, shows checkpoint name, NO card draw yet). Login redirects back to the scanned URL via ?next=. /docs is disabled.
  - Login: boat ID + 6 digit PIN. Session is a signed cookie lasting 7 days (survives refresh, closing the browser and server restarts). Signing key auto-created in config.json as secret_key.
  - Throttle: 5 wrong PINs locks that boat ID for 60 seconds (in memory).
  - Set "https_only": true in config.json if served over HTTPS.
- store.py: shared JSON read/write (atomic), lock, PIN hashing (PBKDF2).
- add_boat.py: `python add_boat.py "Name" [phone]` adds a boat (IDs B01, B02, ...) and prints the PIN once; `--reset B01` new PIN; `--list`. Run before the event only.
- requirements.txt: fastapi, uvicorn, itsdangerous, python-multipart, segno.
- Login flow tested in a sandbox (bad PIN, persistence, restart, redirect, throttle, logout). Not yet run on the real folder.
- README.md: one line description.
- config.json: base_url (placeholder, must be changed), admin_phone, infinite_cards, checkpoints (list of names). Event window not added yet.
- data.json: {} until generate_qr.py runs.
- draw.py: the ONE place cards are handed out (admin manual roll must call draw_card too). draw_card(boat_id, checkpoint_id, method) under store.LOCK: boat exists, already drew here returns the same card (new=False), event window, checkpoint enabled, no duplicate card in a hand, infinite_cards (full 52 each time) or finite (per checkpoint deck in data.json "decks", flagged unavailable when given), secrets.choice, logs {checkpoint, card, time (UTC), method}. Cards stored as rank+suit, T = ten (e.g. "TH"); shown as 10♥.
  - Event window in config.json is optional: "event": {"status": "open|closed", "start": "2026-12-05T09:00", "end": "..."}. No event key = always open (so testing works). Set status closed as a hard lockout before the event.
- main.py draw routes: GET /s/{token} shows a "Draw my card" button (or the existing card if already drawn, or the closed message). POST /s/{token}/draw does the draw. Reveal is immediate (default for now). Home page shows the hand with card faces.
- Draw logic tested in a sandbox (repeat draw, disabled checkpoint, closed/not started/ended, finite deck gives 52 unique cards then deck_empty, 40 concurrent draws). The routes themselves not yet run on the real folder.
- generate_qr.py: reads config, creates checkpoints (128 bit token + 5 char short_code) in data.json, builds printable A4 QR pages in qr_pages/. Needs `pip install segno`. Safe to re-run (keeps existing tokens), --pages rebuilds pages only, --force regenerates everything (invalidates printed QRs). Tested in a sandbox, not yet run on the real folder.

- admin.py (router mounted in main.py at /admin): separate admin password (hash in config.json as admin_hash, set with `python set_admin.py`), session expires after 12h, 5 wrong passwords locks login for 60s. Phone friendly. Dashboard: event open/close switch (sets config event.status, hard lockout), manual roll form (boat ID + 5 char code, tolerant of case/spaces/dashes, calls draw.draw_card with method manual, shows the card and a "text back" line), checkpoints table (code, draw count, enable/disable), boats table (hand, phone, reset PIN shown once), recent draws plus /admin/draws full log (manual draws in bold). All admin routes redirect to /admin/login when not logged in.
- Admin panel tested in a sandbox with a test harness (gating, roll incl. repeat/bad boat/bad code, closed event, disabled checkpoint, PIN reset, lockout, expiry, missing admin_hash). Not yet run on the real folder. To use: `python set_admin.py`, then restart main.py once (main.py now includes the admin router).

## Next steps
1. Settle the open decisions above.
2. Done: {} in data.json, starter config.json. Change base_url in config.json, then run generate_qr.py.
3. Done: boat registration (add_boat.py) and login. Next: install deps, run the server, test login from a phone.
3b. Done: draw wired up (see draw.py). Next: run the server and test a draw from a phone.
4. Done: admin panel (admin.py). Next: set admin password, run it, try a manual roll end to end.
5. Add hand ranking and leaderboard.
6. Dry run on land, then field test signal at checkpoints.

## Log
- Initial notes created. Project folder had 4 files (main.py, README.md, config.json, data.json), almost nothing in them.
- Decided: single hand per run. Added infinite_cards bool idea to handle more than a 52 card deck's worth of players.
- Decided: one hand per boat, the boat is the UID (account = boat). Draft JSON updated (boats replace users, hands keyed by boat id).
- Decided: registration is always ahead of time by the admin.
- Added fallback: 5 character code under each QR, boat texts it to the admin who rolls the card via the admin page.
- Wrote generate_qr.py (tokens, short codes, printable QR pages), starter config.json, {} in data.json.
- Wrote draw.py and the /s/{token} draw button + POST route. Reveal timing defaulted to immediate, event window optional.
- Wrote admin.py (event switch, manual roll, checkpoint toggles, boats and PIN reset, draw log) and set_admin.py; hooked into main.py with include_router.
- Wrote login system in main.py (persistent 7 day cookie session), store.py, add_boat.py, requirements.txt.
- Project moved to GitHub (Waikato-Poker folder) after an SMB issue. Repaired: restored store.py (was missing), removed bogus `store` line from requirements.txt (that is a different PyPI package), added .gitignore (config.json, data.json, qr_pages/ hold secrets). config.json and data.json are not in the repo: recreate config.json (base_url, checkpoints, secret_key is auto added) and re-run generate_qr.py and add_boat.py on the new machine.
