# Deploying Ranti

## Status: the Hugging Face recommendation below is out of date

Hugging Face now refuses to create a Docker or Gradio Space on the free
`cpu-basic` hardware, returning HTTP 402. Its own error text is: "Static Spaces
are free for everyone, but hosting Gradio and Docker Spaces on free cpu-basic
requires a PRO subscription." A Static Space cannot run this Python service, so
the Hugging Face path is no longer free.

The free replacement is a Render web service, described under the fallback
section below. It needs no credit card. Its one real drawback is that it sleeps
after roughly 15 idle minutes, which a scheduled ping against `/health` fixes;
750 instance-hours per month is enough to stay up continuously. Note that a
webhook bot on a sleeping host cold-starts on the first message, so the ping
matters rather than being optional.

The rest of this document is kept because the reasoning still holds for anyone
who already has Hugging Face PRO.

The originally recommended host was a Hugging Face Docker Space. The fallbacks
below are listed with their real tradeoffs, not as equals.

## Originally recommended: Hugging Face Docker Space

Why this one:

- Free CPU tier, no credit card.
- Builds the repository [Dockerfile](../Dockerfile) as-is, so the runtime is
  identical to local.
- Gives a public HTTPS URL, which Telegram requires for a webhook.
- The process can stay always-on, so there is no cold-start gap between a
  Telegram message and the bot.

### 1. Create the Space

Create a new Space with **SDK: Docker** and the free CPU hardware. The Space
repository must contain the project, including `Dockerfile`, `pyproject.toml`
and `src/`.

### 2. Add the Space frontmatter

The Space serves its landing page from the repository `README.md`, and Hugging
Face reads configuration from YAML frontmatter at the very top of that file.
The required block is exactly:

```yaml
---
title: Ranti
sdk: docker
app_port: 7860
---
```

`app_port: 7860` must match the port in the Dockerfile `CMD`. The frontmatter
must be the first thing in the file, before any other content.

### 3. Set the secrets

In the Space settings, under **Variables and secrets**, add the following.
Secrets are the values that must not appear in the repository.

Required for real Walrus Memory:

- `MEMWAL_PRIVATE_KEY` - the delegate key from https://memory.walrus.xyz
- `MEMWAL_ACCOUNT_ID` - the Walrus Memory account id

Required for the LLM failover chain (at least one):

- `GROQ_API_KEY` - primary provider (`GROQ_MODEL` defaults to `qwen3-32b`)
- `GEMINI_API_KEY` - secondary provider
- `OLLAMA_BASE_URL` - only useful if an Ollama instance is reachable

Required for the Telegram surface:

- `TELEGRAM_BOT_TOKEN` - from @BotFather
- `TELEGRAM_WEBHOOK_SECRET` - any shared secret; it also appears in the path
- `PUBLIC_BASE_URL` - the Space URL, for example
  `https://<user>-<space>.hf.space`

Optional overrides:

- `MEMWAL_SERVER_URL` (default `https://relayer.memory.walrus.xyz`)
- `MEMWAL_NAMESPACE_PREFIX` (default `ranti`)
- `GROQ_MODEL`, `GEMINI_MODEL`, `OLLAMA_MODEL`
- `RANTI_ENVIRONMENT` (set to `production`)

The image already sets `RANTI_DATABASE_PATH=/app/artifacts/ranti.db`. If you
prefer `/tmp`, override it. The location does not matter: the file is
disposable.

### 4. Register the webhook

Once the Space is running and `PUBLIC_BASE_URL` points at it:

```bash
python scripts/register_telegram_webhook.py
```

Run it from a checkout whose `.env` has the same
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` and `PUBLIC_BASE_URL` values as
the Space, or export those three variables first.

## Fallback: Render free web service

A Render free web service can build the same Dockerfile and terminates HTTPS for
you.

- No credit card required.
- Sleeps after 15 minutes idle. The next Telegram message triggers a cold start
  that can take about a minute, and a webhook POST that arrives during the
  wake-up can be dropped.
- Needs an external ping to stay warm, for example a scheduled GitHub Action or
  a free uptime pinger hitting `/health` every 10 minutes. That is a workaround,
  not a guarantee.
- Ephemeral disk, so a redeploy loses the local index.
- Render injects `PORT`; override the start command with
  `uvicorn main:app --host 0.0.0.0 --port $PORT --app-dir src`, or keep the
  Docker `CMD` and set the service to route to port 7860.

Choose this only if a small chance of a missed first message is acceptable.

## Fallback: Oracle Cloud Always Free

An Always Free VM (Ampere ARM or the x86 micro shape) is genuinely always-on and
the most durable option.

- A credit card is required at signup, even though the Always Free resources are
  not billed. This is the main reason it is not the recommended path.
- You manage the machine: systemd for the app, and Caddy or nginx with Let's
  Encrypt for the HTTPS URL Telegram needs.
- The host filesystem persists, so the local index survives restarts. That is a
  convenience, not a requirement.

## Ephemeral disk is acceptable

The local SQLite file is a rebuildable index, not the source of truth. Facts and
the portable consolidation index live in Walrus Memory, and
`GET /memories/{user_id}/passport` exports a bundle that can be re-imported into
a fresh instance. A host that wipes its disk on every redeploy loses the counters
until the index is rebuilt, but no memory is lost.

## Local development only

Running the API locally and exposing it through a tunnel (for example
`cloudflared` or `ngrok`) works for a short demo, but the tunnel URL changes and
the tunnel must stay up, so it is not a deployment target.
