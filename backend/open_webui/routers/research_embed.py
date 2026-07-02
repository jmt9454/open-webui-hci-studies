"""
Admin-configurable settings for the research embed feature, plus optional
behavioral-tracking event ingest/export.

Most of this router only manages configuration (PersistentConfig-backed, so
it survives restarts) and never touches participant accounts or chats
directly -- that's the standalone entry service's job, which reads this
config via GET /config using its admin API key. See
/entry-service/entry_service.py.

The exception is the behavioral-tracking event endpoints near the bottom of
this file (POST /events, GET /events/export): those ARE hit directly by
participants' own browsers (authenticated as themselves, not the entry
service), since that's where keystroke/visibility/clipboard events actually
happen. See src/lib/utils/researchEmbedTracking.ts for the client side.
"""

import csv
import io
import json
import logging
import os
import re
import secrets

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.models.research_embed_events import (
    ResearchEmbedEventBatchForm,
    ResearchEmbedEvents,
)

log = logging.getLogger(__name__)
router = APIRouter()

# Topology/secret for reaching the entry service's internal sync endpoint.
# Deliberately plain os.environ reads, NOT PersistentConfig: these describe
# where the entry service lives and a shared secret authenticating pushes to
# it, not a setting a professor should ever see or edit through the Settings
# GET/POST round-trip (PersistentConfig values are readable via GET /config).
ENTRY_SERVICE_BASE_URL = os.environ.get("ENTRY_SERVICE_BASE_URL", "")


def _get_or_create_shared_secret(path: str) -> str:
    """
    Mirrors entry_service.py's function of the same name -- duplicated
    rather than imported because this backend and the entry service are two
    separately-deployed containers with no shared package between them, not
    because the logic differs.

    Reads a secret from `path`, generating and atomically persisting a new
    one the first time either side needs it. `path` lives on a Docker volume
    mounted into both this backend and the entry service (the
    `shared-secret` volume in docker-compose.research-embed.yml), so
    whichever container hits this first wins, and the other reads the same
    value back -- nobody has to generate a value and paste it into .env on
    both sides before either container can boot. os.link() makes "create
    only if nobody beat us to it" atomic across processes: it's a hard-link,
    which the filesystem refuses to create if the destination already
    exists, unlike a plain `open(path, "w")` which would clobber it.
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


# An explicit ENTRY_SERVICE_SYNC_SECRET env var always wins (for
# scripted/advanced deployments that want a fixed, known value); otherwise
# it's auto-generated on first use and shared with the entry service via a
# small Docker volume -- no manual .env setup needed on either side. If that
# auto-generation fails (e.g. the shared-secret volume isn't mounted), this
# import-time failure is caught and just leaves the value blank rather than
# crashing the whole backend over an optional feature -- /sync-entry-service
# below reports the problem clearly (503) instead when someone tries to use it.
SHARED_SECRET_FILE_PATH = os.environ.get(
    "SHARED_SECRET_FILE_PATH", "/shared/entry_service_sync_secret"
)
ENTRY_SERVICE_SYNC_SECRET = os.environ.get("ENTRY_SERVICE_SYNC_SECRET", "")
if not ENTRY_SERVICE_SYNC_SECRET:
    try:
        ENTRY_SERVICE_SYNC_SECRET = _get_or_create_shared_secret(SHARED_SECRET_FILE_PATH)
    except OSError as e:
        log.warning(
            "Could not read or create a shared sync secret at %s (%s) -- "
            "the 'Connect Entry Service' button will fail until the "
            "`shared-secret` volume is mounted correctly, or "
            "ENTRY_SERVICE_SYNC_SECRET is set directly as an env var.",
            SHARED_SECRET_FILE_PATH,
            e,
        )
        ENTRY_SERVICE_SYNC_SECRET = ""


############################
# Get / Set Config
############################


class ResearchEmbedConfigForm(BaseModel):
    RESEARCH_EMBED_MODEL_ID: str
    RESEARCH_EMBED_SEED_MESSAGE: str
    RESEARCH_EMBED_PARTICIPANT_ID_PARAM: str
    RESEARCH_EMBED_PARTICIPANT_ID_REGEX: str
    RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN: str
    RESEARCH_EMBED_ALLOWED_ORIGIN: str

    # Behavioral tracking toggles -- see the comment above the
    # RESEARCH_EMBED_TRACK_* PersistentConfig entries in config.py. Default
    # False; only enable once your IRB protocol / consent language covers
    # the specific feature.
    RESEARCH_EMBED_TRACK_KEYSTROKES: bool
    RESEARCH_EMBED_TRACK_TEMPORAL_DELAYS: bool
    RESEARCH_EMBED_TRACK_VISIBILITY: bool
    RESEARCH_EMBED_TRACK_CLIPBOARD: bool

    # NOTE: deliberately no @field_validator decorators here. Pydantic
    # field validators run during FastAPI's automatic request-body parsing,
    # *before* our route handler ever executes -- a raised ValueError there
    # gets turned into FastAPI's default 422 response, whose `detail` is a
    # LIST of structured error objects, not a plain string. The frontend's
    # fetch wrappers (src/lib/apis/*) universally assume `err.detail` is a
    # string they can drop into a toast (`toast.error(`${error}`)`), which
    # is true for every other HTTPException in this codebase but renders a
    # list of objects as a useless "[object Object]". Validating manually
    # inside the handler below and raising HTTPException(400, detail=<str>)
    # keeps us consistent with that existing convention instead of being the
    # one endpoint that breaks it.


def _config_to_dict(request: Request) -> dict:
    return {
        "RESEARCH_EMBED_MODEL_ID": request.app.state.config.RESEARCH_EMBED_MODEL_ID,
        "RESEARCH_EMBED_SEED_MESSAGE": request.app.state.config.RESEARCH_EMBED_SEED_MESSAGE,
        "RESEARCH_EMBED_PARTICIPANT_ID_PARAM": request.app.state.config.RESEARCH_EMBED_PARTICIPANT_ID_PARAM,
        "RESEARCH_EMBED_PARTICIPANT_ID_REGEX": request.app.state.config.RESEARCH_EMBED_PARTICIPANT_ID_REGEX,
        "RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN": request.app.state.config.RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN,
        "RESEARCH_EMBED_ALLOWED_ORIGIN": request.app.state.config.RESEARCH_EMBED_ALLOWED_ORIGIN,
        "RESEARCH_EMBED_TRACK_KEYSTROKES": request.app.state.config.RESEARCH_EMBED_TRACK_KEYSTROKES,
        "RESEARCH_EMBED_TRACK_TEMPORAL_DELAYS": request.app.state.config.RESEARCH_EMBED_TRACK_TEMPORAL_DELAYS,
        "RESEARCH_EMBED_TRACK_VISIBILITY": request.app.state.config.RESEARCH_EMBED_TRACK_VISIBILITY,
        "RESEARCH_EMBED_TRACK_CLIPBOARD": request.app.state.config.RESEARCH_EMBED_TRACK_CLIPBOARD,
    }


@router.get("/config", response_model=ResearchEmbedConfigForm)
async def get_research_embed_config(request: Request, user=Depends(get_admin_user)):
    return _config_to_dict(request)


def _validate_config_form(form_data: ResearchEmbedConfigForm) -> None:
    """Raises HTTPException(400, detail=<plain string>) on the first invalid
    field. See the comment on ResearchEmbedConfigForm for why this isn't
    done via @field_validator instead."""
    if form_data.RESEARCH_EMBED_PARTICIPANT_ID_REGEX:
        try:
            re.compile(form_data.RESEARCH_EMBED_PARTICIPANT_ID_REGEX)
        except re.error as e:
            raise HTTPException(
                status_code=400, detail=f"Not a valid regular expression: {e}"
            )

    if form_data.RESEARCH_EMBED_PARTICIPANT_ID_PARAM and not re.match(
        r"^[A-Za-z0-9_-]+$", form_data.RESEARCH_EMBED_PARTICIPANT_ID_PARAM
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Participant ID param name should only contain letters, numbers, "
                "underscores, and hyphens (it becomes a URL query parameter name)."
            ),
        )

    if form_data.RESEARCH_EMBED_ALLOWED_ORIGIN and not re.match(
        r"^https?://[^/\s]+$", form_data.RESEARCH_EMBED_ALLOWED_ORIGIN
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Allowed origin should look like https://yourorg.qualtrics.com "
                "(scheme + host, no trailing slash or path)."
            ),
        )


@router.post("/config", response_model=ResearchEmbedConfigForm)
async def set_research_embed_config(
    request: Request,
    form_data: ResearchEmbedConfigForm,
    user=Depends(get_admin_user),
):
    _validate_config_form(form_data)

    request.app.state.config.RESEARCH_EMBED_MODEL_ID = form_data.RESEARCH_EMBED_MODEL_ID
    request.app.state.config.RESEARCH_EMBED_SEED_MESSAGE = (
        form_data.RESEARCH_EMBED_SEED_MESSAGE
    )
    request.app.state.config.RESEARCH_EMBED_PARTICIPANT_ID_PARAM = (
        form_data.RESEARCH_EMBED_PARTICIPANT_ID_PARAM
    )
    request.app.state.config.RESEARCH_EMBED_PARTICIPANT_ID_REGEX = (
        form_data.RESEARCH_EMBED_PARTICIPANT_ID_REGEX
    )
    request.app.state.config.RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN = (
        form_data.RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN
    )
    request.app.state.config.RESEARCH_EMBED_ALLOWED_ORIGIN = (
        form_data.RESEARCH_EMBED_ALLOWED_ORIGIN
    )
    request.app.state.config.RESEARCH_EMBED_TRACK_KEYSTROKES = (
        form_data.RESEARCH_EMBED_TRACK_KEYSTROKES
    )
    request.app.state.config.RESEARCH_EMBED_TRACK_TEMPORAL_DELAYS = (
        form_data.RESEARCH_EMBED_TRACK_TEMPORAL_DELAYS
    )
    request.app.state.config.RESEARCH_EMBED_TRACK_VISIBILITY = (
        form_data.RESEARCH_EMBED_TRACK_VISIBILITY
    )
    request.app.state.config.RESEARCH_EMBED_TRACK_CLIPBOARD = (
        form_data.RESEARCH_EMBED_TRACK_CLIPBOARD
    )
    return _config_to_dict(request)


############################
# Generate Embed Code
############################


class EmbedCodeResponse(BaseModel):
    entry_url: str
    iframe_snippet: str
    warnings: list[str]


@router.get("/embed-code", response_model=EmbedCodeResponse)
async def get_research_embed_code(request: Request, user=Depends(get_admin_user)):
    """
    Builds the Qualtrics-ready entry URL and <iframe> snippet from the
    currently saved config. Uses the host/scheme the admin's own browser used
    to reach this API (request.base_url) as the public domain -- correct as
    long as the backend is run behind the reverse proxy described in Part 5
    (Caddy forwards the original Host/scheme). If this instance is reachable
    under a different public domain than what admins use internally, edit
    the generated URL's host manually before pasting it into Qualtrics.
    """
    config = _config_to_dict(request)
    warnings = []

    if not config["RESEARCH_EMBED_MODEL_ID"]:
        warnings.append(
            "No model selected yet -- participants won't be able to send messages "
            "until you pick one and save."
        )
    if not config["RESEARCH_EMBED_ALLOWED_ORIGIN"]:
        warnings.append(
            "No allowed origin set -- most browsers will refuse to render this in "
            "a Qualtrics iframe until you set your survey platform's domain."
        )

    param = config["RESEARCH_EMBED_PARTICIPANT_ID_PARAM"] or "pid"
    base = str(request.base_url).rstrip("/")

    # ${e://Field/ResponseID} is Qualtrics' own piped-text syntax -- it must
    # stay literal (unescaped) in the URL; Qualtrics substitutes it with the
    # real response ID before the participant's browser ever requests it.
    entry_url = f"{base}/enter?{param}=${{e://Field/ResponseID}}"

    iframe_snippet = (
        f'<iframe src="{entry_url}" width="100%" height="700" '
        f'style="border:none;" title="Study Chat"></iframe>'
    )

    return {
        "entry_url": entry_url,
        "iframe_snippet": iframe_snippet,
        "warnings": warnings,
    }


############################
# Connect Entry Service (zero-touch bootstrap)
############################
#
# The entry service starts with no admin API key at all -- there's no way it
# could, since that key can only exist after a human creates the admin
# account through Open WebUI's own onboarding screen, which happens after
# first boot. This endpoint is what the "Connect Entry Service" button in
# Admin Panel > Settings > Research Embed calls: the frontend first generates
# a fresh key for the current admin (POST /api/v1/auths/api_key, already
# existed before this feature), then hands it to us here, and we forward it
# over the internal Docker network to entry_service.py's
# POST /internal/admin-key. Nothing in this flow needs a container restart
# or hand-editing of .env.


class SyncEntryServiceForm(BaseModel):
    api_key: str


class SyncEntryServiceResponse(BaseModel):
    status: str
    detail: str = ""


@router.post("/sync-entry-service", response_model=SyncEntryServiceResponse)
async def sync_entry_service(
    form_data: SyncEntryServiceForm, user=Depends(get_admin_user)
):
    if not ENTRY_SERVICE_BASE_URL or not ENTRY_SERVICE_SYNC_SECRET:
        raise HTTPException(
            status_code=503,
            detail=(
                "ENTRY_SERVICE_BASE_URL is not set, and/or the auto-generated "
                "ENTRY_SERVICE_SYNC_SECRET could not be read or created -- check "
                "that the `shared-secret` Docker volume from "
                "docker-compose.research-embed.yml is mounted into this "
                "container. Not editable here, since these are shared "
                "topology/secrets rather than per-study settings."
            ),
        )

    if not form_data.api_key:
        raise HTTPException(status_code=400, detail="Missing api_key.")

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.post(
                f"{ENTRY_SERVICE_BASE_URL.rstrip('/')}/internal/admin-key",
                headers={"X-Sync-Secret": ENTRY_SERVICE_SYNC_SECRET},
                json={"api_key": form_data.api_key},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Entry service rejected the key (HTTP {resp.status}): {body}"
                        ),
                    )
    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Could not reach the entry service at {ENTRY_SERVICE_BASE_URL} ({e}). "
                "Check that the entry-service container is running and "
                "ENTRY_SERVICE_BASE_URL points to its address on the Docker network "
                "(e.g. http://entry-service:9000)."
            ),
        )

    return {"status": "ok", "detail": "Entry service connected."}


############################
# Behavioral tracking events
############################
#
# Ingest is intentionally permissive about *when* it accepts events (any
# signed-in user, not just chat-only participants -- staff previewing the
# embed with ?chatOnly=true produce the same event shape) but strict about
# *whether tracking is on at all*: each of the four feature toggles is
# re-checked server-side per batch, not just trusted from the client, so a
# stale/modified frontend can't write data for a feature an admin has since
# turned off.


@router.post("/events")
async def ingest_research_embed_events(
    request: Request,
    form_data: ResearchEmbedEventBatchForm,
    user=Depends(get_verified_user),
):
    enabled_event_types = set()
    if request.app.state.config.RESEARCH_EMBED_TRACK_KEYSTROKES:
        enabled_event_types.add("keystroke")
    if request.app.state.config.RESEARCH_EMBED_TRACK_TEMPORAL_DELAYS:
        enabled_event_types.add("temporal_delay")
    if request.app.state.config.RESEARCH_EMBED_TRACK_VISIBILITY:
        enabled_event_types.add("visibility")
    if request.app.state.config.RESEARCH_EMBED_TRACK_CLIPBOARD:
        enabled_event_types.add("clipboard")

    accepted = [e for e in form_data.events if e.event_type in enabled_event_types]
    dropped = len(form_data.events) - len(accepted)
    if dropped:
        log.info(
            "Dropped %d research embed event(s) for a type that's currently "
            "disabled (user_id=%s).",
            dropped,
            user.id,
        )

    inserted = ResearchEmbedEvents.insert_events(user.id, accepted)
    return {"accepted": inserted, "dropped": dropped}


def _json_dumps_for_csv(data: dict) -> str:
    try:
        return json.dumps(data)
    except (TypeError, ValueError):
        return str(data)


@router.get("/events/export")
async def export_research_embed_events(
    request: Request,
    format: str = "csv",
    user=Depends(get_admin_user),
):
    """Admin-only. Every behavioral-tracking event ever recorded, across all
    participants, oldest first. Meant for pulling into your own analysis
    pipeline (R, pandas, etc.) -- this fork doesn't build an in-app
    aggregate-analytics dashboard on top of this data."""
    events = ResearchEmbedEvents.get_all_events()

    if format == "json":
        return [e.model_dump() for e in events]

    if format != "csv":
        raise HTTPException(
            status_code=400, detail="format must be 'csv' or 'json'."
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["id", "user_id", "chat_id", "event_type", "client_timestamp", "created_at", "data"]
    )
    for e in events:
        writer.writerow(
            [
                e.id,
                e.user_id,
                e.chat_id or "",
                e.event_type,
                e.client_timestamp if e.client_timestamp is not None else "",
                e.created_at,
                # `data` is a nested JSON payload with a shape that varies by
                # event_type -- serialize it as a JSON string in a single CSV
                # cell rather than trying to flatten every possible key into
                # its own column.
                _json_dumps_for_csv(e.data),
            ]
        )

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=research_embed_events.csv"
        },
    )
