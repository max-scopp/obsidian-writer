# obsidian-writer

A small FastAPI service that exposes a vault of Markdown files (typically an
[Obsidian](https://obsidian.md/) vault mounted over NFS) to AI agents.

The service provides:

- **Read**: search, list folders, read individual notes, read the agent's
  learned structure cache.
- **Write**: create notes, append to notes, patch frontmatter.
- **Soft-delete**: move notes into a dated `.trash/` directory.
- **Structure**: a persistent `.obsidian-map.yaml` at the vault root that the
  agent reads and updates. **No fixed folder schema is enforced** — the agent
  learns your structure as your life changes.

Designed for **server-side tool dispatch** from an LLM proxy such as
[LiteLLM](https://github.com/BerriAI/litellm) (see
[`obsidian-litellm-tools`](https://github.com/max-scopp/obsidian-litellm-tools)),
but the HTTP API is plain JSON and works with any agent that can make HTTP
calls.

---

## What this service deliberately does NOT do

- **No fixed allowlist of folders.** The agent can write anywhere under the
  vault root. Path traversal is blocked (`..` resolution must stay inside the
  vault); nothing else is.
- **No automatic folder creation on read.** New folders are only created when
  an explicit write operation targets them.
- **No validation of Markdown syntax.** Obsidian owns that.
- **No plugin execution, no image embedding, no binary attachments.**
- **No permanent delete.** A `propose_delete` operation moves the note to
  `.trash/YYYY-MM-DD/<basename>` with a sidecar `meta.md`. The user reviews
  and bulk-deletes from Obsidian.
- **No background polling, no daemon, no scheduler.** It is a request/response
  HTTP service.

---

## Architecture in one paragraph

The writer is mounted read-write at the vault root. Every write goes through
a single `atomic_write()` helper that writes `path.tmp`, `fsync(2)`s, and
`rename(2)`s onto the final path — atomic on NFS-over-ZFS. Path safety is
enforced by `safe_resolve()` which canonicalises the requested path and
rejects anything whose resolved location falls outside the vault root. A
rate limiter (per `Authorization` token) caps burst and daily volume. The
agent's knowledge of the vault structure lives in `.obsidian-map.yaml`,
cooperatively maintained by the agent through the `obsidian_map` endpoint.

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/healthz` | Liveness probe. Returns `{"status":"ok","vault":<mount>}` |
| `GET`  | `/note` | Read a single note by vault-relative path |
| `GET`  | `/list` | List a folder's immediate children (notes + subfolders) |
| `GET`  | `/search` | Title/tag/path substring search across the vault |
| `POST` | `/create` | Create a new note with generated frontmatter |
| `POST` | `/append` | Append a Markdown section to an existing note |
| `PATCH` | `/frontmatter` | Patch typed frontmatter fields on an existing note |
| `POST` | `/trash` | Soft-delete (move to `.trash/YYYY-MM-DD/`) |
| `GET`  | `/map` | Read `.obsidian-map.yaml` |
| `PATCH` | `/map` | Patch `.obsidian-map.yaml` (merge / replace) |

All write endpoints return `409 Conflict` if the target path already exists
(where applicable), `403 Forbidden` if path traversal is attempted, `413` if
the body exceeds the configured limit, and `429` if the rate limit is hit.

---

## Configuration

Environment variables (all have defaults):

| Variable | Default | Notes |
|---|---|---|
| `OBSIDIAN_VAULT_PATH` | `/vault` | Absolute path of the mounted vault root |
| `OBSIDIAN_WRITER_TOKEN` | _(required)_ | Bearer token for write endpoints |
| `OBSIDIAN_WRITER_READ_TOKEN` | same as above | Bearer token for read endpoints |
| `OBSIDIAN_MAX_BODY_BYTES` | `262144` (256 KiB) | Per-request body limit |
| `OBSIDIAN_RATE_PER_MIN` | `30` | Max write ops per minute per token |
| `OBSIDIAN_RATE_PER_DAY` | `200` | Max write ops per day per token |
| `OBSIDIAN_LOG_LEVEL` | `INFO` | Standard log levels |

The two tokens exist so a read-only client (search UI, etc.) can use
`OBSIDIAN_WRITER_READ_TOKEN` while the LLM agent uses the write token.

---

## Running

```bash
# Local
pip install -e .
uvicorn obsidian_writer.app:app --host 0.0.0.0 --port 4040

# Container
docker compose up --build
```

The vault mount lives outside the image — provide it via a bind mount:

```yaml
volumes:
  - /mnt/obsidian:/vault:rw
```

---

## Development

```bash
make install   # editable install + dev deps
make test      # pytest with the suite under tests/
make lint      # ruff + mypy
```

Tests use a tmp-path fixture and never touch a real vault.

---

## Related

- [`obsidian-litellm-tools`](https://github.com/max-scopp/obsidian-litellm-tools) —
  the LiteLLM proxy plugin that registers the agent-facing tool set and
  dispatches tool calls to this service.
