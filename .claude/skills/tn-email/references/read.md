# tn-email — read / attachment / labels verbs

## 5b. `read <id>`

1. Determine provider from id format (Gmail ids are short hex ~16 chars; Outlook ids
   are long base64url ~150+ chars). Or ask the operator if ambiguous.
2. `POST /gmail/get {"message_id": "<id>"}` or `POST /outlook/get {"message_id": "<id>"}`.
3. Display: from, to, subject, date, body_text. Summarize if long.
4. Note: `body_text` may be empty for HTML-only messages.

---

## 5d. `attachment <msg-id> <att-id>` (Gmail only)

```
POST /gmail/attachment {"message_id": "<id>", "attachment_id": "<att-id>"}
```
Display: filename, mime_type, size. Offer to summarize content if text-based.
Never write attachment data to any file.

---

## 5e. `labels` (Gmail only)

```
POST /gmail/labels {}
```
Display the label list (id + name + type).
