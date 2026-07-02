# Research Embed: Setup & Researcher Guide

This fork of [Open WebUI](https://github.com/open-webui/open-webui) adds a
**chat-only mode** you can embed directly in a Qualtrics survey (or any
platform that can host an iframe or a link). Each participant who opens the
embed link automatically gets their own account and a single chat, with no
sidebar, no settings, and no way to see or reach anything else in the app.

This document is the setup and operating guide. If you just want to get a
study running, start at [Quickstart](#quickstart) below.

## How it works, briefly

Three pieces run together behind one reverse proxy (Caddy):

- **Open WebUI** (this fork) -- the actual chat app. Runs in "chat-only"
  mode for participant accounts, detected by email domain.
- **entry-service** -- a small standalone service. When a participant opens
  `/enter?pid=...`, it creates (or looks up) that participant's account,
  signs them in, and redirects them straight into their one chat. It talks
  to Open WebUI only through its normal public REST API.
- **Caddy** -- reverse proxy, routes `/enter*` to entry-service and
  everything else to Open WebUI, and gets you a real HTTPS certificate
  automatically once you have a real domain.

Everything you'd actually want to change for a study -- which model,
the seed message, the participant ID format, which domain is allowed to
embed you -- is configured live from **Admin Panel > Settings > Research
Embed**, no redeploy needed.

## Prerequisites

- Docker and Docker Compose (v2, the `docker compose` subcommand -- not the
  old standalone `docker-compose`).
- An API key for whatever model you want participants to talk to (e.g. an
  OpenAI API key), added after the stack is up (see below) -- you don't need
  it beforehand.
- For a real deployment: a domain name pointed at your server. For local
  testing, you don't need one at all.

## Quickstart

1. Clone this repo and `cd` into it.

2. Copy the example env file and fill in `DOMAIN`:

   ```
   cp .env.research-embed.example .env
   ```

   For local testing, set `DOMAIN=localhost` in `.env`. For a real
   deployment, use a real hostname that already points at this server (e.g.
   `chat.yourlab.edu`). Everything else in that file is optional -- see the
   comments in `.env.research-embed.example` and
   `docker-compose.research-embed.yml`.

3. Bring the stack up. This pulls prebuilt images (no local build, no wait):

   ```
   docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml up -d
   ```

   If you've brought this stack up before (e.g. while testing), run `down`
   first as a habit -- it's a no-op if nothing's running, and it prevents the
   most common cause of the port-80-already-in-use error below (a leftover
   container from an earlier run that was never torn down):

   ```
   docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml down
   ```

4. Open `https://localhost` (local) or `https://your-domain` (real deploy)
   and create your admin account through the normal onboarding screen.
   (Local testing over `https://localhost` will show a browser warning
   about the certificate not being trusted -- that's expected, see
   [Local testing](#local-testing-vs-real-deployment) below; click through it.)

5. Add a model connection: **Admin Panel > Settings > Connections**, add
   your OpenAI (or other) API key, same as any normal Open WebUI setup.

6. Go to **Admin Panel > Settings > Research Embed**:
   - Pick the model participants will talk to.
   - Optionally set a seed message (the first message participants see
     already sent, e.g. "Hi! Tell me about a recent purchase you regret.").
   - Set the **Allowed Embed Origin** to your survey platform's domain
     (e.g. `https://yourorg.qualtrics.com`) -- required for the embed to
     actually render in an iframe.
   - Click **Save**.
   - Click **Connect Entry Service**. This is the one-time step that lets
     entry-service create participant accounts on your behalf -- no manual
     key generation or `.env` editing involved.

7. Copy the generated **Qualtrics Entry URL** or **Iframe Snippet** from the
   same page and paste it into your survey.

That's it. Every setting above can be changed later from the same page
without touching Docker at all.

## Local testing vs. real deployment

`DOMAIN` controls what Caddy does for HTTPS:

- **`localhost`** (or another private name) -- Caddy issues itself a
  self-signed certificate automatically. Your browser will warn that the
  connection isn't secure; that's expected for local testing, click through.
- **A real, DNS-resolvable domain** -- Caddy automatically gets you a real
  Let's Encrypt certificate. This needs ports 80 and 443 reachable from the
  public internet.
- **Anything else made up** (e.g. `example.com`, a domain that isn't
  actually yours) -- Caddy will try real certificate issuance, fail, and
  retry indefinitely. This is the most common reason the stack appears to
  hang on first boot. If this happens, fix `DOMAIN` in `.env` and restart:
  `docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml up -d --force-recreate caddy`.

## Admin settings reference

All under **Admin Panel > Settings > Research Embed**:

| Setting | What it does |
|---|---|
| Model | The model participants are routed to. Must also be given non-admin access in Admin Panel > Settings > Models, or participants will hit a "model not selected" error. |
| Seed Message | First message auto-sent on a participant's first visit. Leave empty to start on a blank chat instead. |
| Participant ID Parameter Name | The URL query param your survey platform uses for the participant ID (default `pid`; Qualtrics's own field is `${e://Field/ResponseID}`, already built into the generated embed URL). |
| Participant ID Format | A regex the participant ID must match before an account is created. Default matches Qualtrics Response IDs. |
| Participant Email Domain | Participant accounts are created as `{id}@this-domain` -- purely internal, used to tell participant accounts apart from staff accounts. |
| Allowed Embed Origin | Sets the `Content-Security-Policy: frame-ancestors` response header so browsers will actually render the iframe. Must match your survey platform's real domain (check your survey's share link -- some institutions are on a subdomain like `yourschool.co1.qualtrics.com`). |
| Connect Entry Service | Pushes a fresh admin API key to entry-service so it can create accounts. Safe to click again any time (e.g. after rotating keys). |

## Pre-launch checklist

- **Public sign-up is disabled by default** in this deployment
  (`ENABLE_SIGNUP=false` in `docker-compose.research-embed.yml`) so the only
  way to get an account is through the entry service. Don't override this
  unless you have a specific reason to.
- **Set the Allowed Embed Origin** to your actual survey platform's domain
  before going live -- without it, most browsers refuse to render the embed
  at all.
- **Give the model non-admin access**: Admin Panel > Settings > Models ->
  your model -> make it accessible to non-admin users (or at least to the
  participant role), or every participant will hit a "model not selected"
  error.
- **Don't set `WEBUI_AUTH_TRUSTED_EMAIL_HEADER`** for the `open-webui`
  service. If it's set, sign-in takes a different code path (trusted-header
  auth) and entry-service's participant sign-ins will fail outright.
- **If you turn on `ENABLE_API_KEY_ENDPOINT_RESTRICTIONS`** (off by
  default), add `/api/v1/auths/add` and `/api/v1/research-embed/config` to
  `API_KEY_ALLOWED_ENDPOINTS`, or entry-service's admin calls will be
  rejected.
- **Test the actual embed link** in an incognito/private window before
  sending it to real participants -- confirms a fresh browser with no
  existing session gets the intended experience.

## Data and privacy note for researchers

Once participants start using this, `entry-service`'s SQLite database and
Open WebUI's own database contain real participant identifiers, generated
credentials, and full chat transcripts. Treat this the way you'd treat any
other system storing human-subjects data:

- Check this fits your IRB protocol's data handling and retention
  requirements before collecting real data.
- Back up (or securely delete, per your protocol) the `entry-data` and
  `open-webui` Docker volumes at the end of a study.
- Both are Docker named volumes, not committed to git -- see `.gitignore`
  for what's excluded and why.

## Troubleshooting

- **Stack seems to hang on first boot** -- almost always `DOMAIN` in `.env`
  pointing at a fake/unresolvable domain. See
  [Local testing](#local-testing-vs-real-deployment) above.
- **"Model not selected" for participants** -- the model needs non-admin
  access control; see the checklist above.
- **`/enter` returns a 503 saying the instance "hasn't been connected"** --
  you haven't clicked **Connect Entry Service** yet (or need to click it
  again after recreating the entry-service container/volume).
- **`/enter` returns a 503 saying "this study isn't configured yet"** -- no
  model has been picked and saved in Admin Panel > Settings > Research
  Embed.
- **Port 80/443 already in use** (e.g. `Bind for 0.0.0.0:80 failed: port is
  already allocated`) -- something else already holds that port. Find out
  what, in order:
  1. `docker ps --filter "publish=80"` -- if this shows a container, it's a
     leftover from an earlier run that was never `down`'d. Run
     `docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml down`,
     then `up -d` again.
  2. If that's empty, it's a native process on the host, not Docker. On
     Windows (PowerShell): `Get-NetTCPConnection -LocalPort 80 | Select-Object OwningProcess`,
     then `Get-Process -Id <PID>` to see what it is (IIS / "World Wide Web
     Publishing Service" is a common default-enabled culprit). On
     Mac/Linux: `sudo lsof -i :80`.
  3. Stop or disable whatever that turns out to be, or -- only if you
     already have another reverse proxy fronting this host -- remap the
     ports in `docker-compose.research-embed.yml` and forward from your
     existing proxy instead (see the comment above the `caddy` service in
     that file). Don't do this for a real deployment relying on Caddy's
     automatic HTTPS: the Let's Encrypt HTTP-01 challenge needs Caddy
     reachable on the real port 80.

## Customizing / running your own fork

If you fork this repo to make your own changes (custom branding, additional
logic, etc.):

1. Enable GitHub Actions on your fork.
2. Push to your fork's `main` branch. `.github/workflows/research-embed-images.yml`
   automatically builds and publishes your own images to
   `ghcr.io/<your-username>/<your-repo-name>` and `-entry`.
3. **One-time step:** GHCR packages default to private even in a public
   repo. Go to your GitHub profile/org -> Packages -> select each of the two
   new packages -> Package settings -> Change visibility -> Public.
4. Update the `image:` lines in `docker-compose.research-embed.yml` to point
   at your own images instead.

Alternatively, skip GHCR entirely and build locally:

```
docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml build
```

## Key files, if you're modifying this

- `entry-service/entry_service.py` -- the standalone entry service.
- `backend/open_webui/routers/research_embed.py` -- the admin-configurable
  settings API this fork adds.
- `src/lib/components/admin/Settings/ResearchEmbed.svelte` -- the admin UI
  for the above.
- `src/routes/(app)/+layout.svelte` and `src/lib/components/chat/Chat.svelte`
  -- where chat-only mode (hiding the sidebar/navbar/settings) is
  implemented, gated on participant email domain.
- `docker-compose.research-embed.yml`, `Caddyfile.production` -- the
  deployment topology.
- `futurefeature.md` -- a written-up, not-yet-implemented proposal for
  per-model embed configuration (running multiple studies/conditions on one
  instance).
