# AGENTS.md — uff-agent conventions

Engineering conventions for this repository. Product/landing copy lives in the
`uff` monorepo, not here.

## Golden rule: no hardcoded values

Every configuration value lives in one of three places:

| Layer | File | Example |
|---|---|---|
| Runtime env | `.env.example` (dev) / `docker-compose.yml` (self-host) | `USER_LANGUAGE=pl` |
| Config YAML | `config.yaml` | `language: ${USER_LANGUAGE}` |
| Python fallback | `src/config.py` `_from_env()` | `_e("USER_LANGUAGE")` |

Never write a value directly into `config.yaml` — always through `${ENV_VAR}`.
`_from_env()` is only used when there is no `config.yaml`.

Unresolved `${VAR}` placeholders are tolerated by `as_int()` for numeric
settings, but string settings should always be provided by the deployment.

## `src/` layers

| Layer | Location | Responsibility |
|---|---|---|
| LLM / vendor | `src/llm/` | OpenRouter client, embeddings |
| Mail / vendor | `src/mail/` | parser (pure), IMAP listener, SMTP sender, `+persona` router |
| Storage | `src/storage/` | thread, memory, memory_search, index (RAG), tasks, background_task, script_runner, attachment, housekeeping |
| Tools | `src/tools/` | skill framework (`BaseSkill`, `SkillRegistry`) + MCP |
| Prompts | `src/prompts/` | thread summaries, dream cycle, document analysis |
| Orchestration | `src/runtime/` | `app.py` (Runtime), `runner.py`, `dispatcher.py`, `scheduler.py`, `handlers/*`, `server/*` |
| Bootstrap | `src/bootstrap.py` | `.env` loading, data directories, welcome email |

Key primitives:

- **`Runtime`** (`src/runtime/app.py`) — the composition root. All dependencies
  (llm, queue, stores, skills, mcp, smtp, listener) live on one object.
  `Runtime.build()` creates them; `process_event(event, rt)` consumes them.
  **Do not pass 10+ parameters — pass `rt`.**
- **`AgentRunner`** (`src/runtime/runner.py`) — the single "LLM ⇄ tool-calls"
  loop. `.run()` returns a `RunResult`. Do not duplicate this loop in handlers.
- **Event handler registry** (`src/runtime/handlers/__init__.py`) — maps event
  type → handler. A new event type = a new handler + one entry in the dict.
- **`RequestContext` / `get_data_root()`** (`src/thread_ctx.py`) — per-request
  context via `activate(ctx)`. Skills get their data root through
  `get_data_root()`; **never read `AGENT_HOME` from the environment in a skill**.

## Prompt language

- All prompts and instructions in code (system prompts, skill descriptions and
  parameters) are **in English**.
- The language the user sees is controlled solely by the system prompt
  (`src/context.py`, `Language: {USER_LANGUAGE}`), never hardcoded in a prompt.
- Exception: text sent directly to the user (quiet-hours auto-reply, progress,
  the "Sources" header) is localized via `config.user_language`.
- Values returned by skills (`execute()` → string) are tool results that the LLM
  paraphrases into the user's language — they may stay neutral.

## Skills

Each skill is a directory under `skills/<name>/` containing `tool.py`
(one or more `BaseSkill` subclasses) and optionally `SKILL.md` and
`requirements.txt`. `SkillRegistry` loads them at startup and exposes the tools
selected by each agent profile in `agents/<name>/config.yaml`.

## Adding a new configuration variable

1. `config.yaml` — add `${NEW_VAR}` (no value, no fallback).
2. `src/config.py` — add `_e("NEW_VAR")` in `_from_env()` (no second argument).
3. `docker-compose.yml` and `.env.example` — document it with an example value.

When a variable is consumed by the hosted deployment as well, the matching
Ansible wiring lives in the `uff` monorepo (`provisioner/`).

## Naming

| Context | Convention | Example |
|---|---|---|
| Env var | `UPPER_SNAKE_CASE` | `EMAIL_OWNER` |
| Python module/function | `snake_case` | `build_context` |
| Skill directory | `snake_case` | `data_analysis` |

## Tests

`pytest -q`. Storage and parsing are pure and cheap to test; prefer those over
network-dependent tests.
