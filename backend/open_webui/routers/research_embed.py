"""
Admin-configurable settings for the research embed feature, plus optional
behavioral-tracking event ingest/export.

Most of this router only manages configuration (PersistentConfig-backed, so
it survives restarts) and never touches participant accounts or chats
directly -- that's the standalone entry service's job, which reads this
config via GET /config using its admin API key. See
/entry-service/entry_service.py.

Which model + seed message a study uses is NOT part of that global config --
see futurefeature.md's "Per-Model Research Embed" design (now implemented):
that lives per-model in Model.meta.research_embed instead, edited from
Workspace > Models rather than this admin settings page, so more than one
study/condition can run on the same instance at once. The
GET /models/{model_id}/config and GET /models/{model_id}/embed-code
endpoints below are the per-model equivalents of this file's old global
/config and /embed-code endpoints.

The other exception is the behavioral-tracking event endpoints near the
bottom of this file (POST /events, GET /events/export): those ARE hit
directly by participants' own browsers (authenticated as themselves, not the
entry service), since that's where keystroke/visibility/clipboard events
actually happen. See src/lib/utils/researchEmbedTracking.ts for the client
side.
"""

import csv
import io
import json
import logging
import os
import re
import secrets
from typing import Optional
from urllib.parse import quote

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.models.models import Models
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
    # Model + seed message are NOT here anymore -- they moved to per-model
    # Model.meta.research_embed (see futurefeature.md's "Per-Model Research
    # Embed" design). Behavioral tracking toggles moved there too (see the
    # comment above ModelMeta.research_embed in models/models.py), for the
    # same reason: each study should be able to enable its own tracking
    # independently. What's left here is genuinely instance-wide: one set of
    # participant-ID parsing rules and one CSP allowed-origin list, shared by
    # every study running on this instance.
    RESEARCH_EMBED_PARTICIPANT_ID_PARAM: str
    RESEARCH_EMBED_PARTICIPANT_ID_REGEX: str
    RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN: str
    RESEARCH_EMBED_ALLOWED_ORIGIN: str

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
        "RESEARCH_EMBED_PARTICIPANT_ID_PARAM": request.app.state.config.RESEARCH_EMBED_PARTICIPANT_ID_PARAM,
        "RESEARCH_EMBED_PARTICIPANT_ID_REGEX": request.app.state.config.RESEARCH_EMBED_PARTICIPANT_ID_REGEX,
        "RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN": request.app.state.config.RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN,
        "RESEARCH_EMBED_ALLOWED_ORIGIN": request.app.state.config.RESEARCH_EMBED_ALLOWED_ORIGIN,
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
    return _config_to_dict(request)


############################
# Per-Model Research Embed
############################
#
# Whether a model is opted into research embed, its seed message, and its
# generated embed code all live per-model (Model.meta.research_embed) rather
# than in the global config above -- see futurefeature.md. Enabling/editing
# these is admin-only regardless of who can otherwise edit the model
# (enforced server-side in routers/models.py's create/update handlers, not
# just hidden in the UI), since a research embed link is an unauthenticated
# public entry point that creates real accounts and spends that model's API
# budget.


class ModelResearchEmbedConfigResponse(BaseModel):
    enabled: bool
    seed_message: str


@router.get(
    "/models/{model_id}/config", response_model=ModelResearchEmbedConfigResponse
)
async def get_model_research_embed_config(model_id: str, user=Depends(get_admin_user)):
    """
    Called by the entry service (using its admin API key) to find out
    whether a specific model is opted into research embed, and what its seed
    message is. 404s identically whether the model doesn't exist or simply
    was never enabled for research embed -- deliberately indistinguishable,
    so this can't be used to enumerate model ids that exist on this instance
    but aren't public, or to tell those two cases apart from the outside.
    """
    model = Models.get_model_by_id(model_id)
    research_embed = (getattr(model.meta, "research_embed", None) if model else None) or {}

    if not model or not research_embed.get("enabled"):
        raise HTTPException(status_code=404, detail="Not found.")

    return {
        "enabled": True,
        "seed_message": research_embed.get("seed_message") or "",
    }


class ModelTrackingConfigResponse(BaseModel):
    track_keystrokes: bool
    track_temporal_delays: bool
    track_visibility: bool
    track_clipboard: bool


@router.get(
    "/models/{model_id}/tracking-config", response_model=ModelTrackingConfigResponse
)
async def get_model_tracking_config(model_id: str, user=Depends(get_verified_user)):
    """
    Called by ANY signed-in user's own browser (src/lib/utils/
    researchEmbedTracking.ts), not just admins -- unlike
    GET /models/{model_id}/config above, this doesn't gate on the entry
    service's admin key, since the client that needs these four booleans is
    the participant's (or a staff member's, previewing the embed) own chat
    page deciding whether to instrument itself at all. Returns all-False
    rather than 404 for a model with no research_embed config -- these
    booleans aren't sensitive, and a 404 here would just be extra client-side
    special-casing for no real benefit.
    """
    model = Models.get_model_by_id(model_id)
    research_embed = (getattr(model.meta, "research_embed", None) if model else None) or {}

    return {
        "track_keystrokes": bool(research_embed.get("track_keystrokes")),
        "track_temporal_delays": bool(research_embed.get("track_temporal_delays")),
        "track_visibility": bool(research_embed.get("track_visibility")),
        "track_clipboard": bool(research_embed.get("track_clipboard")),
    }


class ModelEmbedCodeResponse(BaseModel):
    entry_url: str
    iframe_snippet: str
    warnings: list[str]


@router.get("/models/{model_id}/embed-code", response_model=ModelEmbedCodeResponse)
async def get_model_embed_code(
    request: Request, model_id: str, user=Depends(get_admin_user)
):
    """
    Builds the Qualtrics-ready entry URL and <iframe> snippet for one
    specific model's research embed. Unlike the old global version of this
    endpoint, the URL now carries the model explicitly (&model=<id>,
    required by entry_service.py's /enter) and the survey/condition
    implicitly (&survey=${e://Field/SurveyID}, Qualtrics's own per-survey
    piped-text field) -- that's what lets more than one study run on this
    instance at once without their participant accounts/chats colliding.
    Uses the host/scheme the admin's own browser used to reach this API
    (request.base_url) as the public domain, same caveat as before: if this
    instance is reachable under a different public domain than what admins
    use internally, edit the generated URL's host manually before pasting it
    into Qualtrics.
    """
    model = Models.get_model_by_id(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found.")

    research_embed = getattr(model.meta, "research_embed", None) or {}
    warnings = []
    if not research_embed.get("enabled"):
        warnings.append(
            "Research embed isn't enabled on this model yet -- turn it on "
            "below and save before sharing this link."
        )

    global_config = _config_to_dict(request)
    if not global_config["RESEARCH_EMBED_ALLOWED_ORIGIN"]:
        warnings.append(
            "No Allowed Embed Origin set (Admin Panel > Settings > Research "
            "Embed) -- most browsers will refuse to render this in a "
            "Qualtrics iframe until you set your survey platform's domain."
        )

    param = global_config["RESEARCH_EMBED_PARTICIPANT_ID_PARAM"] or "pid"
    base = str(request.base_url).rstrip("/")

    # ${e://Field/ResponseID} and ${e://Field/SurveyID} are Qualtrics' own
    # piped-text syntax -- they must stay literal (unescaped) in the URL;
    # Qualtrics substitutes them with the real response/survey ID before the
    # participant's browser ever requests it. model_id is quoted since model
    # ids can contain characters like ':' (e.g. "llama3:latest").
    entry_url = (
        f"{base}/enter?{param}=${{e://Field/ResponseID}}"
        f"&model={quote(model_id, safe='')}"
        f"&survey=${{e://Field/SurveyID}}"
    )

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
    """
    Which event types are accepted is now looked up from the batch's own
    model_id (Model.meta.research_embed's track_* keys) rather than a global
    config -- each study enables its own tracking independently. A batch
    with no model_id, or one naming a model that doesn't exist, drops
    everything: there's no instance-wide fallback to fall back to anymore,
    so "unknown model" and "no tracking enabled" are treated the same way
    (silently drop, don't error the participant's page over it).
    """
    model = Models.get_model_by_id(form_data.model_id) if form_data.model_id else None
    research_embed = (getattr(model.meta, "research_embed", None) if model else None) or {}

    enabled_event_types = set()
    if research_embed.get("track_keystrokes"):
        enabled_event_types.add("keystroke")
    if research_embed.get("track_temporal_delays"):
        enabled_event_types.add("temporal_delay")
    if research_embed.get("track_visibility"):
        enabled_event_types.add("visibility")
    if research_embed.get("track_clipboard"):
        enabled_event_types.add("clipboard")

    accepted = [e for e in form_data.events if e.event_type in enabled_event_types]
    dropped = len(form_data.events) - len(accepted)
    if dropped:
        log.info(
            "Dropped %d research embed event(s) for a type that's currently "
            "disabled (user_id=%s, model_id=%s).",
            dropped,
            user.id,
            form_data.model_id,
        )

    inserted = ResearchEmbedEvents.insert_events(user.id, form_data.model_id, accepted)
    return {"accepted": inserted, "dropped": dropped}


def _json_dumps_for_csv(data: dict) -> str:
    try:
        return json.dumps(data)
    except (TypeError, ValueError):
        return str(data)


_EVENTS_CSV_HEADER = [
    "id",
    "user_id",
    "chat_id",
    "model_id",
    "event_type",
    "client_timestamp",
    "created_at",
    "data",
]


def _events_to_csv_response(events: list, filename: str) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_EVENTS_CSV_HEADER)
    for e in events:
        writer.writerow(
            [
                e.id,
                e.user_id,
                e.chat_id or "",
                e.model_id or "",
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
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/events/export")
async def export_research_embed_events(
    request: Request,
    format: str = "csv",
    user=Depends(get_admin_user),
):
    """Admin-only. Every behavioral-tracking event ever recorded, across
    EVERY model/study on this instance at once, oldest first -- most
    researchers running a single study want
    GET /models/{model_id}/events?format=csv instead; this is for someone
    who genuinely wants everything in one file. Meant for pulling into your
    own analysis pipeline (R, pandas, etc.) -- this fork doesn't build an
    in-app aggregate-analytics dashboard on top of this data."""
    events = ResearchEmbedEvents.get_all_events()

    if format == "json":
        return [e.model_dump() for e in events]

    if format != "csv":
        raise HTTPException(
            status_code=400, detail="format must be 'csv' or 'json'."
        )

    return _events_to_csv_response(events, "research_embed_events.csv")


@router.get("/models/{model_id}/events")
async def get_model_research_embed_events(
    model_id: str,
    format: str = "json",
    limit: int = 50,
    offset: int = 0,
    event_type: Optional[str] = None,
    user=Depends(get_admin_user),
):
    """
    Admin-only, scoped to one model/study. Two shapes depending on `format`:

    - format=json (default): a page of events -- {"total": N, "events": [...]}
      -- meant for the in-app data viewer (Workspace > Models > Research
      Embed > View Data). limit/offset paginate, event_type optionally
      narrows to one of "keystroke"/"temporal_delay"/"visibility"/"clipboard".
    - format=csv: EVERY event for this model (limit/offset/event_type are
      ignored), as a single downloadable file -- meant for pulling into your
      own analysis pipeline, same shape as the instance-wide
      GET /events/export above but filtered to just this model.
    """
    if format == "csv":
        events = ResearchEmbedEvents.get_events_by_model_id(model_id)
        return _events_to_csv_response(
            events, f"research_embed_events_{model_id}.csv"
        )

    if format != "json":
        raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'.")

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    total = ResearchEmbedEvents.count_events_by_model_id(model_id, event_type)
    events = ResearchEmbedEvents.get_events_by_model_id(
        model_id, skip=offset, limit=limit, event_type=event_type
    )
    return {"total": total, "events": [e.model_dump() for e in events]}
