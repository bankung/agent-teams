# API contracts (FastAPI ↔ Next.js)

> **Lead is the only writer of this file.** Backend proposes new/changed contracts; frontend consumes them. Lead reviews and writes.
>
> This is the source of truth for HTTP contracts shared between the Next.js client and the FastAPI server. If code disagrees with this file, fix the code (or fix this file via a proposal — not both at once).

## Conventions

- **Base URL:** `<set per environment in .env — e.g., http://localhost:8456>`
- **Auth:** `<JWT in Authorization: Bearer ... | session cookie | none>` *(fill in once decided)*
- **Error envelope:** FastAPI default — `{"detail": "<message>"}` with appropriate HTTP status
- **Pagination:** `<offset/limit | cursor>` *(fill in once decided)*
- **Datetime:** ISO 8601 with timezone (`2026-05-04T12:34:56+07:00`)
- **IDs:** UUIDv4 strings unless specified

## Endpoints

<!--
Template for a new endpoint:

### <METHOD> /path/{param}
**Purpose:** <one line>
**Auth:** <required role / public>

**Request:**
```json
{ "field": "type" }
```

**Response 200:**
```json
{ "field": "type" }
```

**Errors:**
- `400` — `{ "detail": "<message>" }` when <condition>
- `401` — when <condition>
- `404` — when <condition>
-->

<!-- No endpoints documented yet. First endpoint goes above this line. -->
