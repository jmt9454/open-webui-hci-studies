# Research Embed: Setup & Researcher Guide

This fork of [Open WebUI](https://github.com/open-webui/open-webui) adds a
**chat-only mode** you can embed directly in a Qualtrics survey (or any
platform that can host an iframe or a link). Each participant who opens the
embed link automatically gets their own account and a single chat, with no
sidebar, no settings, and no way to see or reach anything else in the app.

This document is the setup and operating guide. If you just want to get a
study running, start at [Quickstart](#quickstart) below.

## How it works, briefly

Two required pieces, plus an optional third for routing/HTTPS:

- **Open WebUI** (this fork) -- the actual chat app. Runs in "chat-only"
  mode for participant accounts, detected by email domain.
- **entry-service** -- a small standalone service. When a participant opens
  `/enter?pid=...`, it creates (or looks up) that participant's account,
  signs them in, and redirects them straight into their one chat. It talks
  to Open WebUI only through its normal public REST API.
- **Caddy** (optional, `docker-compose.caddy.yml`) -- reverse proxy, routes
  `/enter*` to entry-service and everything else to Open WebUI, and gets you
  a real HTTPS certificate automatically once you have a real domain. Skip
  this file if you already run your own reverse proxy (nginx-proxy-manager,
  Traefik, another Caddy instance, a cloud load balancer) -- see
  [Already running your own reverse proxy?](#already-running-your-own-reverse-proxy)
  below.

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
- Either ports 80 and 443 free on the host (for the built-in Caddy reverse
  proxy), **or** your own reverse proxy already running -- see
  [Already running your own reverse proxy?](#already-running-your-own-reverse-proxy).

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

3. Bring the stack up. This pulls prebuilt images (no local build, no wait).

   If this host has no reverse proxy already running on it (the common
   case for a fresh VM), include `docker-compose.caddy.yml` and Caddy
   handles HTTPS for you automatically:

   ```
   docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml -f docker-compose.caddy.yml up -d
   ```

   If this host already runs its own reverse proxy (nginx-proxy-manager,
   Traefik, another Caddy instance, a cloud load balancer -- common on a
   shared or homelab-style server), omit that third file instead and see
   [Already running your own reverse proxy?](#already-running-your-own-reverse-proxy)
   below for how to wire it up:

   ```
   docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml up -d
   ```

   If you've brought this stack up before (e.g. while testing), run `down`
   first as a habit -- it's a no-op if nothing's running, and it prevents the
   most common self-inflicted cause of a port-already-in-use error (a
   leftover container from an earlier run that was never torn down):

   ```
   docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml -f docker-compose.caddy.yml down
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

This section applies when you're using `docker-compose.caddy.yml` (the
built-in reverse proxy). If you're fronting this with your own reverse
proxy instead, see
[Already running your own reverse proxy?](#already-running-your-own-reverse-proxy)
-- your proxy's own HTTPS/domain settings apply there, not `DOMAIN` below.

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
  `docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml -f docker-compose.caddy.yml up -d --force-recreate caddy`.

## Already running your own reverse proxy?

If this host already has a reverse proxy on ports 80/443 -- nginx-proxy-manager,
Traefik, another Caddy instance, a cloud load balancer -- skip
`docker-compose.caddy.yml` entirely:

```
docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml up -d
```

Without Caddy in the mix, `open-webui` and `entry-service` each publish a
plain-HTTP port straight to the host instead:

- `open-webui` on `${OPEN_WEBUI_PORT-3000}` (from `docker-compose.yaml`)
- `entry-service` on `${ENTRY_SERVICE_PORT-9000}` (from
  `docker-compose.research-embed.yml`)

Point your existing reverse proxy at those, splitting by path the same way
`Caddyfile.production` does: `/enter*` goes to entry-service, everything
else goes to open-webui. Your proxy handles TLS/HTTPS itself in this setup
(`DOMAIN` and Caddy's automatic Let's Encrypt cert are irrelevant here --
don't set `DOMAIN` unless you also bring `docker-compose.caddy.yml` back).

**Example: nginx-proxy-manager.** Create one Proxy Host for your domain
pointing at `open-webui`'s host and port (Forward Hostname/IP = your
server's address, Forward Port = `3000`). Then add a Custom Location on
that same Proxy Host for path `/enter` (with "include subdirectories" /
a trailing wildcard, depending on your NPM version) forwarding to the same
host on port `9000`. Enable SSL on the Proxy Host as you would for any other
service -- NPM's own Let's Encrypt integration handles the certificate; you
don't need Caddy for that.

The CSP header that lets the embed render inside your survey platform's
iframe (`Content-Security-Policy: frame-ancestors`) is set by Open WebUI's
own backend based on the **Allowed Embed Origin** setting in Admin Panel >
Settings > Research Embed, not by Caddy -- so this works identically whether
Caddy, your own reverse proxy, or no proxy at all (local testing) is in
front.

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
  1. `docker ps --filter "publish=80"` -- if this shows a container, check
     what it is. If it's a leftover `caddy` container from an earlier run of
     *this* stack that was never `down`'d, run
     `docker compose -f docker-compose.yaml -f docker-compose.research-embed.yml -f docker-compose.caddy.yml down`,
     then `up -d` again. If it's something else entirely (nginx-proxy-manager,
     Traefik, another Caddy instance, etc.) -- that's a real, permanent
     reverse proxy already running on this host, not a leftover. Don't fight
     it for the port: skip `docker-compose.caddy.yml` and follow
     [Already running your own reverse proxy?](#already-running-your-own-reverse-proxy)
     instead.
  2. If step 1 shows nothing, it's a native process on the host, not Docker.
     On Windows (PowerShell):
     `Get-Process -Id (Get-NetTCPConnection -LocalPort 80).OwningProcess`
     (IIS / "World Wide Web Publishing Service" is a common default-enabled
     culprit). On Mac/Linux: `sudo lsof -i :80`. Stop or disable whatever
     that turns out to be, or free up 80/443 for Caddy some other way.
- **`Conflict. The container name "/open-webui" is already in use`** -- a
  container literally named `open-webui` already exists on this host. This
  is common on a shared research/lab server that already runs a
  general-purpose Open WebUI instance -- `open-webui` is upstream's own
  default container name too. Check what it is first:
  `docker ps -a --filter "name=open-webui" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"`.
  If you can't remove it, set `OPEN_WEBUI_CONTAINER_NAME` in `.env` (see
  `.env.research-embed.example`) instead of renaming or removing the
  existing container. Also check its `Ports` column -- if it's already
  using host port 3000, set `OPEN_WEBUI_PORT` in `.env` too, or `up -d` will
  just trade this error for a port-already-allocated one.

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
- `docker-compose.research-embed.yml` -- open-webui + entry-service topology
  (required). `docker-compose.caddy.yml` -- the optional built-in reverse
  proxy, separate so it can be skipped if you already run your own (see
  [Already running your own reverse proxy?](#already-running-your-own-reverse-proxy)).
  `Caddyfile.production` -- Caddy's routing config, only relevant if you're
  using `docker-compose.caddy.yml`.
- `futurefeature.md` -- a written-up, not-yet-implemented proposal for
  per-model embed configuration (running multiple studies/conditions on one
  instance).
