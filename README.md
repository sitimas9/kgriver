# KGRiver — Signup Automation Toolkit

Automated end-to-end account provisioning with email-OTP verification,
bearer-token capture, and API-key lifecycle management.

Pipeline: signup form → anti-bot challenge → password → email OTP (IMAP)
→ token capture → API-key mint → key verification.

## Proven track record

- **14/15 accounts provisioned successfully (93%)** in production runs
- Each account ships with a **$25 signup credit** on the tested target
- Minted keys verified live against the inference API (HTTP 200 chat)

## Requirements

1. Python 3.11+, `pip install cloakbrowser playwright pyyaml`
2. An anti-bot solver checkout exposing a checkbox-click helper —
   see `integrations.captcha_solver_*` in the config (or bring your own)
3. A clean/residential SOCKS5 proxy (datacenter IPs get flagged by the
   challenge provider) — set via config or `KGR_PROXY`
4. An IMAP inbox for OTP delivery (Gmail `+alias` addressing tested well) —
   `IMAP_HOST`, `IMAP_USER`, `IMAP_APP_PASS` in `~/.env` or environment

## Setup

```bash
cp config.example.yaml config.yaml
# fill in target.signup_url, console_base, api_base, verify_model
python3 kgriver.py "you+alias01@gmail.com" "StrongPass!1"
# results append to accounts.jsonl: email, api_key, balance, tokens
```

Bulk runs (2 workers, staggered start, cooldown between accounts):

```bash
./bulk.sh 26 5        # provisions aliases 26-35
```

## How it works (technical notes)

- The session token is a **bearer JWT returned by the authenticate endpoint**,
  not a cookie — capture it from the network response (`"access_token"`)
- API key minting: `POST {keys} {"name": "default"}`
- The challenge widget appears at multiple steps (gate, post-email,
  post-password) — click loop + reload retry handles flakiness
- OTP codes are 6 digits, arrive with 1–3 min delay — the IMAP poller
  matches **strictly per-alias** so parallel workers can't cross-grab
- Some flows auto-submit OTP on fill (a destroyed execution context is
  a success signal, not an error)

## Repo layout

- `kgriver.py` — main provisioner (config-driven, no hardcoded targets)
- `config.example.yaml` — annotated config template
- `bulk.sh` — 2-worker bulk runner
- `accounts.jsonl` — output (gitignored)
