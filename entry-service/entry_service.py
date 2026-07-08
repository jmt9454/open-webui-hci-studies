"""
Entry service for the Open WebUI research embed.

Handles participant identity and hands each participant off to their own
chat in the forked Open WebUI instance. More than one study/condition can be
live on the same instance at once -- see futurefeature.md's "Per-Model
Research Embed" design -- so every /enter hit says explicitly which model
it's for (?model=<id>, required) and, when the survey platform provides one,
which survey/condition it belongs to (?survey=<id>, optional; Qualtrics's
own ${e://Field/SurveyID}). Talks to Open WebUI ONLY through its own public
REST API (never touches its internals / Python code directly):

  - GET  /api/v1/research-embed/config              -- global settings (participant ID
                                                        format, email domain) -- see
                                                        Admin Panel > Settings > Research Embed
  - GET  /api/v1/research-embed/models/{id}/config   -- per-model settings (enabled,
                                                        seed message) -- see
                                                        Workspace > Models > edit a model
  - POST /api/v1/auths/add                           -- create a participant account (admin token)
  - POST /api/v1/auths/signin                        -- sign in as that participant (email+password)
  - GET  /api/v1/chats/list, /api/v1/chats/{id}      -- find the participant's chat for this model

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
    (+ /api/v1/chats/{id} to check which model it uses, see
    find_existing_chat_id) rather than tracked in our own DB, since a
    chat-only participant (no sidebar, no new-chat button -- see the
    +layout.svelte / Chat.svelte edits) can never end up with more than one
    chat per model.

  - A browser that already has a stale localStorage.token (shared computer,
    staff testing the link, etc.) would otherwise short-circuit the handoff,
    since /auth's onMount redirects immediately if $user is already set,
    before it even looks at our #token= fragment. We serve a tiny HTML page
    that clears localStorage before navigating, so every /enter hit starts
    from a clean slate.

  - Participant ID param/regex and the participant email domain (global,
    shared by every study) are fetched live from GET
    /api/v1/research-embed/config; a specific model's enabled/seed_message
    come from GET /api/v1/research-embed/models/{id}/config. Both are cached
    (see CONFIG_CACHE_TTL_SECONDS / get_live_config / get_model_config) so a
    professor's changes take effect within that window without redeploying
    this service. If the global endpoint is unreachable (backend down,
    wrong admin key, or an older Open WebUI fork that doesn't have this
    router yet), this falls back to the env vars below rather than failing
    every /enter request outright; the per-model endpoint has no env var
    fallback (there's no single "the" model anymore) -- an unreachable
    backend just means /enter 503s until the connection is fixed.

  - Participant accounts are scoped by (external_id, scope_id), not
    external_id alone, where scope_id is the survey id if the platform sent
    one, else the model id (see get_or_create_participant). This is what
    lets the same participant legitimately show up in more than one
    concurrent study without their accounts colliding.

There is no manual "generate a key, paste it into .env, restart the
container" step needed anymore. On first boot this service has no admin key
at all and /enter says so clearly. Once an admin creates their account
through Open WebUI's normal onboarding screen, a button in Admin Panel >
Settings > Research Embed ("Connect Entry Service") generates a fresh API
key for that admin and pushes it here over the internal Docker network
(POST /internal/admin-key, gated by ENTRY_SERVICE_SYNC_SECRET -- auto-
generated on first use and shared with Open WebUI's backend via a small
Docker volume, see _get_or_create_shared_secret below, so there's no
bootstrapping problem: neither side needs a human to generate and hand off
a value before either one exists). The key is persisted to KEY_FILE_PATH so
it survives this container restarting on its own. OPEN_WEBUI_ADMIN_API_KEY
as an env var still works too, for scripted/advanced deployments that want
to skip the button entirely -- it just takes priority if set.

One thing you should still double-check against your own instance before
trusting this in production (see the guide's "verify" callouts): that
WEBUI_AUTH_TRUSTED_EMAIL_HEADER is NOT set. If it is, /api/v1/auths/signin
takes a completely different code path (trusted-header auth) and ignores
the email/password this service sends.
"""

import html
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import quote, urlencode

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

app = FastAPI()
log = logging.getLogger("entry_service")

# ---- Fixed operational config -- always from env vars, never admin-editable ----
OPEN_WEBUI_BASE_URL = os.environ.get("OPEN_WEBUI_BASE_URL", "http://open-webui:8080")
DB_PATH = os.environ.get("DB_PATH", "/data/participants.sqlite3")
KEY_FILE_PATH = os.environ.get("KEY_FILE_PATH", "/data/admin_api_key.txt")

def _get_or_create_shared_secret(path: str) -> str:
    """
    Reads a secret from `path`, generating and atomically persisting a new
    one the first time either side needs it. `path` lives on a Docker volume
    mounted into both this service and Open WebUI's backend (the
    `shared-secret` volume in docker-compose.research-embed.yml), so whichever
    container happens to boot first (or first hit this code) wins, and the
    other one just reads the same value back moments later -- nobody has to
    generate a value by hand and paste it into .env on both sides before
    either container can start.

    os.link() is what makes "create only if nobody beat us to it" atomic
    across two independent processes without an external lock: it's a
    hard-link, which the filesystem refuses to create if the destination
    already exists -- unlike a plain `open(path, "w")`, which would silently
    clobber whatever the other side just wrote.
    """
    try:
        with open(path) as f:
            existing = f.read().strip()
            if existing:
                return existing
    except FileNotFoundError:
        pass

    os.makedirs(os.path.dirname(path), exist_ok=True)
    candidate = secrets.token_hex(32)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w") as f:
        f.write(candidate)
    try:
        os.link(tmp_path, path)
    except FileExistsError:
        pass  # the other side won the race -- fine, we'll read its value below
    finally:
        os.remove(tmp_path)

    with open(path) as f:
        return f.read().strip()


# Shared secret authenticating the internal push from Open WebUI's backend
# (see POST /internal/admin-key below). An explicit ENTRY_SERVICE_SYNC_SECRET
# env var always wins (for scripted/advanced deployments that want a fixed,
# known value); otherwise it's auto-generated on first use and shared with
# Open WebUI's backend via a small Docker volume (see
# _get_or_create_shared_secret) -- no manual .env setup needed on either side.
SHARED_SECRET_FILE_PATH = os.environ.get(
    "SHARED_SECRET_FILE_PATH", "/shared/entry_service_sync_secret"
)
ENTRY_SERVICE_SYNC_SECRET = os.environ.get("ENTRY_SERVICE_SYNC_SECRET", "")
if not ENTRY_SERVICE_SYNC_SECRET:
    try:
        ENTRY_SERVICE_SYNC_SECRET = _get_or_create_shared_secret(SHARED_SECRET_FILE_PATH)
    except OSError as e:
        # Fails loudly on purpose rather than falling back to a per-process
        # random value: two independently-generated secrets would silently
        # break the sync handshake in a much more confusing way (403s that
        # look like a wrong-password bug) than a clear crash-on-boot pointing
        # at the actual cause -- almost always a missing/misconfigured
        # `shared-secret` volume mount.
        raise RuntimeError(
            f"Could not read or create a shared sync secret at "
            f"{SHARED_SECRET_FILE_PATH} ({e}). Check that the `shared-secret` "
            "Docker volume is mounted into this container (see "
            "docker-compose.research-embed.yml), or set ENTRY_SERVICE_SYNC_SECRET "
            "directly as an env var to bypass auto-generation entirely."
        ) from e

# ---- Admin API key -- mutable at runtime, see /internal/admin-key ----
# Priority: env var (if set) > persisted file from a previous sync > unset.
# Held in a dict (not a bare module-level string) so admin_headers() always
# reads the current value even after a runtime update, without needing
# `global` reassignment sprinkled through the file.
_admin_key_state = {"value": os.environ.get("OPEN_WEBUI_ADMIN_API_KEY", "")}
if not _admin_key_state["value"] and os.path.exists(KEY_FILE_PATH):
    try:
        with open(KEY_FILE_PATH) as f:
            _admin_key_state["value"] = f.read().strip()
    except OSError as e:
        log.warning("Could not read persisted admin key from %s: %s", KEY_FILE_PATH, e)


def is_admin_key_configured() -> bool:
    return bool(_admin_key_state["value"])


def set_admin_key(api_key: str) -> None:
    _admin_key_state["value"] = api_key
    try:
        os.makedirs(os.path.dirname(KEY_FILE_PATH), exist_ok=True)
        with open(KEY_FILE_PATH, "w") as f:
            f.write(api_key)
    except OSError as e:
        log.warning(
            "Admin key updated in memory but could not persist to %s (%s) -- "
            "it won't survive this container restarting.",
            KEY_FILE_PATH,
            e,
        )
    # The new key might unlock config that was previously unreachable.
    with _config_lock:
        _config_cache["value"] = None
        _config_cache["fetched_at"] = 0.0


# ---- Global study config -- these have env var defaults, but Admin Panel >
# Settings > Research Embed (once saved at least once) takes priority. See
# get_live_config(). Which model + seed message a study uses is NOT here --
# that's per-model now (Model.meta.research_embed), fetched on demand by
# get_model_config() below, since more than one study/model can be live on
# this instance at once. See futurefeature.md. ----
_ENV_DEFAULTS = {
    "RESEARCH_EMBED_PARTICIPANT_ID_PARAM": os.environ.get("PARTICIPANT_ID_PARAM", "pid"),
    "RESEARCH_EMBED_PARTICIPANT_ID_REGEX": os.environ.get(
        "PARTICIPANT_ID_REGEX", r"^R_[a-zA-Z0-9]{15,32}$"
    ),
    "RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN": os.environ.get(
        "PARTICIPANT_EMAIL_DOMAIN", "participants.local"
    ),
}

CONFIG_CACHE_TTL_SECONDS = 30
_config_cache = {"value": None, "fetched_at": 0.0}
_config_lock = threading.Lock()

# Separate cache for per-model config, keyed by model_id -- a study with
# several conditions hits several different model ids, and each one's
# enabled/seed_message can change independently, so one shared cache slot
# (like the global config above) wouldn't work here.
_model_config_cache: dict[str, tuple[float, "dict | None"]] = {}
_model_config_lock = threading.Lock()


def admin_headers():
    return {
        "Authorization": f"Bearer {_admin_key_state['value']}",
        "Content-Type": "application/json",
    }


def get_live_config() -> dict:
    """
    Merges the admin-configured global settings (Admin Panel > Settings >
    Research Embed) over this service's own env var defaults. Cached briefly
    so every participant request doesn't round-trip to the backend just to
    read config that rarely changes.
    """
    now = time.monotonic()
    with _config_lock:
        cached = _config_cache["value"]
        if cached is not None and (now - _config_cache["fetched_at"]) < CONFIG_CACHE_TTL_SECONDS:
            return cached

    merged = dict(_ENV_DEFAULTS)
    try:
        resp = requests.get(
            f"{OPEN_WEBUI_BASE_URL}/api/v1/research-embed/config",
            headers=admin_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        remote = resp.json()
        for key in _ENV_DEFAULTS:
            value = remote.get(key)
            if value:  # only override with non-empty values -- an admin who
                merged[key] = value  # hasn't touched a field yet shouldn't
                # blank out a working env var default.
    except Exception as e:
        log.warning(
            "Could not fetch live config from %s/api/v1/research-embed/config "
            "(%s) -- falling back to this service's own env vars.",
            OPEN_WEBUI_BASE_URL,
            e,
        )

    with _config_lock:
        _config_cache["value"] = merged
        _config_cache["fetched_at"] = now

    return merged


def get_model_config(model_id: str) -> "dict | None":
    """
    Fetches whether `model_id` is opted into research embed and its seed
    message, from GET /api/v1/research-embed/models/{model_id}/config.
    Returns None if the model doesn't exist or isn't enabled (the backend
    deliberately can't tell those two cases apart from the outside either --
    see that endpoint's docstring). Cached per model_id with the same TTL as
    the global config, including caching the "not enabled" result -- so a
    burst of hits against a bad/disabled model id doesn't hammer the backend
    with 404s.
    """
    now = time.monotonic()
    with _model_config_lock:
        cached = _model_config_cache.get(model_id)
        if cached is not None and (now - cached[0]) < CONFIG_CACHE_TTL_SECONDS:
            return cached[1]

    value = None
    try:
        resp = requests.get(
            f"{OPEN_WEBUI_BASE_URL}/api/v1/research-embed/models/"
            f"{quote(model_id, safe='')}/config",
            headers=admin_headers(),
            timeout=5,
        )
        if resp.status_code == 404:
            value = None
        else:
            resp.raise_for_status()
            value = resp.json()
    except Exception as e:
        log.warning(
            "Could not fetch research embed config for model %r from %s (%s) "
            "-- treating it as not enabled for this request.",
            model_id,
            OPEN_WEBUI_BASE_URL,
            e,
        )
        value = None

    with _model_config_lock:
        _model_config_cache[model_id] = (now, value)

    return value


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS participants (
            external_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (external_id, scope_id)
        )
        """
    )
    return conn


# Characters allowed as-is in the local part of an email address we build --
# anything else gets collapsed to '_' so a Qualtrics survey ID (or a model
# id containing e.g. ':') can't produce something Open WebUI's own email
# validation rejects.
_EMAIL_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _email_safe(value: str) -> str:
    return _EMAIL_SAFE_RE.sub("_", value)


def get_or_create_participant(conn, external_id: str, scope_id: str, email_domain: str):
    """
    A participant account is scoped by (external_id, scope_id) rather than
    external_id alone -- see futurefeature.md's "Per-Model Research Embed"
    design. `scope_id` is the requesting survey's id if the survey platform
    sends one (Qualtrics's ${e://Field/SurveyID}), else it falls back to the
    model id, which degrades exactly to "one account per participant" for a
    single-study deployment that never passes `survey=`. This is what lets
    the same external_id legitimately show up in more than one concurrent
    study on this instance without their accounts/chats colliding.
    """
    row = conn.execute(
        "SELECT email, password FROM participants WHERE external_id = ? AND scope_id = ?",
        (external_id, scope_id),
    ).fetchone()
    if row is not None:
        return row  # (email, password)

    # The email itself must be globally unique across ALL studies (Open
    # WebUI enforces unique emails instance-wide), so scope_id has to be
    # folded into it, not just used as a lookup key on our side.
    email = f"{_email_safe(external_id)}.{_email_safe(scope_id)}@{email_domain}"
    password = secrets.token_urlsafe(24)

    resp = requests.post(
        f"{OPEN_WEBUI_BASE_URL}/api/v1/auths/add",
        headers=admin_headers(),
        json={"name": external_id, "email": email, "password": password, "role": "user"},
        timeout=10,
    )
    resp.raise_for_status()

    conn.execute(
        "INSERT INTO participants (external_id, scope_id, email, password) VALUES (?, ?, ?, ?)",
        (external_id, scope_id, email, password),
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


def get_chat_detail(token: str, chat_id: str) -> dict:
    resp = requests.get(
        f"{OPEN_WEBUI_BASE_URL}/api/v1/chats/{chat_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def find_existing_chat_id(token: str, model_id: str):
    """
    A chat-only participant account is scoped to one (external_id, scope_id)
    pair -- see get_or_create_participant -- but within that account they
    can still end up with more than one chat if a single study embeds
    multiple models for a between-subjects design (each condition is a
    different model). So unlike a single-model deployment, we can't just
    take chats[0]: /chats/list doesn't say which model a chat uses, so each
    candidate's full detail is checked (GET /chats/{id}) until one whose
    stored `models` list contains the model this /enter hit actually asked
    for is found. An account only ever having 0-2 chats in practice (one per
    condition it's been through) keeps this cheap.
    """
    resp = requests.get(
        f"{OPEN_WEBUI_BASE_URL}/api/v1/chats/list",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    chats = resp.json()

    for chat in chats:
        try:
            detail = get_chat_detail(token, chat["id"])
        except requests.RequestException as e:
            log.warning("Could not fetch chat detail for %s: %s", chat["id"], e)
            continue

        chat_models = (detail.get("chat") or {}).get("models") or []
        if model_id in chat_models:
            return chat["id"]

    return None


# ---- Duplicate-chat race mitigation ----
#
# The chat itself isn't created by this service -- it's created lazily,
# client-side, by the browser after we redirect a first-time visitor to
# "/?models=<id>&q=<seed>" (see the module docstring for why we can't
# pre-create it server-side instead: Chat.svelte's submitPrompt() would fork
# a second, orphaned chat). That leaves a real window: if /enter gets hit a
# second time for the same participant before the browser has actually
# saved that first chat, find_existing_chat_id() legitimately finds nothing
# and this service sends them down the "first visit" path again, creating a
# second chat. Observed in practice from a survey iframe apparently loading
# /enter twice in immediate succession (same client connection, back-to-back
# in the access log).
#
# We can't make entry-service *wait* for the browser to finish creating the
# chat -- that happens after our HTTP response is already sent, in a
# separate process we have no handle on. What we *can* do is notice "another
# /enter for this same participant just happened a moment ago" and, in that
# specific case only, retry the chats/list check a few times with short
# pauses before giving up and creating a new chat. This closes the window
# for near-simultaneous duplicate hits (the actual observed case) without
# adding any latency to the normal, non-duplicate path.
_RECENT_ENTER_LOCK = threading.Lock()
_recent_enter_attempts: dict[str, float] = {}
_RECENT_ENTER_WINDOW_SECONDS = 10.0
_DUPLICATE_RETRY_ATTEMPTS = 8
_DUPLICATE_RETRY_DELAY_SECONDS = 0.5


def _is_likely_duplicate_enter(dedup_key: str) -> bool:
    """Records this attempt and reports whether a previous /enter with the
    same dedup_key was seen within _RECENT_ENTER_WINDOW_SECONDS. Keyed on
    (external_id, scope_id, model_id) jointly, not external_id alone -- the
    same participant legitimately hitting /enter for two different studies
    (or two different models/conditions within one study) close together is
    not the race this is meant to catch, and treating it as one would make
    this wait on, then fall through to, a chat search scoped to the wrong
    model. Also opportunistically prunes old entries so this dict doesn't
    grow forever over the life of the container."""
    now = time.monotonic()
    with _RECENT_ENTER_LOCK:
        for key in [
            k
            for k, ts in _recent_enter_attempts.items()
            if now - ts > _RECENT_ENTER_WINDOW_SECONDS
        ]:
            del _recent_enter_attempts[key]

        last_seen = _recent_enter_attempts.get(dedup_key)
        _recent_enter_attempts[dedup_key] = now

    return last_seen is not None and (now - last_seen) <= _RECENT_ENTER_WINDOW_SECONDS


def find_chat_id_for_new_or_returning_participant(
    token: str, external_id: str, scope_id: str, model_id: str
):
    """Wraps find_existing_chat_id() with the retry described above, only
    when a near-duplicate /enter for this (participant, scope, model) was
    just observed."""
    chat_id = find_existing_chat_id(token, model_id)
    if chat_id:
        return chat_id

    dedup_key = f"{external_id}\x1f{scope_id}\x1f{model_id}"
    if not _is_likely_duplicate_enter(dedup_key):
        return None

    for _ in range(_DUPLICATE_RETRY_ATTEMPTS):
        time.sleep(_DUPLICATE_RETRY_DELAY_SECONDS)
        chat_id = find_existing_chat_id(token, model_id)
        if chat_id:
            log.info(
                "Found chat for %s (scope=%s, model=%s) on retry after a "
                "near-duplicate /enter hit -- avoided creating a second chat.",
                external_id,
                scope_id,
                model_id,
            )
            return chat_id

    # Genuinely nothing there after ~4s of retrying -- either the first
    # attempt's chat creation actually failed (e.g. the participant closed
    # the tab before the seed message finished sending), or this really is
    # two deliberate, spaced-out visits that both happen to race past each
    # other's chat-list check. Either way, fall through to creating a normal
    # first-visit chat rather than blocking the participant indefinitely.
    return None


def clear_and_redirect(target_url: str) -> HTMLResponse:
    """Clear any stale localStorage session before handing off, then send the
    browser to `target_url` (the token itself is only ever transmitted in a
    URL fragment, which browsers never send to any server)."""
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


@app.get("/health")
def health():
    """
    Pure liveness check for Docker's HEALTHCHECK / compose's
    `condition: service_healthy` -- deliberately does NOT call out to Open
    WebUI or touch the SQLite file. This only needs to answer "is the
    process itself up and serving requests", not "is the whole system
    correctly configured yet" (that's what /enter's own 503s are for, since
    a not-yet-configured entry service is a normal, valid state that
    shouldn't make Docker think the container is unhealthy and restart it).
    """
    return JSONResponse({"status": "ok"})


@app.post("/internal/admin-key")
def receive_admin_key(payload: dict, x_sync_secret: str = Header(default="")):
    """
    Called by Open WebUI's backend (POST /api/v1/research-embed/sync-entry-service),
    itself triggered by the "Connect Entry Service" button in Admin Panel >
    Settings > Research Embed. Not reachable from outside the Docker network
    in a normal deployment -- Caddyfile.production only proxies /enter* to
    this service, nothing else -- but gated by a shared secret anyway as
    defense in depth in case that routing assumption ever changes.
    """
    if not ENTRY_SERVICE_SYNC_SECRET:
        # In practice this should be unreachable -- the module-level setup
        # above either has a secret by now or already raised RuntimeError at
        # import time -- but kept as a defensive guard in case that logic is
        # ever changed to fail softer.
        raise HTTPException(
            status_code=503,
            detail="ENTRY_SERVICE_SYNC_SECRET could not be determined on the "
            "entry service -- check the `shared-secret` Docker volume is "
            "mounted, or set ENTRY_SERVICE_SYNC_SECRET directly as an env var.",
        )
    if not secrets.compare_digest(x_sync_secret, ENTRY_SERVICE_SYNC_SECRET):
        raise HTTPException(status_code=403, detail="Invalid sync secret.")

    api_key = payload.get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing api_key.")

    set_admin_key(api_key)
    return JSONResponse({"status": "ok"})


@app.get("/enter")
def enter(request: Request):
    if not is_admin_key_configured():
        return PlainTextResponse(
            "This Open WebUI instance hasn't been connected to its entry "
            "service yet. An admin needs to log in, go to Admin Panel > "
            "Settings > Research Embed, and click 'Connect Entry Service'.",
            status_code=503,
        )

    live = get_live_config()

    participant_id_param = live["RESEARCH_EMBED_PARTICIPANT_ID_PARAM"]
    try:
        id_pattern = re.compile(live["RESEARCH_EMBED_PARTICIPANT_ID_REGEX"])
    except re.error:
        # An admin could in principle save an invalid regex (the Admin UI
        # validates this before saving, but env-var-only deployments don't
        # go through that check) -- fail closed rather than 500 on every hit.
        log.error(
            "RESEARCH_EMBED_PARTICIPANT_ID_REGEX is not a valid regex: %r",
            live["RESEARCH_EMBED_PARTICIPANT_ID_REGEX"],
        )
        return PlainTextResponse("Server misconfiguration.", status_code=500)

    external_id = request.query_params.get(participant_id_param, "")
    seed_override = request.query_params.get("seed")

    # `model` is now required -- this is the breaking part of the "Per-Model
    # Research Embed" change (see futurefeature.md): the embed URL used to
    # imply a single, instance-wide model, now every embed link says which
    # model it's for explicitly (the generated embed code always includes
    # this, see backend/open_webui/routers/research_embed.py's
    # get_model_embed_code). Any embed link generated before this change
    # won't have it and needs regenerating.
    model_id = request.query_params.get("model", "")
    if not model_id:
        return PlainTextResponse(
            "This entry link is missing its model parameter -- it was "
            "likely generated before this instance supported more than one "
            "study at a time. Regenerate it from Workspace > Models > "
            "(your study's model) > Research Embed.",
            status_code=400,
        )

    # `survey` is optional -- Qualtrics's own ${e://Field/SurveyID} piped
    # text, present on every link this fork's own generator produces (see
    # get_model_embed_code) but not guaranteed for a hand-built link or a
    # different survey platform. Falling back to model_id here means "no
    # survey id" degrades to one account per (participant, model) --
    # correct for a single-study-per-model deployment, and still keeps two
    # different models' participant pools from colliding with each other.
    scope_id = request.query_params.get("survey") or model_id

    if not id_pattern.match(external_id):
        return PlainTextResponse("Missing or invalid participant ID.", status_code=400)

    model_config = get_model_config(model_id)
    if not model_config:
        return PlainTextResponse(
            "This study isn't configured yet, or has been turned off -- an "
            "admin needs to enable Research Embed on this model (Workspace "
            "> Models > edit the model > Research Embed) and save.",
            status_code=503,
        )

    conn = get_db()
    try:
        email, password = get_or_create_participant(
            conn, external_id, scope_id, live["RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN"]
        )
    finally:
        conn.close()

    token = sign_in(email, password)
    chat_id = find_chat_id_for_new_or_returning_participant(
        token, external_id, scope_id, model_id
    )

    if chat_id:
        # Returning participant -- straight back to their one chat, no reseed.
        target = f"/c/{chat_id}?" + urlencode({"chatOnly": "true"})
    else:
        # First-ever visit (for this participant/study/model combination) --
        # land on a brand-new chat. Open WebUI's own `q` param
        # (initNewChat(), Chat.svelte) auto-submits it for us.
        seed_text = seed_override or model_config.get("seed_message") or ""
        params = {"chatOnly": "true", "models": model_id}
        if seed_text:
            params["q"] = seed_text
        target = "/?" + urlencode(params)

    auth_url = "/auth?" + urlencode({"redirect": target}) + f"#token={quote(token, safe='')}"
    return clear_and_redirect(auth_url)
