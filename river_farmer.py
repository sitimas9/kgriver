"""River.ai farmer — PRODUCTION. Full auto: signup -> OTP -> token grab -> key mint -> verify.
WARP SOCKS5 + CloakBrowser for Cloudflare. Token captured from authenticate response.

Usage: python3 river_farmer.py <email> [password]
Emails: Gmail alias  you+riverNN@gmail.com (bebas)
"""
import asyncio, sys, os, re, json, time, imaplib, email as emaillib
sys.path.insert(0, os.path.expanduser("~/captcha-solver"))  # turnstile solver checkout

CLIENT_ID = "client_01M0TG5W3GBNMFS87K40PYHM98"
SIGNUP_URL = f"https://login.console.river.ai/sign-up?client_id={CLIENT_ID}"
WARP = {"server": "socks5://127.0.0.1:1080"}
OUT = "./river_accounts.jsonl"
JARDIR = "./state"


def load_env():
    env = {}
    p = os.path.expanduser("~/.env  # IMAP_HOST, IMAP_USER, IMAP_APP_PASS")
    for line in open(p):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def fetch_otp_imap(host, user, pw, to_addr, timeout_s=300, debug=True):
    """Poll IMAP for River verification code."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            m = imaplib.IMAP4_SSL(host)
            m.login(user, pw)
            m.select("INBOX")
            typ, ids = m.search(None, 'ALL')
            if typ.upper() == "OK":
                for i in (ids[0].split()[::-1][:25] if ids and ids[0] else []):
                    typ2, data = m.fetch(i, "(RFC822)")
                    if typ2.upper() != "OK" or not data or not isinstance(data[0], tuple):
                        continue
                    msg = emaillib.message_from_bytes(data[0][1])
                    frm = (msg.get("From") or "").lower()
                    to = (msg.get("To") or "").lower()
                    subj = msg.get("Subject") or ""
                    if to_addr.lower() not in to:
                        # STRICT: only accept mail explicitly addressed to this alias
                        continue
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() in ("text/plain", "text/html"):
                                try:
                                    body += (part.get_payload(decode=True) or b"").decode(errors="replace")
                                except Exception:
                                    pass
                    else:
                        try:
                            body = (msg.get_payload(decode=True) or b"").decode(errors="replace")
                        except Exception:
                            body = str(msg.get_payload())
                    mm = re.search(r"\b(\d{6})\b", subj + " " + body)
                    if mm:
                        if debug:
                            print(f"    [otp hit] {subj[:40]} code={mm.group(1)}", flush=True)
                        m.logout()
                        return mm.group(1)
            try:
                m.logout()
            except Exception:
                pass
        except Exception as e:
            if debug:
                print(f"    [imap exc {str(e)[:70]}]", flush=True)
        time.sleep(6)
    return None


async def click_continue(page):
    for sel in ["button:has-text('Continue')", "button:has-text('Verify')",
                "button:has-text('Submit')", "button[type='submit']"]:
        b = await page.query_selector(sel)
        if b:
            try:
                await b.click(timeout=5000)
                return True
            except Exception:
                continue
    return False


async def handle_turnstile(page, attempts=8, rounds=4):
    from turnstile.solve import _click_turnstile_checkbox
    for r in range(rounds):
        has = any("challenges.cloudflare.com" in f.url for f in page.frames)
        if not has:
            return True
        try:
            await _click_turnstile_checkbox(page, attempts=attempts)
        except Exception:
            pass
        await page.wait_for_timeout(4000)
    return not any("challenges.cloudflare.com" in f.url for f in page.frames)


async def wait_form(page, timeout_s=120):
    t0 = time.time()
    clk = 0
    while time.time() - t0 < timeout_s:
        try:
            if await page.query_selector("input[name='email'], input[type='email']"):
                return True
        except Exception:
            pass
        if clk < 5 and any("challenges.cloudflare.com" in f.url for f in page.frames):
            clk += 1
            try:
                await handle_turnstile(page, attempts=6, rounds=2)
            except Exception:
                pass
        await page.wait_for_timeout(2000)
    return False


async def farm_one(email_addr, password, imap_cfg, tag="0"):
    import cloakbrowser

    captured = {"auth_body": None}

    async def on_resp(resp):
        try:
            if "user_management/authenticate" in resp.url and resp.request.method == "POST":
                try:
                    body = await resp.text()
                    if '"access_token"' in body:
                        captured["auth_body"] = body
                        print("    [TOKEN CAPTURED]", flush=True)
                except Exception:
                    pass
        except Exception:
            pass

    async with await cloakbrowser.launch_async(headless=True, humanize=True, proxy=WARP) as browser:
        page = await browser.new_page()
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        await page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=45000)
        ok = False
        for rnd in range(3):
            ok = await wait_form(page, 120)
            if ok:
                break
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
        if not ok:
            print("[!] CF/form never cleared", flush=True)
            return None
        print("[*] form ready:", await page.title(), flush=True)

        ti = await page.query_selector_all("input[type='text'], input[type='email']")
        if len(ti) < 3:
            print("[!] form inputs missing", flush=True)
            return None
        fn, ln = "Agus", "Wijaya"
        names = [("Agus", "Wijaya"), ("Budi", "Santoso"), ("Sari", "Dewi"), ("Rian", "Kusuma")]
        fn, ln = names[int(time.time()) % len(names)]
        await ti[0].fill(fn)
        await ti[1].fill(ln)
        await ti[2].fill(email_addr)
        await page.wait_for_timeout(1200)
        await click_continue(page)
        await page.wait_for_timeout(6000)
        await handle_turnstile(page, attempts=8, rounds=3)

        pw = await page.query_selector("input[type='password']")
        if pw:
            print("[*] password page", flush=True)
            await handle_turnstile(page, attempts=8, rounds=3)
            await pw.fill(password)
            await page.wait_for_timeout(900)
            await click_continue(page)
            await page.wait_for_timeout(7000)
            await handle_turnstile(page, attempts=8, rounds=3)
        print("[*] after password:", page.url[:110], flush=True)

        if "email-verification" in page.url:
            print(f"[*] waiting OTP -> {email_addr}", flush=True)
            otp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: fetch_otp_imap(imap_cfg["host"], imap_cfg["user"], imap_cfg["pw"], email_addr, timeout_s=300))
            if not otp:
                print("[!] OTP timeout", flush=True)
                return None
            print(f"[*] OTP: {otp}", flush=True)
            for ci in await page.query_selector_all("input"):
                try:
                    await ci.fill(otp, timeout=2500)
                    break
                except Exception as e:
                    if "destroyed" in str(e):
                        break
            await page.wait_for_timeout(9000)

        for _ in range(10):
            if "console.river.ai" in page.url and "login." not in page.url:
                break
            await page.wait_for_timeout(2500)
        print("[*] landed:", page.url[:100], flush=True)

        # wait for token capture (authenticate fires on callback)
        for _ in range(12):
            if captured["auth_body"]:
                break
            await page.wait_for_timeout(2500)

        auth_body = captured["auth_body"]
        if not auth_body:
            print("[!] no token captured", flush=True)
            return None
        auth = json.loads(auth_body)
        tok = auth["access_token"]
        print(f"[*] token len={len(tok)} user={(auth.get('user') or {}).get('email')}", flush=True)

        # mint key via Bearer
        import urllib.request
        def api(method, path, body=None):
            req = urllib.request.Request(
                "https://console.river.ai" + path,
                data=json.dumps(body).encode() if body else None, method=method,
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                         "Accept": "application/json",
                         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36",
                         "Referer": "https://console.river.ai/", "Origin": "https://console.river.ai"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode(errors="replace")

        st, me = api("GET", "/api/me")
        st2, bal = api("GET", "/api/billing/account")
        st3, kr = api("POST", "/api/keys", {"name": "default"})
        print(f"[*] me={st} bal={st2} key={st3}", flush=True)
        keyobj = json.loads(kr) if st3 == 200 else {}
        rvkey = keyobj.get("key")
        balance = json.loads(bal).get("balance_usd") if st2 == 200 else None
        if not rvkey:
            print("[!] key mint failed:", kr[:200], flush=True)
            return None

        # verify key works on inference
        vreq = urllib.request.Request(
            "https://api.river.ai/v1/chat/completions",
            data=json.dumps({"model": "deepseek-ai/DeepSeek-V4.1-Flash",
                             "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {rvkey}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(vreq, timeout=45) as r:
                verify_ok = r.status == 200
        except Exception:
            verify_ok = False

        print(f"[*] BALANCE=${balance} KEY={rvkey[:22]}... verify={verify_ok}", flush=True)
        rec = {
            "email": email_addr, "password": password,
            "user_id": (auth.get("user") or {}).get("id"),
            "access_token": tok, "refresh_token": auth.get("refresh_token"),
            "api_key": rvkey, "key_id": keyobj.get("id"), "balance_usd": balance,
            "verify_chat_200": verify_ok, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "VERIFIED" if verify_ok else "KEY_MINTED",
        }
        with open(OUT, "a") as f:
            f.write(json.dumps(rec) + "\n")
        try:
            jf = os.path.join(JARDIR, "river_jar_" + email_addr.replace('@', '_at_').replace('+', '_') + ".json")
            with open(jf, "w") as f:
                json.dump(await page.context.cookies(), f, indent=1)
        except Exception:
            pass
        return rec


async def main():
    email_addr = sys.argv[1]
    password = sys.argv[2] if len(sys.argv) > 2 else "ChangeMe!2026"
    env = load_env()
    imap_cfg = {"host": env.get("IMAP_HOST"), "user": env.get("IMAP_USER"), "pw": env.get("IMAP_APP_PASS")}
    r = await farm_one(email_addr, password, imap_cfg)
    print("[RESULT]", "OK " + (r or {}).get("api_key", "")[:24] if r else "FAILED", flush=True)


asyncio.run(main())
