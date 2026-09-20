# Agent API and network access

This app began as a personal archive served at `http://<host>:7030` with **no
authentication on any route** — including `DELETE /api/analyses/{id}`. The
following changes close that exposure and add a stable contract for scripts and
agents.

Two surfaces now exist:

| Surface | Purpose | Contract stability |
|---|---|---|
| `/api/*` | The UI's own internal contract. The UI calls it over HTTP from the server side. | Free to change with the UI |
| `/api/agent/v1/*` | For scripts, agents, and anything outside the UI. | Versioned, documented here |

The UI's routes were **not** modified: no response shape, no behavior. Existing
clients of `/api/*` keep working.

---

## 1. Network access

### Fail-closed by default

The app **refuses to start** when no token is configured:

```
Refusing to start without authentication.
Set APP_AUTH_TOKEN to a long random string, for example:
  APP_AUTH_TOKEN=$(openssl rand -hex 32)
Or, to serve the app without authentication, set APP_AUTH_DISABLED=1.
```

Generate a token once and put it in `.env`:

```bash
openssl rand -hex 32
```

```dotenv
APP_AUTH_TOKEN=<the generated value>
```

`APP_AUTH_DISABLED=1` bypasses the guard. Use it only for a host that is
genuinely unreachable from other machines.

### Authenticating

| Client | Mechanism |
|---|---|
| Browsers | HTTP Basic — any username, the token as the password. The server then sets an HttpOnly cookie for the websocket. |
| Scripts / agents | `Authorization: Bearer <token>` |

`/health` stays exempt from authentication so container and uptime checks keep
working.

### Why a cookie is involved

Browsers cannot attach custom headers to a WebSocket handshake, and the NiceGUI
UI needs its websocket. So a successful header authentication also sets
`ytsv_ws_auth` — an HMAC of the token, **not the token itself** (a stolen cookie
does not reveal the credential). That cookie is accepted **only** for websocket
upgrades; presenting it to a REST route returns 401. Replay value is therefore
limited to keeping one browser's websocket alive.

### What is protected

Every HTTP route and the websocket. That includes the UI's own routes, so the
previously unauthenticated `DELETE` is no longer reachable from the LAN.

The app's own server-side calls to its own API now send the bearer header
(`app/auth.py::internal_headers`), so the UI keeps working unchanged.

---

## 2. Agent API

Base path: `/api/agent/v1`. All routes require the token.

| Route | Method | Input | Output |
|---|---|---|---|
| `/health` | GET | — | `{status, version, total_active}` |
| `/analyses` | GET | `limit` (1–200, default 20), `offset` (≥0), `q`, `order` (`created_at`\|`updated_at`) | Envelope + metadata only |
| `/analyses` | POST | `{url, llm_enabled?}` | `202 queued` / `200 existing` / `200 needs_regeneration` |
| `/analyses/{id}` | GET | `fields=` whitelist | Metadata + requested bodies |
| `/analyses/{id}/status` | GET | — | Compact status for polling |
| `/analyses/{id}/summary` | GET | — | `summary_short`, `summary_structured` |
| `/analyses/{id}/translation` | GET | `format=json\|text` | Translation pane |
| `/analyses/{id}/transcript` | GET | `format=json\|text` | Original transcript |
| `/videos/{video_id}` | GET | — | Active revision for that video |

### Why the list is compact

`GET /api/analyses` returns `SELECT *`, so every row carries its full transcript,
translation, and structured summary. Measured against the live archive
(75 active rows, 2026-09-20):

| Request | `/api/analyses` | `/api/agent/v1/analyses` |
|---|---|---|
| `limit=1` | 69,880 B | ~645 B/row |
| `limit=20` | 2,298,405 B | ~13 KB |
| 75 rows (all) | **8,061,903 B** | **~48 KB** |

The agent list omits `transcript`, `transcript_ko`, and `summary_structured`, and
adds derived flags so a caller can decide what to fetch next without downloading
anything:

`has_transcript`, `has_summary`, `transcript_chars`, `translation_chars`,
`translation_state`

Bodies are available through the single-item routes, which take an explicit id.

### Envelope

```json
{"total": 75, "limit": 20, "offset": 0, "items": [ ... ]}
```

`total` counts rows matching the same filter as `items` (the UI's endpoint
returns a bare array with no count at all).

### Stable identifier

Use **`video_id`**, not `id`. Regeneration and translation resume insert a new
row with a new `id`; `video_id` is stable across revisions and `/videos/{video_id}`
resolves the currently active one.

### Errors

```json
{"error": {"code": "invalid_parameter", "message": "...", "detail": {}}}
```

| Code | Status | Meaning |
|---|---|---|
| `disabled` | 503 | No `APP_AUTH_TOKEN` configured |
| `unauthorized` | 401 | Missing or wrong token |
| `not_found` | 404 | Unknown id, or no active revision for that video |
| `invalid_url` | 400 | URL is not a recognizable YouTube link |
| `invalid_parameter` | 400 | Out-of-range or unknown parameter |
| `queue_full` | 429 | Too many jobs already queued |
| `parse_error` | 422 | A stored body is not valid JSON |

### Registration never regenerates

`POST /analyses` accepts only `url` and `llm_enabled`. Any other field — notably
`force` — is rejected with 400 rather than ignored.

| Situation | Result |
|---|---|
| New URL | `202 queued` |
| Already queued, running, or completed | `200 existing`, **pipeline not started** |
| Recently failed for a transient reason | `200 cooldown` |
| Failed, or completed with no transcript | `200 needs_regeneration`, **pipeline not started** |

A retrying agent therefore cannot multiply paid LLM calls or pile up revisions.
Regeneration stays a human action in the UI, where it creates a new inactive
revision.

### Deletion is not exposed

There is no delete route on the agent surface; the path returns 405. Deletion
remains UI-only because `delete_analysis` removes **every revision of that
video_id** at once and the repository has no restore function — a soft delete
that is not, in practice, reversible.

### Korean source transcripts

Korean source is never translated (invariant LLM-005). Three rows in the live
archive predate that rule and still have a stored `transcript_ko`; the UI hides
them. The agent surface hides them too, so the rule does not split between
surfaces:

```json
{
  "transcript_lang": "ko",
  "translation_state": "not_applicable_korean_source",
  "display": "- BLANK -",
  "entries": []
}
```

`translation_state` values:

| Value | Condition |
|---|---|
| `not_applicable_korean_source` | `transcript_lang` starts with `ko` |
| `translated` | Non-Korean, has translation entries |
| `pending` | Non-Korean, in progress, nothing yet |
| `partial` | Non-Korean, in progress, some entries present |
| `unavailable` | Completed, but no translation exists — reachable because the pipeline swallows translation errors |
| `failed` | Row status is `failed` |
| `parse_error` | Stored body is not valid JSON |
| `unknown` | Anything else; the raw `status` is returned alongside |

The final `unknown` branch is deliberate: a future status will not break callers.

One asymmetry to know about: in list responses the state is derived from a stored
character count, so a corrupt body reads as present rather than `parse_error`.
The single-item routes parse the body and report `parse_error` accurately.

---

## 3. Examples

```bash
TOKEN=<your token>
BASE=http://localhost:7030/api/agent/v1

# Is it up, and how many records?
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/health"

# Ten most recent
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/analyses?limit=10"

# Register a URL
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}' "$BASE/analyses"

# Summary of one video
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/videos/dQw4w9WgXcQ/summary"

# Translation as plain text
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/analyses/<id>/translation?format=text"
```

---

## 4. Backup risk this change does not fix

`DB_BACKUP_DIR` defaults to `data/backups`, which sits **inside** the same bind
mounted directory as the database. A host disk failure, a stray `rm -rf data`,
or filesystem corruption takes both at once.

The app never deletes backups (invariant DATA-010), so an off-volume copy is a
host operation. Adjust the path to your own checkout:

```bash
# On the host, outside the container's data volume
rsync -a <compose-dir>/data/backups/ /path/to/other-disk/ytsv-backups/
```

Point `DB_BACKUP_DIR` at another disk if one is available, and confirm a restore
actually works — an unverified backup is not a backup.

This is a **host-level hardening item, not a blocker for the current change.**
Keeping the copy under `data/` is fine while the app is still being improved;
revisit it before the archive matters enough to lose.

Also note: `docker compose down -v` does **not** delete this data (it is a bind
mount, not a named volume). Converting `./data` to a named volume would make that
command destructive.
