# tn-email — auth-status / usage verbs

## 5f. `auth-status [gmail|outlook]`

Check status for the named provider (or both if unspecified):
```
GET /auth/gmail/status
GET /auth/outlook/status
```
Report: authenticated, email, expires_at.
If not authenticated, show the start URL.

---

## 5g. `usage` (Gmail only)

```
GET /gmail/usage
```
Report: units_consumed / cap / remaining for today.
