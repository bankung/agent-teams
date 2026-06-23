# tn-email — Usage examples

```
/tn-email auth-status
/tn-email status
/tn-email search "is:unread from:recruiter@" --cap 20
/tn-email read 18b3f1a2c9d4e5f6
/tn-email thread 18b3f1a2c9d4e5f0
/tn-email open --jobs --since 14d
/tn-email triage 10
/tn-email sweep-jobs
/tn-email trash "from:noreply@newsletter.com older_than:60d" --dry-run
/tn-email archive 18b3f1a2 18b3f1a3
/tn-email mark read 18b3f1a2 18b3f1a3
/tn-email draft "hiring@acme.com" "Re: Application" "Thank you for the update..."
/tn-email phishing-scan
/tn-email clean newsletters
```
