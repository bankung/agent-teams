# Remote access

> **Status note (2026-07-01, #2756):** The `bin/tailscale-status.*` and `bin/remote-url.*` helper scripts were **deleted** in #2756 and are no longer present in the repo. The ntfy push integration was also removed. The manual Tailscale steps below (sections 1–5) remain accurate; only the convenience scripts are gone. Push notifications are now delivered via Telegram (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_OPERATOR_CHAT_ID`) — see the Email digest section for non-push alternatives.

How to reach a self-hosted agent-teams instance from outside the home network — phone on cellular, laptop at a coffee shop, a second workstation — without opening any inbound ports on the home router.

The recommended path is **Tailscale**: a zero-config WireGuard mesh with a free tier that covers personal use. Alternatives are listed at the end for users who can't or don't want to use Tailscale.

Cross-host access works out of the box — no env-var tweaks needed. The Next.js web service proxies `/api/*` and `/health` to the FastAPI container via same-origin rewrites, so browser fetches resolve relative to whatever hostname or IP you used to load the page (Tailscale MagicDNS name, LAN IP, localhost, all behave identically).

## Why VPN over port-forwarding

The "simple" approach — DDNS + a forwarded port on the home router — exposes the agent-teams stack to the public internet. That means:

- The Kanban UI, FastAPI, and (worst case) Postgres become reachable by anyone scanning IPv4. Brute-force, SSRF probing, and CVE scanners find these in hours.
- DDNS adds another moving piece that breaks when the ISP rotates the WAN IP.
- TLS and auth become non-optional immediately — you need a real cert, a real reverse proxy, and a real login layer just to make remote access safe.

A VPN-style overlay network sidesteps all three. agent-teams keeps binding only to localhost from the home host's perspective; the overlay gives your other devices a private route in. No inbound port on the home router, no DDNS, no public exposure.

## Tailscale — recommended path

Tailscale is a managed WireGuard mesh. Each device installs a small client, authenticates to your tailnet (your private network), and gets a peer-to-peer link to every other device in the same tailnet. You get a stable hostname per device via **MagicDNS** (`<machine>.<tailnet>.ts.net`) that resolves on every connected device.

### 1. Install on the host that runs agent-teams

**Linux / macOS:**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

The `tailscale up` command prints a login URL. Open it in a browser, sign in (Google / GitHub / Microsoft / email), and the host is now in your tailnet.

**Windows:**

Download the installer from <https://tailscale.com/download/windows> and run it. The Tailscale tray icon prompts you to sign in. After sign-in, you can also drive the client from PowerShell:

```powershell
tailscale up
tailscale status
```

After sign-in on any platform, `tailscale status` shows the host with its MagicDNS name. The shape is:

```
<machine-name>.<tailnet-name>.ts.net
```

For example: `homelab.tailfoo123.ts.net`. The `<tailnet-name>` is assigned by Tailscale based on the account you signed in with; you can rename it in the admin console.

### 2. Verify the agent-teams stack binds to all interfaces

The agent-teams `docker-compose.yml` already maps the web service to all interfaces on the host — no change needed. Excerpt from `docker-compose.yml`:

```yaml
web:
  ports:
    - "${WEB_PORT:-5431}:5431"
```

A `host:container` port mapping without an explicit IP listens on `0.0.0.0` on the host side, which means the Tailscale interface picks it up automatically. The FastAPI service is the same shape (`${API_PORT:-8456}:8456`).

You can confirm with:

```bash
docker compose ps
# Or, for the bound socket directly:
ss -tlnp | grep -E '5431|8456'   # Linux
netstat -ano | findstr "5431 8456"  # Windows PowerShell
```

If you've previously locked compose to `127.0.0.1:5431:5431` for hardening reasons, change it back to the default `${WEB_PORT:-5431}:5431` shape before remote access will work.

### 3. Install Tailscale on the phone (or second laptop)

- **iOS:** App Store, search "Tailscale".
- **Android:** Play Store, search "Tailscale".
- **macOS / Windows / Linux laptops:** same install steps as the host.

Sign in to the **same tailnet** (same account) on the second device. The home host appears in the device list within seconds.

### 4. Access agent-teams from anywhere

Once both devices are signed in to the tailnet:

```
http://<machine>.<tailnet>.ts.net:5431/p/agent-teams
```

For example: `http://homelab.tailfoo123.ts.net:5431/p/agent-teams`.

This works over cellular, hotel Wi-Fi, or any network that lets WireGuard out (the Tailscale client falls back to a relay if direct peer-to-peer is blocked, so even strict captive portals usually work).

The API is at `http://<machine>.<tailnet>.ts.net:8456`.

### 5. HTTPS on the tailnet (Tailscale Serve)

Browsers complain about mixed-content and some PWA / push features require HTTPS. Tailscale issues real Let's Encrypt certs for `*.ts.net` hostnames automatically — no DNS challenge, no public exposure, no certbot.

Enable HTTPS for the web port:

> **Note:** Requires Tailscale client >= 1.54 (which added the `--bg` flag, 2024).

```bash
# On the host running agent-teams. Reverse-proxies the local web port via HTTPS.
sudo tailscale serve --bg --https=443 http://localhost:5431
```

Then access it from any tailnet device as:

```
https://<machine>.<tailnet>.ts.net/p/agent-teams
```

(no port suffix — Serve binds 443 on the tailnet hostname).

To list active Serve mappings:

```bash
tailscale serve status
```

To remove:

```bash
sudo tailscale serve --https=443 off
```

Serve is **tailnet-only** — the cert is real, but the hostname only resolves and routes for devices logged into your tailnet. Nothing is exposed publicly.

### Funnel — do not use for agent-teams

Tailscale **Funnel** is a sibling feature that publishes a `*.ts.net` hostname to the public internet (no auth required). It uses the same cert, but anyone on the internet can hit it.

**Do not enable Funnel for agent-teams.** The stack has no built-in authentication — Kanban writes, project scaffolding, and the agent harness are all unauthenticated. Exposing the API publicly via Funnel hands the keys to anyone who finds the URL. Funnel is appropriate for a static blog, a webhook receiver with its own auth, or a public demo — not for an admin-grade orchestrator with DB write access.

### MagicDNS — turn it on

In the [Tailscale admin console](https://login.tailscale.com/admin/dns), enable **MagicDNS** if it isn't already. Without it, you'd need to use the raw `100.x.y.z` Tailscale IP, which changes if you reinstall and breaks bookmarks. MagicDNS gives you stable `<machine>.<tailnet>.ts.net` names that survive client reinstalls.

## Alternatives

agent-teams has no hard dependency on Tailscale. The stack just binds to `0.0.0.0` on its host ports (`5431` for web, `8456` for API) — **any network path that lets your phone or laptop reach the host on those ports works the same way.** If you already use a VPN you trust (Mullvad, ProtonVPN, ZeroTier, Nebula, Twingate, an AWS Client VPN, a corporate VPN, plain OpenVPN, etc.), put the host and phone on it and skip the Tailscale-specific sections above — the access URL becomes `http://<host-ip-or-name>:5431/p/<project>` regardless of which overlay delivered the route. Same for a phone that's just on the same home Wi-Fi as the host: no tunnel needed.

The Tailscale-specific extras in this repo (`bin/tailscale-status.*`, `bin/remote-url.*`, and sections 1–5 above) are convenience helpers — skippable if you're using a different VPN. The four named alternatives below cover the most common substitutes when no existing VPN is in play; pick the one that matches your appetite for setup cost vs. third-party trust.

If Tailscale doesn't fit (corporate device that bans third-party VPN clients, account-creation friction, philosophical preference for self-hosted), here are the next-best options ordered by setup cost.

### Cloudflare Tunnel

Free for personal use. Install `cloudflared` on the host, log in to a Cloudflare account, point a domain you own at the tunnel, and Cloudflare routes inbound traffic through their edge to the local stack — no router config, no port forwarding.

**Tradeoff:** requires a domain on Cloudflare DNS. Traffic terminates at Cloudflare's edge (TLS cert is theirs), so they could in principle see plaintext. Acceptable for most home users, deal-breaker for some.

Docs: <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/>.

### Self-hosted WireGuard

Skip the Tailscale management plane entirely — run WireGuard on a VPS and connect home + phone as peers. Full control, no third party.

**Tradeoff:** you write the config (subnets, peer keys, route tables) by hand and host the coordination server yourself. Setup is an afternoon, not 10 minutes. No MagicDNS — you assign IPs and remember them, or run your own DNS.

Starting point: <https://www.wireguard.com/quickstart/>.

### Reverse SSH tunnel via VPS

Cheapest fallback if you already have a $5/mo VPS. From the home host:

```bash
ssh -N -R 5431:localhost:5431 user@vps.example.com
```

The VPS exposes `<vps>:5431` and forwards traffic back over SSH. Add `autossh` for reconnection on link flap.

**Tradeoff:** single-port-per-tunnel, no encryption beyond SSH, you manage the VPS firewall, public exposure shifted to the VPS (so the VPS needs its own auth in front — usually nginx + basic auth or oauth2-proxy). Works as a quick-and-dirty bridge, awkward as a long-term home for an unauthenticated stack.

## Optional helper scripts

> **Removed in #2756.** `bin/tailscale-status.{ps1,sh}` and `bin/remote-url.{ps1,sh}` no longer exist in the repo. Run `tailscale status` and `tailscale ip -4` directly from your terminal for the same information.

## Push notifications

> **ntfy removed in #2756.** The ntfy.sh push integration no longer exists. Push is now delivered via **Telegram** (the `notify_telegram.py` adapter, #2757).

### Telegram setup

1. Create a bot via BotFather in Telegram (`/newbot`) and copy the bot token.
2. Send your new bot a message (so it has a chat to reply to), then fetch your chat ID:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
   # Look for "chat":{"id":<number>} in the response.
   ```
3. Add to root `.env`:
   ```
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_OPERATOR_CHAT_ID=<chat_id>
   ```
4. Recreate the api container:
   ```bash
   docker compose up -d api
   ```

The Telegram poller (`api/scripts/telegram_poller.py`) runs as a background process. It picks up operator replies to HITL cards and forwards them to the `/api/tasks/{id}/decide` endpoint. Only messages from the configured `TELEGRAM_OPERATOR_CHAT_ID` are accepted.

## Email digest

Daily task summary via Gmail SMTP relay, with operator opt-out support.

### Setup

1. **Gmail prerequisites:**
   - Your account must have 2-step verification enabled.
   - Go to [Google Account → App Passwords](https://myaccount.google.com/apppasswords) → select Mail / your platform → Google issues a 16-char password.
   - Add to `.env`: `GMAIL_SMTP_APP_PASSWORD=<your-16-char-password>`

2. **Configure delivery:**
   - `GMAIL_SMTP_USER=your.email@gmail.com`
   - `GMAIL_SMTP_FROM=your.email@gmail.com` (or any address that makes sense to you)
   - `DIGEST_EMAIL_RECIPIENT=your.email@gmail.com` (where the daily digest goes)
   - `DIGEST_EMAIL_ENABLED=1`

3. **Restart:**
   ```bash
   docker compose restart api
   ```

### Unsubscribe

Every email footer includes an **Unsubscribe** link signed with a time-bound token. Click it to opt out of future digests. Your unsubscribe preference is stored in the database and survives service restarts.

To **re-enable** digest emails after opting out, contact the operator or use the API:
```bash
curl -X PATCH http://localhost:8456/api/projects/1 \
  -H "X-Project-Id: 1" \
  -H "Content-Type: application/json" \
  -d '{"config": {"digest_email_enabled": true}}'
```

### Schedule

By default, the digest fires once daily. To trigger it manually:
```bash
curl -X POST http://localhost:8456/api/digest/fire \
  -H "X-Project-Id: 1" \
  -H "Content-Type: application/json"
```

## Smoke test

End-to-end check from a second device:

1. On the host: `docker compose ps` — confirm `agent-teams-web` is `Up (healthy)`.
2. On the host: `tailscale status` — confirm the host is in the tailnet and note its MagicDNS name.
3. On the second device (phone on cellular, ideally — not on the same Wi-Fi, to prove the tunnel works): open `http://<machine>.<tailnet>.ts.net:5431/p/agent-teams`.
4. Expected: the Kanban board loads exactly as it does on localhost.

If step 3 fails, check in order:

- Is the second device actually on the tailnet? (`tailscale status` on the second device should list the host.)
- Is the host's compose stack listening on `0.0.0.0`, not `127.0.0.1`? (See "Verify the agent-teams stack binds to all interfaces" above.)
- Is the home host's local firewall (Windows Defender Firewall, `ufw`, `firewalld`) blocking the Tailscale interface? On a fresh Tailscale install it usually isn't, but some hardened setups need a rule like `ufw allow in on tailscale0`.
