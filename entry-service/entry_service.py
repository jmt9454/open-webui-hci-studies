"""
Entry service for the Open WebUI research embed.

Handles participant identity and hands each participant off to their own
chat in the forked Open WebUI instance. Talks to Open WebUI ONLY through its
own public REST API (never touches its internals / Python code directly):

  - POST /api/v1/auths/add     -- create a participant account (admin token)
  - POST /api/v1/auths/signin  -- sign in as that participant (email+password)
  - GET  /api/v1/chats/list    -- check whether the participant already has a chat

Design notes (verified against the actual v0.6.5 source, not just docs):

  - Open WebUI's frontend session is entirely localStorage-based (checked in
    src/routes/+layout.svelte). A plain Set-Cookie on a 302 redirect can NOT
    log a participant in -- localStorage can only be written by JS running on
    that origin. Instead we reuse Open WebUI's *existing* OAuth-callback
    handoff mechanism: the /auth page already reads a #token=<jwt> URL
    fragment on load, verifies it, sets localStorage.token, and navigates to
    a `redirect` query param (src/routes/auth/+page.svelte, checkOauthCallback
    / setSessionUser). We never had to add this -- it already ships.

  - We never hand-build a chat's message/history JSON. Pre-creating an
    "empty" chat and filling it in later does NOT work with this codebase:
    Chat.svelte's submitPrompt() decides whether to create-vs-update a chat
    purely by checking if zero messages exist yet in the browser's local
    `history` state (see the `if (messages.length === 0)` branch), not by
    whether the URL's chat id already exists server-side. Sending the first
    message into a pre-created empty chat would silently fork a second, real
    chat and orphan the one we tracked. Instead we redirect first-time
    participants to `/?models=<id>&q=<seed>` and let Open WebUI's own,
    already-shipped `q` param (in initNewChat(), Chat.svelte) auto-submit the
    seed message through the app's real, always-correct send path. The
    resulting chat is then discovered on return visits via /api/v1/chats/list
    rather than tracked in our own DB, since a chat-only participant (no
    sidebar, no new-chat button -- see the +layout.svelte / Chat.svelte edits)
    can never have more than one chat.

  - A browser that already has a stale localStorage.token (shared computer,
    staff testing the link, etc.) would otherwise short-circuit the handoff,
    since /auth's onMount redirects immediately if $user is already set,
    before it even looks at our #token= fragment. We serve a tiny HTML page
    that clears localStorage before navigating, so every /enter hit starts
    from a clean slate.

Two things you should still double-check against your own instance before
trusting this in production (see the guide's "verify" callouts):
  1. That WEBUI_AUTH_TRUSTED_EMAIL_HEADER is NOT set on your Open WebUI
     instance. If it is, /api/v1/auths/signin takes a completely different
     code path (trusted-header auth) and ignores the email/password this
     service sends.
  2. That ENABLE_API_KEYS is on and you've generated an admin API key
     (Admin Panel -> Settings -> Account -> API Keys) for
     OPEN_WEBUI_ADMIN_API_KEY below.
"""

import html
import os
import re
import secrets
import sqlite3
from urllib.parse import quote, urlencode

import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

app = FastAPI()

# ---- Configuration -- change via env vars, no code edits needed ----
PARTICIPANT_ID_PARAM = os.environ.get("PARTICIPANT_ID_PARAM", "pid")
PARTICIPANT_ID_REGEX = os.environ.get("PARTICIPANT_ID_REGEX", r"^R_[a-zA-Z0-9]{15,32}$")
PARTICIPANT_EMAIL_DOMAIN = os.environ.get("PARTICIPANT_EMAIL_DOMAIN", "participants.local")
DEFAULT_SEED_MESSAGE = os.environ.get("DEFAULT_SEED_MESSAGE", "")
DEFAULT_MODEL_ID = os.environ["DEFAULT_MODEL_ID"]  # e.g. "gpt-4o" -- see Part 4 to find yours
OPEN_WEBUI_BASE_URL = os.environ.get("OPEN_WEBUI_BASE_URL", "http://open-webui:8080")
OPEN_WEBUI_ADMIN_API_KEY = os.environ["OPEN_WEBUI_ADMIN_API_KEY"]  # required, no default
DB_PATH = os.environ.get("DB_PATH", "/data/participants.sqlite3")

ID_PATTERN = re.compile(PARTICIPANT_ID_REGEX)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS participants (
            external_id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return conn


def admin_headers():
    return {
        "Authorization": f"Bearer {OPEN_WEBUI_ADMIN_API_KEY}",
        "Content-Type": "application/json",
    }


def get_or_create_participant(conn, external_id: str):
    row = conn.execute(
        "SELECT email, password FROM participants WHERE external_id = ?",
        (external_id,),
    ).fetchone()
    if row is not None:
        return row  # (email, password)

    email = f"{external_id}@{PARTICIPANT_EMAIL_DOMAIN}"
    password = secrets.token_urlsafe(24)

    resp = requests.post(
        f"{OPEN_WEBUI_BASE_URL}/api/v1/auths/add",
        headers=admin_headers(),
        json={"name": external_id, "email": email, "password": password, "role": "user"},
        timeout=10,
    )
    resp.raise_for_status()

    conn.execute(
        "INSERT INTO participants (external_id, email, password) VALUES (?, ?, ?)",
        (external_id, email, password),
    )
    conn.commit()
    return (email, password)


def sign_in(email: str, password: str) -> str:
    resp = requests.post(
        f"{OPEN_WEBUI_BASE_URL}/api/v1/auths/signin",
        json={"email": email, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def find_existing_chat_id(token: str):
    """A chat-only participant has no sidebar / new-chat button, so they can
    never end up with more than one chat. If the list is non-empty, the
    first entry is *the* chat."""
    resp = requests.get(
        f"{OPEN_WEBUI_BASE_URL}/api/v1/chats/list",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    chats = resp.json()
    return chats[0]["id"] if chats else None


def clear_and_redirect(target_url: str) -> HTMLResponse:
    """Clear any stale localStorage session before handing off, then send the
    browser to `target_url` (kept out of the Location header / server logs
    isn't the goal here -- the token itself is only ever transmitted in a URL
    fragment, which browsers never send to any server)."""
    safe_url = html.escape(target_url, quote=True)
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body>
<script>
  try {{ localStorage.clear(); }} catch (e) {{}}
  window.location.replace("{safe_url}");
</script>
</body></html>"""
    return HTMLResponse(content=body)


@app.get("/enter")
def enter(request: Request):
    external_id = request.query_params.get(PARTICIPANT_ID_PARAM, "")
    seed_override = request.query_params.get("seed")

    if not ID_PATTERN.match(external_id):
        return PlainTextResponse("Missing or invalid participant ID.", status_code=400)

    conn = get_db()
    try:
        email, password = get_or_create_participant(conn, external_id)
    finally:
        conn.close()

    token = sign_in(email, password)
    chat_id = find_existing_chat_id(token)

    if chat_id:
        # Returning participant -- straight back to their one chat, no reseed.
        target = f"/c/{chat_id}?" + urlencode({"chatOnly": "true"})
    else:
        # First-ever visit -- land on a brand-new chat. Open WebUI's own
        # `q` param (initNewChat(), Chat.svelte) auto-submits it for us.
        seed_text = seed_override or DEFAULT_SEED_MESSAGE
        params = {"chatOnly": "true", "models": DEFAULT_MODEL_ID}
        if seed_text:
            params["q"] = seed_text
        target = "/?" + urlencode(params)

    auth_url = "/auth?" + urlencode({"redirect": target}) + f"#token={quote(token, safe='')}"
    return clear_and_redirect(auth_url)
