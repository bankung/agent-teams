# tn-email — search verb

## 5a. `search <query> [--cap N]`

1. Check auth: `GET /auth/gmail/status` + `GET /auth/outlook/status`.
2. Search both inboxes (or Gmail only if Outlook not authenticated):
   ```
   POST /gmail/search  {"query": "<query>", "max_results": <N or 10>}
   POST /outlook/search {"query": "<kql-equivalent>", "max_results": <N or 10>}
   ```
   Note: you must manually translate the query concept to KQL for Outlook — the
   syntaxes differ; do NOT pass the Gmail query string to Outlook verbatim.
3. Display results grouped by provider: from / subject / date / snippet.
