# zb-email — Usage examples

```
/zb-email auth-status
/zb-email status
/zb-email search "is:unread from:recruiter@" --cap 20
/zb-email read 18b3f1a2c9d4e5f6
/zb-email thread 18b3f1a2c9d4e5f0
/zb-email open --jobs --since 14d
/zb-email triage 10
/zb-email sweep-jobs
/zb-email trash "from:noreply@newsletter.com older_than:60d" --dry-run
/zb-email archive 18b3f1a2 18b3f1a3
/zb-email mark read 18b3f1a2 18b3f1a3
/zb-email draft "hiring@acme.com" "Re: Application" "Thank you for the update..."
/zb-email phishing-scan
/zb-email clean newsletters
```
