# uff-agent

An email-first AI assistant you run yourself. It has its own email address; you
send it work by email and the answer comes back in the same thread. One Docker
container, no database, no dashboard — its entire state is a folder you can back
up with `tar`.

This is the same agent that powers [uff.email](https://uff.email), packaged for
self-hosting.

```
you ──email──▶ assistant@yourdomain.com ──▶ [ agent ] ──▶ OpenRouter (LLM)
                                              │
              ◀──────reply in the thread──────┘        brain/ (memory, threads, index)
```

- **Email is the interface.** No app, no chat, no API to learn.
- **Your data stays put.** Everything it knows lives in `./brain` on your machine.
- **One address, one owner.** Only the address you whitelist can talk to it.
- **Background work.** Reminders, recurring reports and monitoring keep running.
- **~50 skills.** Web, files, RAG, an entity/knowledge graph, scripts, MCP tools.

## Quick start

```bash
mkdir -p uff-agent/brain && cd uff-agent
curl -O https://raw.githubusercontent.com/worotyns/uff-agent/main/docker-compose.yml
# open docker-compose.yml and fill in every value under `environment:`
docker compose up -d
docker compose logs -f
```

Then send an email **from the address in `EMAIL_OWNER`** to the agent's address,
e.g. *"Summarize this thread and list the next steps."* Mail from any other
address is ignored by design.

Prefer a full walkthrough (including running your own mail server)?
See **https://uff.email/self-host.html**.

## Configuration

Everything is set in the `environment:` block of `docker-compose.yml`. There is
no separate `.env` file to manage — though plain environment variables work too
if you prefer `docker run` or a platform like Fly.io.

### Required

| Variable | Description | Example |
|---|---|---|
| `OPENROUTER_KEY` | LLM API key ([openrouter.ai/keys](https://openrouter.ai/keys)) | `sk-or-v1-…` |
| `OPENROUTER_MODEL` | Default model | `deepseek/deepseek-v4.1-flash` |
| `ASSISTANT_MODEL` | Model for the `assistant` persona | `deepseek/deepseek-v4.1-flash` |
| `RESEARCHER_MODEL` | Model for the `researcher` persona | `deepseek/deepseek-v4-pro` |
| `VISION_MODEL` | Model for images/OCR | `google/gemini-3.1-flash-lite` |
| `IMAGE_GEN_MODEL` | Model for image generation | `google/gemini-3.1-flash-lite-image` |
| `TTS_MODEL` | Text-to-speech model | `openai/gpt-4o-mini-tts-2025-12-15` |
| `TTS_VOICE` | Text-to-speech voice | `nova` |
| `EMAIL_USER` | The agent's **own, dedicated** mailbox | `assistant@yourdomain.com` |
| `EMAIL_PASSWORD` | App password for that mailbox | `abcd efgh ijkl mnop` |
| `EMAIL_IMAP_HOST` | IMAP server | `imap.yourdomain.com` |
| `EMAIL_IMAP_PORT` | IMAP port (implicit TLS) | `993` |
| `EMAIL_SMTP_HOST` | SMTP server | `smtp.yourdomain.com` |
| `EMAIL_SMTP_PORT` | SMTP port | `465` (SSL) or `587` (STARTTLS) |
| `EMAIL_OWNER` | **Your** address — the only one allowed to talk to the agent | `you@yourdomain.com` |
| `USER_LANGUAGE` | Reply language | `pl` or `en` |
| `USER_TIMEZONE` | Your timezone | `Europe/Warsaw` |

### Optional

| Variable | Default | Description |
|---|---|---|
| `AGENTS` | `assistant,researcher,sources` | Personas. Reach them via plus-addressing. |
| `EMAIL_DELETE_AFTER_READ` | `true` | Delete from the agent inbox after processing. |
| `WELCOME_EMAIL` | `true` | Send a first-launch email on startup (doubles as an SMTP smoke test). |
| `MCP_ENABLED` | `true` | Allow remote MCP tool servers, added per-user by email. |
| `ADMIN_NOTIFY_EMAIL` | *(empty)* | Where credit-limit alerts go. Empty = log only. |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | *(empty)* | Browser Run for `browser_*` skills. |
| `EMAIL_SIGNATURE` | *(empty)* | Extra text appended to outgoing mail. |
| `HEARTBEAT_INTERVAL` | `300` | Seconds between housekeeping ticks (no LLM calls). |
| `DREAM_HOUR` | `03:00` | When nightly memory consolidation runs (UTC). |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

### The two addresses

This is the part worth getting right:

- **`EMAIL_USER`** is the agent's own mailbox. Create a **separate, dedicated**
  address for it. Never point this at your personal inbox.
- **`EMAIL_OWNER`** is your personal address. It is the **only** sender the
  agent will process. Everything else is logged and dropped. That single line is
  the entire access-control model.

## How it works

```
IMAP listener ──▶ event queue ──▶ agent loop ──▶ OpenRouter ──▶ SMTP reply
                                    │
                          scheduler (heartbeats, reminders,
                          scripts, nightly memory consolidation)
```

- **State is files.** YAML for metadata, JSONL for messages, Markdown for memory.
  No database.
- **The LLM is only called for real work.** Heartbeats and housekeeping are free.
- **Plus-addressing** picks a persona: `assistant+researcher@…` routes to the
  `researcher` profile (different model, different skills).

### Memory

| Path | Contents |
|---|---|
| `brain/memory/*.md` | Daily logs, consolidated facts and decisions |
| `brain/memory/entities/graph.jsonl` | Entity/knowledge graph (people, companies, documents, relations) |
| `brain/threads/` | Per-thread history and summaries |
| `brain/index/` | Chunked + embedded documents for semantic search |

`memory_search` uses [ripgrep](https://github.com/BurntSushi/ripgrep) (bundled
in the image) for full-text recall; `search_documents` uses embeddings via
OpenRouter.

## Skills

The agent ships ~50 tools, grouped by skill: `email`, `tasks`, `terminal`,
`scripts`, `speech`, `rag`, `files`, `web`, `ontology`, `image_gen`, `browser`,
`send_progress`, `task_system`, `data_analysis`, `create_attachment`,
`send_document`, `mcp`, `memory`. Each skill lives in `skills/<name>/` with a
`tool.py` and, usually, a `SKILL.md`.

You can also connect remote **MCP** servers by simply asking the agent — no code.

## Where to run it

It is a single stateful container; anything that gives you a volume will do.

**VPS (Docker Compose)**

```bash
git clone https://github.com/worotyns/uff-agent && cd uff-agent
# edit docker-compose.yml
docker compose up -d
```

**Fly.io**

```bash
fly launch --no-deploy
fly volumes create uff_brain --size 1
# mount the volume at /app/brain in fly.toml, set the env vars as secrets
fly deploy
```

**Home server / NAS (Umbrel, CasaOS, …)** — any compose-based platform can run
the bundled `docker-compose.yml` as-is.

No inbound ports are needed anywhere: the agent only makes outbound connections.

## Updating

```bash
docker compose pull && docker compose up -d
```

`brain/` is preserved across updates and is the only thing worth backing up:

```bash
tar czf uff-brain-$(date +%F).tar.gz brain/
```

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# smoke test without a mailbox
export OPENROUTER_KEY=sk-or-v1-...
python3 -c "
from src.event import Event, EventQueue
EventQueue('events').push(Event(type='manual.test', agent='assistant', message='Say hello'))
"
python3 main.py
```

Configuration is read from `config.yaml` with `${ENV_VAR}` interpolation; if no
`config.yaml` is present it falls back to environment variables. See
[`AGENTS.md`](AGENTS.md) for the engineering conventions.

```bash
pytest -q
```

## License

[Elastic License 2.0](LICENSE) — you may use, modify and self-host this software
freely, including for commercial purposes inside your own organization. You may
**not** offer it to third parties as a hosted or managed service that
substantially reproduces its features. In short: run it for yourself; don't
resell it as a service.

---

Built for [uff.email](https://uff.email). Self-hosting guide:
<https://uff.email/self-host.html>.
