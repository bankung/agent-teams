# zb-email — outlook-actions verbs (phishing-scan)

Note: Outlook mutate verbs (mark, archive, draft, trash) share playbooks with Gmail
equivalents — see `references/gmail-actions.md`. Provider is auto-detected from id length
(short hex = Gmail, long base64url = Outlook) or from operator context.

---

## 5o. `phishing-scan`

READ-ONLY review of recent/unread mail for suspicious signals.

1. `POST /gmail/search {"query": "is:unread", "max_results": 20}`
2. For each, check:
   - Sender domain mismatch (display name vs actual From address)
   - Urgency language ("verify immediately", "account suspended", "click now")
   - Unexpected attachment types (.exe, .zip from unknown senders)
   - Requests for credentials, payment, or personal info
3. Report flagged messages with reason. Do NOT open links. Recommend operator
   actions (mark spam, delete). Wait for go before any mutate.
