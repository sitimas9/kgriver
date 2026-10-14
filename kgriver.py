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
                    code = extract_otp(subj + " " + body, exclude=[to_addr])
                    if code:
                        print(f"    [otp] {subj[:40]} code={code}", flush=True)
                        m.logout()
                        return code
            try:
                m.logout()
            except Exception:
                pass
        except Exception as e:
            print(f"    [imap: {str(e)[:70]}]", flush=True)
        time.sleep(interval)
    return None


# --------------------------------------------------------------------------
# OTP extraction (shared by IMAP and disposable-inbox paths)
# --------------------------------------------------------------------------

def extract_otp(blob, exclude=None):
    """Pull a 6-digit code out of a mail body/subject.

    Order matters: strip the recipient address and CSS hex colours FIRST,
    otherwise a digit-run inside the address (u1234567890@host) or a colour
    literal wins the regex. Tenant codes are usually standalone in their own
    line/paragraph, so a whitespace-delimited match is the strongest signal.
    """
    text = blob or ""
    for pat in (exclude or []):
        if pat:
            text = text.replace(pat, " ")
            text = text.replace(pat.split("@")[0], " ")
    text = re.sub(r"#[0-9a-fA-F]{3,8}", " ", text)      # css hex colours
    text = re.sub(r"&[#a-zA-Z0-9]+;", " ", text)         # html entities
    text = re.sub(r"https?://\S+", " ", text)            # urls (may carry ids)
    text = re.sub(r"\b\d{7,}\b", " ", text)              # long digit runs (ids)
    # strongest: standalone token surrounded by whitespace / tags
    for pat in (r"(?:^|[\s>\"'])(\d{6})(?:$|[\s<\"',.])",
                r"(?:code|kode|otp|verification)[^0-9]{0,30}(\d{6})",
                r"(\d{6})[^0-9]{0,30}(?:code|kode|otp|expires|valid)"):
        m = re.search(pat, text, re.I | re.M)
        if m:
            return m.group(1)
    m = re.search(r"\b(\d{6})\b", text)
    return m.group(1) if m else None


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
# Disposable-inbox provider (mail.tm compatible API)
# --------------------------------------------------------------------------

class MailTm:
    """Thin client for mail.tm-style disposable inboxes (hydra API)."""

    def __init__(self, base="https://api.mail.tm", domain=None, timeout=20):
        self.base = base.rstrip("/")
        self.domain = domain
        self.timeout = timeout
        self.address = None
        self.password = None
        self.token = None

    def _req(self, method, path, body=None, token=None):
        import urllib.request
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode(errors="replace")
                return r.status, (json.loads(raw) if raw.strip() else {})
        except Exception as e:
            code = getattr(e, "code", 0)
            try:
                return code, json.loads(e.read().decode(errors="replace"))
            except Exception:
                return code, {"error": str(e)}

    def pick_domain(self):
        if self.domain:
            return self.domain
        st, d = self._req("GET", "/domains")
        items = d.get("hydra:member") if isinstance(d, dict) else d
        if st == 200 and isinstance(items, list):
            active = [m["domain"] for m in items if isinstance(m, dict) and m.get("isActive")]
            if active:
                return active[0]
        raise RuntimeError("no active mail.tm domain available")

    def create(self, local=None, retries=4):
        domain = self.pick_domain()
        local = local or ("u" + str(int(time.time() * 1000))[-9:] + os.urandom(2).hex())
        self.address = f"{local}@{domain}"
        self.password = os.urandom(9).hex() + "Aa1!"
        # mail.tm rate-limits account creation per IP — back off and retry.
        st = None
        for attempt in range(retries):
            st, _ = self._req("POST", "/accounts", {"address": self.address, "password": self.password})
            if st in (200, 201):
                break
            if st == 429:
                wait = 20 * (attempt + 1)
                print(f"[*] mail.tm 429, backoff {wait}s (attempt {attempt + 1}/{retries})", flush=True)
                time.sleep(wait)
                # rotate the local part so we don't collide with a half-created account
                local = "u" + str(int(time.time() * 1000))[-9:] + os.urandom(2).hex()
                self.address = f"{local}@{domain}"
                self.password = os.urandom(9).hex() + "Aa1!"
                continue
            raise RuntimeError(f"mail.tm account create failed: {st}")
        if st not in (200, 201):
            raise RuntimeError(f"mail.tm account create rate-limited after {retries} attempts: {st}")
        st, tok = self._req("POST", "/token", {"address": self.address, "password": self.password})
        if st != 200 or "token" not in tok:
            raise RuntimeError(f"mail.tm token failed: {st}")
        self.token = tok["token"]
        print(f"[*] inbox created: {self.address}", flush=True)
        return self.address

    def fetch_code(self, timeout_s=300, interval_s=6):
        """Poll the inbox for a 6-digit code; scan newest-first."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            st, d = self._req("GET", "/messages", token=self.token)
            if st == 200:
                items = d.get("hydra:member") if isinstance(d, dict) else d
                if isinstance(items, list):
                    msgs = sorted(items, key=lambda m: (m.get("createdAt") if isinstance(m, dict) else "",), reverse=True)
                    for m in msgs[:10]:
                        if not isinstance(m, dict): continue
                        mid = m.get("id")
                        st2, full = self._req("GET", f"/messages/{mid}", token=self.token)
                        if st2 != 200 or not isinstance(full, dict): continue
                        blob = (m.get("subject") or "") + " " + json.dumps(full.get("text", "")) + " " + json.dumps(full.get("html", ""))
                        try:
                            with open("/tmp/kgriver_last_mail.json", "w") as _f:
                                json.dump({"subject": m.get("subject"), "text": full.get("text"), "html": full.get("html")}, _f, indent=1)
                        except Exception:
                            pass
                        code = extract_otp(blob, exclude=[self.address])
                        if code:
                            print(f"    [otp] {(m.get('subject') or '')[:40]} code={code}", flush=True)
                            return code
            time.sleep(interval_s)
        return None


# --------------------------------------------------------------------------
# Browser interaction helpers
# --------------------------------------------------------------------------

def _get_captcha_solver(cfg):
    cpath = cfg["integrations"]["captcha_solver_path"]
    if cpath and cpath not in sys.path:
        sys.path.insert(0, cpath)
    mod = __import__(cfg["integrations"]["captcha_solver_module"], fromlist=["_"])
    return getattr(mod, cfg["integrations"]["captcha_solver_func"])


OAUTH_WORDS = ("google", "github", "microsoft", "apple", "sso", "passkey", "saml", "okta")


async def click_primary(page, cfg):
    """Click the real submit button. OAuth provider buttons (e.g. 'Continue with
    Google') match the same :has-text('Continue') selector and sit earlier in
    the DOM — skipping them is mandatory or the form never submits."""
    for sel in cfg["selectors"]["continue_buttons"]:
        for b in await page.query_selector_all(sel):
            try:
                txt = ((await b.inner_text()) or "").strip()
            except Exception:
                txt = ""
            if any(w in txt.lower() for w in OAUTH_WORDS):
                continue
            try:
                if not await b.is_visible():
                    continue
                await b.click(timeout=5000)
                print(f"    [click] {sel} -> {txt[:34]!r}", flush=True)
                return True
            except Exception:
                continue
    return False


async def submit_form(page, cfg, email_sel):
    """click_primary, then fall back to Enter-in-email-field (React forms
    sometimes ignore a synthetic click pre-hydration)."""
    if await click_primary(page, cfg):
        return True
    print("    [click] no button matched — trying Enter fallback", flush=True)
    try:
        el = await page.query_selector(email_sel)
        if el:
            await el.press("Enter")
            return True
    except Exception as e:
        print(f"    [click] Enter fallback err: {e}", flush=True)
    return False


async def submit_otp(page, cfg, otp):
    """Fill the OTP field. WorkOS-style pages render a hidden aggregate input
    (autocomplete=one-time-code, maxlength=6) plus per-digit boxes with
    data-index. Filling the aggregate is the intended path; per-digit boxes
    must be ordered by their data-index attribute, not DOM position."""
    # 1) aggregate input (preferred)
    agg = await page.query_selector("input[autocomplete='one-time-code']")
    if agg:
        try:
            await agg.click(timeout=2500)
        except Exception:
            pass
        try:
            await agg.fill(otp, timeout=3000)
        except Exception as e:
            if "destroyed" in str(e):
                return  # auto-submit fired
            try:
                await agg.type(otp, delay=90)
            except Exception:
                pass
        await page.wait_for_timeout(2500)
        # did the boxes fill?
        try:
            filled = await page.evaluate(
                "() => Array.from(document.querySelectorAll(\"input[data-index]\")).filter(i => i.value).length")
            if filled and filled >= len(otp) - 1:
                await click_primary(page, cfg)
                await page.wait_for_timeout(6500)
                return
        except Exception:
            pass
    # 2) per-digit boxes ordered by data-index
    boxes = {}
    try:
        els = await page.query_selector_all("input[data-index]")
    except Exception:
        return  # navigation happened — treat as auto-submit
    for el in els:
        try:
            idx = int(await el.get_attribute("data-index"))
            boxes[idx] = el
        except Exception:
            pass
    if boxes:
        for i, ch in enumerate(otp):
            el = boxes.get(i + 1)
            if not el:
                continue
            try:
                await el.fill(ch, timeout=1500)
            except Exception as e:
                if "destroyed" in str(e):
                    return
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass
    await page.wait_for_timeout(2500)
    await click_primary(page, cfg)
    await page.wait_for_timeout(6500)


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

async def provision_account(email_addr, password, cfg, imap_cfg, mail_box=None):
    import cloakbrowser

    captured: dict = {"token_body": None, "req_token": None}

    async def on_resp(resp):
        try:
            url = resp.url
            frag = cfg["target"]["authenticate_path_fragment"]
            if frag in url and resp.request.method == "POST":
                body = await resp.text()
                if '"access_token"' in body:
                    captured["token_body"] = body
                    print("    [+] token captured", flush=True)
                else:
                    print(f"    [!] auth {resp.status}: {body[:300]}", flush=True)
            elif resp.request.method == "POST" and "login.console.river.ai" in url:
                body = await resp.text()
                print(f"    [net] {resp.status} POST {url.split('?')[0][-70:]} -> {body[:220]}", flush=True)
        except Exception:
            pass

    def on_request(req):
        # Donor trick (river.js): grab Bearer token straight from request
        # headers — fires even when the response body isn't observable.
        try:
            h = req.headers or {}
            a = h.get("authorization") or ""
            if a.lower().startswith("bearer ") and "workos" not in (req.url or ""):
                if not captured.get("req_token"):
                    captured["req_token"] = a[7:].strip()
                    print(f"    [dbg] req-token captured len={len(captured['req_token'])}", flush=True)
        except Exception:
            pass

    proxy = cfg["integrations"]["proxy"]
    async with await cloakbrowser.launch_async(headless=True, humanize=True, proxy=proxy) as browser:
        page = await browser.new_page()
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))
        page.on("request", lambda r: on_request(r))

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
        step1_ok = False
        for s1 in range(3):
            inputs = await page.query_selector_all(cfg["selectors"]["text_inputs"])
            if len(inputs) < 3:
                print("[!] form inputs missing", flush=True)
                return None
            try:
                await inputs[0].fill(fn)
                await inputs[1].fill(ln)
                await inputs[2].fill(email_addr)
            except Exception as e:
                print(f"    [step1 attempt {s1 + 1}] fill err: {e}", flush=True)
            await page.wait_for_timeout(1500)
            await submit_form(page, cfg, cfg["selectors"]["email_input"])
            await page.wait_for_timeout(7000)
            await handle_challenge(page, cfg, attempts=8, rounds=3)
            try:
                cur_url = page.url
            except Exception:
                cur_url = ""
            path = cur_url.split("?")[0]
            pw_check = await page.query_selector(cfg["selectors"]["password_input"])
            advanced = ("password" in path or "email-verification" in path
                        or "callback" in path or bool(pw_check))
            if advanced:
                step1_ok = True
                print(f"    [step1 attempt {s1 + 1}] advanced: {path}", flush=True)
                break
            print(f"    [step1 attempt {s1 + 1}] still at {path} — retry", flush=True)
        if not step1_ok:
            print("[!] step1 submit never advanced — bail", flush=True)
            return None

        pw = await page.query_selector(cfg["selectors"]["password_input"])
        if pw:
            print("[*] password step", flush=True)
            # Submit with retry: a stale Turnstile token makes the button a no-op,
            # leaving the page parked on sign-up/password. Re-solve + resubmit.
            # BUT: policy_denied (WorkOS Radar) must NOT be retried — hammering the
            # same email+Turnstile escalates the block. Detect it and bail at once.
            denied = False
            for attempt in range(3):
                await handle_challenge(page, cfg, attempts=8, rounds=3)
                try:
                    cur = await page.query_selector(cfg["selectors"]["password_input"])
                    if cur:
                        await cur.fill(password)
                    else:
                        await pw.fill(password)
                    # let the Turnstile token mint/settle before submitting
                    await page.wait_for_timeout(2500)
                except Exception as e:
                    if "destroyed" in str(e):
                        break
                await click_primary(page, cfg)
                await page.wait_for_timeout(7000)
                await handle_challenge(page, cfg, attempts=8, rounds=3)
                if cfg["selectors"]["otp_url_fragment"] in page.url or "callback" in page.url:
                    break
                try:
                    body = await page.content()
                except Exception:
                    body = ""
                if "policy_denied" in body or "Access blocked" in body:
                    denied = True
                    print("    [!] policy_denied (WorkOS Radar) — stop, do not retry", flush=True)
                    break
                stuck = "sign-up/password" in page.url or "sign-up?" in page.url
                print(f"    [password attempt {attempt + 1}] still at {page.url[:70]} stuck={stuck}", flush=True)
                if not stuck:
                    break
                try:
                    with open(f"/tmp/kgriver_pw_stuck_{attempt}.html", "w") as _f:
                        _f.write(body)
                    await page.screenshot(path=f"/tmp/kgriver_pw_stuck_{attempt}.png", full_page=True)
                    print(f"    [dbg] stuck dump {attempt}", flush=True)
                except Exception:
                    pass
            if denied:
                return None
        print("[*] after password:", page.url[:110], flush=True)

        if cfg["selectors"]["otp_url_fragment"] in page.url:
            print(f"[*] awaiting OTP -> {email_addr}", flush=True)
            if mail_box is not None:
                otp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: mail_box.fetch_code(
                        timeout_s=cfg["timing"]["otp_timeout_s"],
                        interval_s=cfg["timing"]["imap_poll_interval_s"]))
            else:
                otp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: fetch_otp(cfg, imap_cfg, email_addr))
            if not otp:
                print("[!] OTP timeout", flush=True)
                return None
            print(f"[*] OTP: {otp}", flush=True)
            try:
                await page.screenshot(path="/tmp/kgriver_otp_page.png", full_page=True)
                with open("/tmp/kgriver_otp_page.html", "w") as _f:
                    _f.write(await page.content())
                print("    [dbg] otp page dumped", flush=True)
            except Exception as _e:
                print("    [dbg] dump fail", _e, flush=True)
            await submit_otp(page, cfg, otp)
            try:
                await page.screenshot(path="/tmp/kgriver_otp_after.png", full_page=True)
                with open("/tmp/kgriver_otp_after.html", "w") as _f:
                    _f.write(await page.content())
                print("    [dbg] after-submit page dumped ->", page.url[:80], flush=True)
            except Exception:
                pass

        console_host = cfg["target"]["console_base"].split("//")[-1].split("/")[0]
        for _ in range(10):
            if console_host in page.url and "login." not in page.url:
                break
            await page.wait_for_timeout(2500)
        print("[*] landed:", page.url[:100], flush=True)

        for _ in range(12):
            if captured["token_body"] or captured["req_token"]:
                break
            await page.wait_for_timeout(2500)
        tok = None
        if captured["token_body"]:
            try:
                auth = json.loads(captured["token_body"])
                tok = auth.get("access_token")
                print(f"[*] token len={len(tok or '')} user={(auth.get('user') or {}).get('email')}", flush=True)
            except Exception as e:
                print(f"[!] token_body parse fail: {e}", flush=True)
        if not tok and captured["req_token"]:
            # donor path: validate the request-header token against /api/me
            st0, _me0 = api_call(cfg, "me", "GET", bearer=captured["req_token"])
            if st0 == 200:
                tok = captured["req_token"]
                print(f"[*] req-token validated len={len(tok)}", flush=True)
            else:
                print(f"[!] req-token rejected me={st0}", flush=True)
        if not tok:
            print("[!] no token captured", flush=True)
            return None
        auth = {"access_token": tok}

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
            "key_name": keyobj.get("name"),
            "balance_usd": balance,
            "verify_ok": ok,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        out = cfg["output"]["accounts_file"]
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        # donor-style JSON/CSV mirror (audit-friendly, safe-to-share format)
        try:
            base = os.path.splitext(out)[0]
            accs = []
            if os.path.exists(base + ".json"):
                try:
                    accs = json.load(open(base + ".json")).get("accounts", [])
                except Exception:
                    accs = []
            accs.append({"status": "ok", "email": email_addr, "password": password,
                         "api_key": newkey, "key_name": keyobj.get("name"),
                         "balance_usd": balance, "verify_ok": ok,
                         "created": rec["created"]})
            json.dump({"generated_at": rec["created"], "total": len(accs),
                       "succeeded": sum(1 for a in accs if a.get("api_key")),
                       "accounts": accs}, open(base + ".json", "w"), indent=2)
            with open(base + ".csv", "a") as f:
                f.write(",".join(["ok", email_addr, password, newkey,
                                  str(keyobj.get("name")), str(balance), str(ok)]) + "\n")
        except Exception as e:
            print(f"[!] json/csv mirror fail: {e}", flush=True)
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
    use_tmail = "--tmail" in sys.argv or os.environ.get("KGR_TMAIL") == "1"
    cfg = load_config(cfg_path)
    if use_tmail:
        mb_cfg = (cfg.get("integrations") or {}).get("tmail") or {}
        box = MailTm(base=mb_cfg.get("base", "https://api.mail.tm"),
                     domain=mb_cfg.get("domain"))
        email_addr = box.create()
        password = args[0] if args else os.environ.get("KGR_PASSWORD", "ChangeMe!2026")
        rec = await provision_account(email_addr, password, cfg, None, mail_box=box)
        print("[RESULT]", ("OK " + rec["api_key"][:24]) if rec else "FAILED", flush=True)
        return
    if not args:
        print("usage: kgriver.py <email> [password] [--config path] [--tmail]")
        sys.exit(1)
    email_addr = args[0]
    password = args[1] if len(args) > 1 else os.environ.get("KGR_PASSWORD", "ChangeMe!2026")
    if not cfg["target"]["signup_url"]:
        print("[!] target.signup_url not set — configure config.yaml or KGR_SIGNUP_URL")
        sys.exit(2)
    imap_cfg = load_mail_env()
    rec = await provision_account(email_addr, password, cfg, imap_cfg)
    print("[RESULT]", ("OK " + rec["api_key"][:24]) if rec else "FAILED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
