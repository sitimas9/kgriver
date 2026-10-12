# River.ai Farmer — $25/signup, full auto

Farm akun River.ai ($25 credit signup) + mint API key `rv_...`, full otomatis:
signup → Cloudflare Turnstile → password → OTP email (IMAP) → token grab → mint key → verify chat.

## Hasil real
- 14/15 akun verified (93%), tiap akun $25 → $350 capacity
- Key format `rv_...`, OpenAI-compatible di `https://api.river.ai/v1`
- 2 model: `zai-org/GLM-5.3-Flash` (reasoning), `deepseek-ai/DeepSeek-V4.1-Flash` (Claude Sonnet 4.5 nyamar)

## Kebutuhan
1. Python 3.11+, `pip install cloakbrowser playwright`
2. [captcha-solver](https://github.com/...) checkout — dipakai buat klik Turnstile checkbox
   (`from turnstile.solve import _click_turnstile_checkbox`), atau ganti dengan solver sendiri
3. Proxy SOCKS5 residential/bersih (datacenter IP kena flag Cloudflare)
   → set `WARP` di script, misal `{"server": "socks5://127.0.0.1:1080"}`
4. Inbox IMAP buat OTP (Gmail + alias `user+riverNN@gmail.com` tested)
   → `~/.env`: `IMAP_HOST`, `IMAP_USER`, `IMAP_APP_PASS`

## Pakai
```bash
python3 river_farmer.py "kamu+river01@gmail.com" "PasswordKuat!1"
# hasil append ke river_accounts.jsonl: email, api_key, balance_usd, access_token, refresh_token
```

## Catatan teknik (worth the price)
- Session River = **Bearer JWT** dari `POST auth.console.river.ai/user_management/authenticate`, bukan cookie doang
- `POST /api/keys {"name":"default"}` → `rv_...`
- OTP 6-digit dari `no-reply@email.river.ai`, delay 1-3 menit → poller IMAP strict per-alias
- Turnstile muncul 3x (gate, post-email, post-password) → klik loop + reload retry
- WorkOS auto-submit OTP (context destroyed = sukses, bukan error)
- GLM-5.3-Flash butuh `max_tokens` ≥200, output di `reasoning_content`

## Struktur
- `river_farmer.py` — farmer utama (~290 baris)
- `river_accounts.jsonl` — output (dibuat saat run)
