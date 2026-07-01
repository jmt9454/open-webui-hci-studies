"""
Admin-configurable settings for the research embed feature.

This router only manages configuration (PersistentConfig-backed, so it
survives restarts). It never touches participant accounts or chats directly
-- that's the standalone entry service's job, which reads this config via
GET /config using its admin API key. See /entry-service/entry_service.py.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from open_webui.utils.auth import get_admin_user

router = APIRouter()


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

    @field_validator("RESEARCH_EMBED_PARTICIPANT_ID_REGEX")
    @classmethod
    def validate_regex(cls, value: str) -> str:
        if value:
            try:
                re.compile(value)
            except re.error as e:
                raise ValueError(f"Not a valid regular expression: {e}")
        return value

    @field_validator("RESEARCH_EMBED_PARTICIPANT_ID_PARAM")
    @classmethod
    def validate_param_name(cls, value: str) -> str:
        if value and not re.match(r"^[A-Za-z0-9_-]+$", value):
            raise ValueError(
                "Participant ID param name should only contain letters, numbers, "
                "underscores, and hyphens (it becomes a URL query parameter name)."
            )
        return value

    @field_validator("RESEARCH_EMBED_ALLOWED_ORIGIN")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        if value and not re.match(r"^https?://[^/\s]+$", value):
            raise ValueError(
                "Allowed origin should look like https://yourorg.qualtrics.com "
                "(scheme + host, no trailing slash or path)."
            )
        return value


def _config_to_dict(request: Request) -> dict:
    return {
        "RESEARCH_EMBED_MODEL_ID": request.app.state.config.RESEARCH_EMBED_MODEL_ID,
        "RESEARCH_EMBED_SEED_MESSAGE": request.app.state.config.RESEARCH_EMBED_SEED_MESSAGE,
        "RESEARCH_EMBED_PARTICIPANT_ID_PARAM": request.app.state.config.RESEARCH_EMBED_PARTICIPANT_ID_PARAM,
        "RESEARCH_EMBED_PARTICIPANT_ID_REGEX": request.app.state.config.RESEARCH_EMBED_PARTICIPANT_ID_REGEX,
        "RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN": request.app.state.config.RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN,
        "RESEARCH_EMBED_ALLOWED_ORIGIN": request.app.state.config.RESEARCH_EMBED_ALLOWED_ORIGIN,
    }


@router.get("/config", response_model=ResearchEmbedConfigForm)
async def get_research_embed_config(request: Request, user=Depends(get_admin_user)):
    return _config_to_dict(request)


@router.post("/config", response_model=ResearchEmbedConfigForm)
async def set_research_embed_config(
    request: Request,
    form_data: ResearchEmbedConfigForm,
    user=Depends(get_admin_user),
):
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
