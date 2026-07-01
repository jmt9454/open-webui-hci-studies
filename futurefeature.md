# Future Feature: Per-Model Research Embed

Status: proposed, not implemented. This document captures the design discussion
so it can be picked up later without re-deriving it from scratch.

## Motivation

The current implementation (Admin Panel > Settings > Research Embed) treats
"research embed" as a single, instance-wide configuration: one model, one seed
message, one participant ID format, one allowed embed origin. That's fine for
a single study, but breaks down as soon as a professor wants to run more than
one study (or study condition) on the same Open WebUI instance -- there's
nowhere to configure a second seed message or a second model.

The fix: make the embed-relevant settings (enable toggle, seed message,
generated embed code) a property of each individual model, configured from
Workspace > Models, rather than a single global settings page. A professor
creating a custom model for "Condition A" and another for "Condition B" would
independently enable and configure research embed on each, and get a
distinct Qualtrics embed link per condition.

## Data model

`Model.meta` (`backend/open_webui/models/models.py`) is already a free-form
JSON blob (`ModelMeta` has `model_config = ConfigDict(extra="allow")`), the
same place `capabilities` already lives. No schema migration needed -- add:

```
model.meta.research_embed = {
    "enabled": bool,
    "seed_message": str,
}
```

## What stays global vs. what moves per-model

**Per-model** (lives in `model.meta.research_embed`, edited from
`ModelEditor.svelte`):
- Enabled (on/off)
- Seed message
- The generated embed code itself (since the model is now implicit -- no
  dropdown needed)

**Stays global** (slimmed-down Admin Settings > Research Embed page):
- Participant ID parameter name (e.g. `pid`)
- Participant ID regex
- Participant email domain
- Allowed embed origin(s) -- `Content-Security-Policy: frame-ancestors` is
  set instance-wide on every response regardless of which model a
  participant lands on, so if multiple studies embed from different survey
  platforms, this needs to be a space-separated list covering all of them,
  not one value per study.

## Participant/study scoping (the part with real ripple effects)

The current entry service assumes "one participant = one account = one
chat" (`find_existing_chat_id` just takes `chats[0]`). That assumption
breaks once one instance can host multiple concurrent embedded studies.

Model ID alone is not the right scoping key for this, because:
- The same model can be reused across multiple separate survey deployments
  (a pilot, then the real study, both using the same "Condition A" model) --
  model ID can't tell those apart.
- One study can embed multiple models for a between-subjects design (each
  condition is a different model, but they're all one study for analysis
  purposes).

**Resolution: use the survey platform's own study identifier.** Qualtrics
already exposes `${e://Field/SurveyID}` as a built-in piped-text field,
separate from `${e://Field/ResponseID}`. Prolific similarly passes its own
`STUDY_ID` natively in completion URLs. Passing this through costs the admin
nothing extra -- the platform fills it in automatically, same as the
participant ID already is.

Design:
- The embed URL becomes `/enter?pid=...&survey=...&model=<model_id>`. The
  embed-code generator adds the `survey=` piece automatically using
  Qualtrics's `${e://Field/SurveyID}` syntax (the guide's primary target
  platform); adapting it for another platform's placeholder syntax is a
  detail of the generator template, not the entry service's logic.
- Participant **accounts** are scoped by `(external_id, survey_id)` instead
  of `(external_id, model_id)`. If no `survey_id` is present (a platform
  that doesn't send one), fall back to scoping by `model_id`, which
  degrades exactly to the original single-study design.
- **Chats** stay scoped by `(account, model_id)`. `find_existing_chat_id`
  needs to actually inspect each candidate chat's stored model list and
  match on the requested model, rather than blindly taking the first
  result. This logic is only actually exercised in the multi-model-per-study
  case -- for a single condition per study, an account will only ever have
  one chat anyway, so this doesn't add risk to the common case.
- The entry service's SQLite participants table key changes from just
  `external_id` to `(external_id, survey_id)`.

## Backend changes needed

- `GET/POST /api/v1/research-embed/config` shrinks to just the four global
  fields (participant ID param/regex/email domain, allowed origin).
- New endpoint: `GET /api/v1/research-embed/models/{model_id}/config`,
  returning that model's `enabled`/`seed_message` from `meta.research_embed`.
  Returns 404 (or an explicit "not enabled") if the model was never opted
  in -- this stops someone hand-crafting a `/enter?...&model=<arbitrary_id>`
  link for a model that was never meant to be public.
- Saving the enable toggle / seed message reuses the existing model-update
  endpoint (just extends its payload with `meta.research_embed`) -- no new
  save endpoint needed there.
- A new endpoint (or extension of the one above) to generate the embed code
  for a specific model, reusing the URL-building logic already written for
  the global `/embed-code` endpoint, now also emitting `&survey=...` and
  `&model=...`.

## Frontend changes needed

- Strip the model dropdown, seed message field, and embed-code section out
  of the global `ResearchEmbed.svelte` admin settings page.
- Add a new component (e.g. `ModelResearchEmbed.svelte`) inside
  `ModelEditor.svelte`, following the exact pattern `Capabilities.svelte`
  already uses: bound to a local object, read from
  `model?.meta?.research_embed` on load, written to
  `info.meta.research_embed` on save.
- Embed code generation needs a real persisted model ID, so that section
  can only appear after a model's first save, not while creating a brand
  new one (same UX constraint as "share" or "clone" actions on unsaved
  entities elsewhere in the app).

## Entry service changes needed

- `get_live_config()` becomes model-aware: fetch the global participant-
  format settings (rarely changes, can stay cached longer) plus the
  specific model's `enabled`/`seed_message` via the new per-model endpoint.
- Read `model` and `survey` query params in `/enter`, in addition to the
  existing participant ID param.
- Reject the request (400/404) if the requested model isn't enabled for
  research embed, rather than silently falling back to some default model.
- Update `get_or_create_participant` and the SQLite schema to key on
  `(external_id, survey_id_or_model_id_fallback)`.
- Update `find_existing_chat_id` to filter by model rather than take the
  first result.

## Open decision: who can enable this on a model?

Workspace models can be created by non-admin users if that permission is
granted on the instance. Enabling research embed on a model opens an
unauthenticated public entry point that creates real accounts and spends
API budget on that model. Needs a decision before implementation:

- Option A: the "Enable Research Embed" toggle and embed-code generation
  are admin-only, regardless of who owns/can edit the model (the section
  simply doesn't render for non-admins, even in the editor for a model they
  own).
- Option B: available to anyone who can edit the model, same as any other
  model setting.

Leaning toward Option A given the cost/abuse exposure, but this is a product
call, not a technical one, and should be confirmed before implementation.

## Backward compatibility note

This is a breaking change to the embed URL shape (adds required `model=`,
optional `survey=`) and to the entry service's SQLite schema (key changes
from `external_id` to a composite key). Any already-deployed embed links
using the current global-config design would need to be regenerated, and
existing participant rows in the entry service's database would not carry
forward cleanly -- likely needs a one-time migration script or an accepted
reset of the participants table when this ships.
