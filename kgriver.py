"""
KGRiver — Account Provisioning Automation Toolkit
=================================================
End-to-end automated account provisioning with email-OTP verification,
bearer-token capture, and API-key lifecycle management.

Pipeline: signup form -> anti-bot challenge -> password -> email OTP (IMAP)
          -> token capture -> API key mint -> key verification.

All target endpoints, selectors, and API paths live in config.yaml — no
vendor names are hardcoded in this module.

Usage:
    python3 kgriver.py <email> [password] [--config config.yaml]
"""

import asyncio
import sys
import os
import re
import json
import time
import imaplib
import email as emaillib

try:
    import yaml
except ImportError:
    yaml = None


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "target": {
        "signup_url": "",
        "console_base": "",
        "api_base": "",
        "authenticate_path_fragment": "user_management/authenticate",
    },
    "selectors": {
        "email_input": "input[name='email'], input[type='email']",
        "text_inputs": "input[type='text'], input[type='email']",
        "password_input": "input[type='password']",
        "continue_buttons": [
            "button:has-text('Continue')",
            "button:has-text('Verify')",
            "button:has-text('Submit')",
            "button[type='submit']",
        ],
        "otp_url_fragment": "email-verification",
    },
    "api": {
        "me": "/api/me",
        "billing": "/api/billing/account",
        "keys": "/api/keys",
        "chat": "/v1/chat/completions",
    },
    "integrations": {
        "captcha_solver_path": "~/captcha-solver",
        "captcha_solver_module": "turnstile.solve",
        "captcha_solver_func": "_click_turnstile_checkbox",
        "proxy": {"server": "socks5://127.0.0.1:1080"},
    },
    "identity": {
        "name_pool": [
            ["Agus", "Wijaya"],
            ["Budi", "Santoso"],
            ["Sari", "Dewi"],
            ["Rian", "Kusuma"],
            ["Dewi", "Rahmawati"],
            ["Andi", "Pratama"],
        ],
    },
    "verify_model": "",
    "output": {
        "accounts_file": "./accounts.jsonl",
        "cookie_dir": "./state",
    },
    "timing": {
        "otp_timeout_s": 300,
        "imap_poll_interval_s": 6,
    },
}


def deep_merge(base, overlay):
    out = dict(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    cfg = DEFAULT_CONFIG
    candidates = [path] if path else ["config.yaml", "config.yml"]
    for c in candidates:
        if c and os.path.exists(c):
            if yaml is None:
                raise RuntimeError("pyyaml required to read config: pip install pyyaml")
            with open(c) as f:
                cfg = deep_merge(DEFAULT_CONFIG, yaml.safe_load(f) or {})
            break
    # env-var overrides for secrets / paths (keep config file shareable)
    cpath = os.environ.get("KGR_CAPTCHA_PATH", cfg["integrations"]["captcha_solver_path"])
    cfg["integrations"]["captcha_solver_path"] = os.path.expanduser(cpath) if cpath else ""
    if os.environ.get("KGR_PROXY"):
        cfg["integrations"]["proxy"] = {"server": os.environ["KGR_PROXY"]}
    if os.environ.get("KGR_SIGNUP_URL"):
        cfg["target"]["signup_url"] = os.environ["KGR_SIGNUP_URL"]
    if os.environ.get("KGR_CONSOLE_BASE"):
        cfg["target"]["console_base"] = os.environ["KGR_CONSOLE_BASE"]
    if os.environ.get("KGR_VERIFY_MODEL"):
        cfg["verify_model"] = os.environ["KGR_VERIFY_MODEL"]
    return cfg


def load_mail_env():
    """IMAP credentials: environment first, then ~/.env, then ~/github-farm/.env
    (legacy path). Never hardcode secrets in the repo."""
    env = dict(os.environ)
    for p in (os.path.expanduser("~/.env"), os.path.expanduser("~/github-farm/.env")):
        if not os.path.exists(p):
            continue
        for line in open(p):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    # support both naming schemes
    host = env.get("IMAP_HOST") or env.get("IMAP_SERVER")
    user = env.get("IMAP_USER") or env.get("IMAP_LOGIN")
    pw = env.get("IMAP_APP_PASS") or env.get("IMAP_PASS") or env.get("IMAP_PASSWORD")
    return {"host": host, "user": user, "pw": pw}


# --------------------------------------------------------------------------
# Email OTP retrieval (IMAP)
# --------------------------------------------------------------------------

def fetch_otp(cfg, imap_cfg, to_addr, timeout_s=None):
    """Poll IMAP for a 6-digit verification code addressed to to_addr."""
    timeout_s = timeout_s or cfg["timing"]["otp_timeout_s"]
    interval = cfg["timing"]["imap_poll_interval_s"]
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            m = imaplib.IMAP4_SSL(imap_cfg["host"])
            m.login(imap_cfg["user"], imap_cfg["pw"])
            m.select("INBOX")
            typ, ids = m.search(None, "ALL")
            if typ.upper() == "OK":
                recent = ids[0].split()[::-1][:25] if ids and ids[0] else []
                for i in recent:
                    t2, data = m.fetch(i, "(RFC822)")
                    if t2.upper() != "OK" or not data or not isinstance(data[0], tuple):
                        continue
                    msg = emaillib.message_from_bytes(data[0][1])
                    to = (msg.get("To") or "").lower()
                    subj = msg.get("Subject") or ""
                    # strict alias match — prevents grabbing another inbox's code
                    if to_addr.lower() not in to:
                        continue
                    body = _extract_body(msg)
                    mm = re.search(r"\b(\d{6})\b", subj + " " + body)
                    if mm:
                        print(f"    [otp] {subj[:40]} code={mm.group(1)}", flush=True)
                        m.logout()
                        return mm.group(1)
            try:
                m.logout()
            except Exception:
                pass
        except Exception as e:
            print(f"    [imap: {str(e)[:70]}]", flush=True)
        time.sleep(interval)
    return None


def _extract_body(msg):
    if msg.is_multipart():
        parts = []
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                try:
                    parts.append((part.get_payload(decode=True) or b"").decode(errors="replace"))
                except Exception:
                    pass
        return "\n".join(parts)
    try:
        return (msg.get_payload(decode=True) or b"").decode(errors="replace")
    except Exception:
        return str(msg.get_payload())


# --------------------------------------------------------------------------
# Browser interaction helpers
# --------------------------------------------------------------------------

def _get_captcha_solver(cfg):
    cpath = cfg["integrations"]["captcha_solver_path"]
    if cpath and cpath not in sys.path:
        sys.path.insert(0, cpath)
    mod = __import__(cfg["integrations"]["captcha_solver_module"], fromlist=["_"])
    return getattr(mod, cfg["integrations"]["captcha_solver_func"])


async def click_primary(page, cfg):
    for sel in cfg["selectors"]["continue_buttons"]:
        b = await page.query_selector(sel)
        if b:
            try:
                await b.click(timeout=5000)
                return True
            except Exception:
                continue
    return False


async def handle_challenge(page, cfg, attempts=8, rounds=4):
    """Dismiss the anti-bot interstitial by driving its widget."""
    solver = _get_captcha_solver(cfg)
    for _ in range(rounds):
        if not any("challenges.cloudflare.com" in f.url for f in page.frames):
            return True
        try:
            await solver(page, attempts=attempts)
        except Exception:
            pass
        await page.wait_for_timeout(4000)
    return not any("challenges.cloudflare.com" in f.url for f in page.frames)


async def wait_for_form(page, cfg, timeout_s=120):
    sel = cfg["selectors"]["email_input"]
    t0 = time.time()
    clk = 0
    while time.time() - t0 < timeout_s:
        try:
            if await page.query_selector(sel):
                return True
        except Exception:
            pass
        if clk < 5 and any("challenges.cloudflare.com" in f.url for f in page.frames):
            clk += 1
            try:
                await handle_challenge(page, cfg, attempts=6, rounds=2)
            except Exception:
                pass
        await page.wait_for_timeout(2000)
    return False


# --------------------------------------------------------------------------
# HTTP API helper
# --------------------------------------------------------------------------

def api_call(cfg, path_key, method, body=None, bearer=None, extra_path=""):
    import urllib.request
    base = cfg["target"]["console_base"].rstrip("/")
    path = cfg["api"][path_key] + extra_path
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body else None,
        method=method,
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36",
            "Referer": base + "/",
            "Origin": base,
        })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(errors="replace")
    except Exception as e:
        code = getattr(e, "code", 0)
        try:
            return code, e.read().decode(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            return code, str(e)


def verify_key(cfg, api_key):
    import urllib.request
    base = cfg["target"]["api_base"].rstrip("/")
    body = {"model": cfg["verify_model"], "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}
    req = urllib.request.Request(
        base + cfg["api"]["chat"],
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status == 200
    except Exception:
        return False


# --------------------------------------------------------------------------
# Core provisioning flow
# --------------------------------------------------------------------------

async def provision_account(email_addr, password, cfg, imap_cfg):
    import cloakbrowser

    captured = {"token_body": None}

    async def on_resp(resp):
        try:
            frag = cfg["target"]["authenticate_path_fragment"]
            if frag in resp.url and resp.request.method == "POST":
                body = await resp.text()
                if '"access_token"' in body:
                    captured["token_body"] = body
                    print("    [+] token captured", flush=True)
        except Exception:
            pass

    proxy = cfg["integrations"]["proxy"]
    async with await cloakbrowser.launch_async(headless=True, humanize=True, proxy=proxy) as browser:
        page = await browser.new_page()
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        await page.goto(cfg["target"]["signup_url"], wait_until="domcontentloaded", timeout=45000)
        ready = False
        for _ in range(3):
            ready = await wait_for_form(page, cfg, 120)
            if ready:
                break
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
        if not ready:
            print("[!] challenge/form never cleared", flush=True)
            return None
        print("[*] form ready:", await page.title(), flush=True)

        inputs = await page.query_selector_all(cfg["selectors"]["text_inputs"])
        if len(inputs) < 3:
            print("[!] form inputs missing", flush=True)
            return None
        pool = cfg["identity"]["name_pool"]
        fn, ln = pool[int(time.time()) % len(pool)]
        await inputs[0].fill(fn)
        await inputs[1].fill(ln)
        await inputs[2].fill(email_addr)
        await page.wait_for_timeout(1200)
        await click_primary(page, cfg)
        await page.wait_for_timeout(6000)
        await handle_challenge(page, cfg, attempts=8, rounds=3)

        pw = await page.query_selector(cfg["selectors"]["password_input"])
        if pw:
            print("[*] password step", flush=True)
            await handle_challenge(page, cfg, attempts=8, rounds=3)
            await pw.fill(password)
            await page.wait_for_timeout(900)
            await click_primary(page, cfg)
            await page.wait_for_timeout(7000)
            await handle_challenge(page, cfg, attempts=8, rounds=3)
        print("[*] after password:", page.url[:110], flush=True)

        if cfg["selectors"]["otp_url_fragment"] in page.url:
            print(f"[*] awaiting OTP -> {email_addr}", flush=True)
            otp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: fetch_otp(cfg, imap_cfg, email_addr))
            if not otp:
                print("[!] OTP timeout", flush=True)
                return None
            print(f"[*] OTP: {otp}", flush=True)
            # some flows auto-submit on fill (navigation destroys the context)
            for ci in await page.query_selector_all("input"):
                try:
                    await ci.fill(otp, timeout=2500)
                    break
                except Exception as e:
                    if "destroyed" in str(e):
                        break
            await page.wait_for_timeout(9000)

        console_host = cfg["target"]["console_base"].split("//")[-1].split("/")[0]
        for _ in range(10):
            if console_host in page.url and "login." not in page.url:
                break
            await page.wait_for_timeout(2500)
        print("[*] landed:", page.url[:100], flush=True)

        for _ in range(12):
            if captured["token_body"]:
                break
            await page.wait_for_timeout(2500)
        if not captured["token_body"]:
            print("[!] no token captured", flush=True)
            return None

        auth = json.loads(captured["token_body"])
        tok = auth["access_token"]
        print(f"[*] token len={len(tok)} user={(auth.get('user') or {}).get('email')}", flush=True)

        st, _me = api_call(cfg, "me", "GET", bearer=tok)
        st2, bal = api_call(cfg, "billing", "GET", bearer=tok)
        st3, kr = api_call(cfg, "keys", "POST", body={"name": "default"}, bearer=tok)
        print(f"[*] me={st} billing={st2} key={st3}", flush=True)
        keyobj = json.loads(kr) if st3 == 200 else {}
        newkey = keyobj.get("key")
        balance = json.loads(bal).get("balance_usd") if st2 == 200 else None
        if not newkey:
            print("[!] key mint failed:", kr[:200], flush=True)
            return None

        ok = verify_key(cfg, newkey) if cfg.get("verify_model") else None
        print(f"[*] balance=${balance} key={newkey[:22]}... verify={ok}", flush=True)

        rec = {
            "email": email_addr,
            "password": password,
            "user_id": (auth.get("user") or {}).get("id"),
            "access_token": tok,
            "refresh_token": auth.get("refresh_token"),
            "api_key": newkey,
            "key_id": keyobj.get("id"),
            "balance_usd": balance,
            "verify_ok": ok,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        out = cfg["output"]["accounts_file"]
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        try:
            cdir = cfg["output"]["cookie_dir"]
            os.makedirs(cdir, exist_ok=True)
            slug = email_addr.replace("@", "_at_").replace("+", "_")
            with open(os.path.join(cdir, f"session_{slug}.json"), "w") as f:
                json.dump(await page.context.cookies(), f, indent=1)
        except Exception:
            pass
        return rec


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cfg_path = None
    if "--config" in sys.argv:
        cfg_path = sys.argv[sys.argv.index("--config") + 1]
    if not args:
        print("usage: kgriver.py <email> [password] [--config path]")
        sys.exit(1)
    email_addr = args[0]
    password = args[1] if len(args) > 1 else os.environ.get("KGR_PASSWORD", "ChangeMe!2026")
    cfg = load_config(cfg_path)
    if not cfg["target"]["signup_url"]:
        print("[!] target.signup_url not set — configure config.yaml or KGR_SIGNUP_URL")
        sys.exit(2)
    imap_cfg = load_mail_env()
    rec = await provision_account(email_addr, password, cfg, imap_cfg)
    print("[RESULT]", ("OK " + rec["api_key"][:24]) if rec else "FAILED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
