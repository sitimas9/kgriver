#!/usr/bin/env python3
"""River Google-OAuth farmer (DrissionPage + Chrome, proven myrikko pattern)."""
import sys, time, json, os
from human_pace import pace, jitter_wait, typo_walk, delay

EMAIL = sys.argv[1]
PASSWORD = sys.argv[2]
PROXY = os.environ.get("KGR_PROXY", "http://127.0.0.1:9130")
CID = "client_01M0TG5W3GBNMFS87K40PYHM98"

def log(*a):
    print("[*]", *a, flush=True)

from DrissionPage import ChromiumPage, ChromiumOptions

def make_page():
    co = ChromiumOptions()
    co.headless(True)
    co.set_argument("--proxy-server=%s" % PROXY.replace("http://", ""))
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    return ChromiumPage(co)

def _js_next(page, elem_id):
    try:
        page.run_cdp("Runtime.evaluate",
            expression=('(()=>{const b=document.getElementById("%s");'
                        'if(b){b.click();return "ok"}return null})()' % elem_id),
            returnByValue=True)
    except Exception:
        pass


def glogin(page, email, password):
    """Fill Google email+password on current page. Returns True if past login."""
    pw = None
    inp = None
    for sel in ("tag:input@id=identifierId", "@type=email",
                "tag:input@@name=identifier", "@name=identifier",
                "tag:input@@name=identifierId"):
        try:
            inp = page.ele(sel, timeout=8)
            if inp:
                break
        except Exception:
            continue
    if inp:
        log("filling email...")
        pace("read_form", cap=8.0)          # scan page before typing
        try:
            inp.clear()
        except Exception:
            pass
        typo_walk(inp, email)               # human typing w/ jitter
        pace("think")
        try:
            page.ele("tag:div@id=identifierNext", timeout=6).click()
        except Exception:
            page.actions.key_down("Enter").key_up("Enter")
        pace("page_load")
        # poll up to ~40s for the password step
        for _ in range(20):
                time.sleep(2)
                for sel in ("tag:input@id=password", "tag:input@name=Passwd",
                            "input#password", "tag:input@type=password"):
                    try:
                        c = page.ele(sel, timeout=2)
                    except Exception:
                        c = None
                    if c is not None:
                        try:
                            if c.states.is_displayed:
                                pw = c
                                break
                        except Exception:
                            pw = c
                            break
                if pw:
                    log("pw field found")
                    break
                log("  wait pw: %s | %s" % ((page.url or "")[:60],
                                            (page.title or "")[:40]))
    if not pw:
        return False
    log("filling password...")
    pace("think")
    typo_walk(pw, password)
    pace("think")
    try:
        page.ele("tag:div@id=passwordNext", timeout=6).click()
    except Exception:
        page.actions.key_down("Enter").key_up("Enter")
    pace("post_submit")
    return True

def main():
    page = make_page()
    try:
        su = ("https://login.console.river.ai/sign-up?client_id=%s"
              "&redirect_uri=https%%3A%%2F%%2Fconsole.river.ai%%2Fauth%%2Fcallback"
              % CID)
        log("goto signup")
        page.get(su)
        # wait for app mount (reload on 'failed to load')
        glink = None
        for i in range(20):
            time.sleep(4)
            try:
                html = page.html or ""
            except Exception:
                html = ""
            if "failed to load" in html.lower():
                log("fallback -> reload")
                try:
                    page.ele("tag:button@@text():Reload", timeout=2).click()
                except Exception:
                    page.get(su)
                continue
            glink = _find_google_link(page)
            if glink:
                break
        if not glink:
            log("no google link found")
            return
        log("google link:", glink[:90])
        page.get(glink)
        time.sleep(4)
        log("url:", page.url[:80])
        if not glogin(page, EMAIL, PASSWORD):
            log("google login did not reach password/oauth")
            return
        _finish(page)
    finally:
        try:
            page.quit()
        except Exception:
            pass


def _find_google_link(page):
    try:
        r = page.run_cdp("Runtime.evaluate",
            expression=("(()=>{for(const a of document.querySelectorAll('a[href]'))"
                        "{if(/google/i.test(a.innerText||''))return a.href;}return null})()"),
            returnByValue=True)
        return r.get("result", {}).get("value")
    except Exception:
        return None


def _finish(page):
    """Walk interstitials until back on console; dump if stuck."""
    import re as _re
    for i in range(30):
        u = (page.url or "")
        log("hop %d: %s" % (i, u[:90]))
        if u.startswith("https://console.river.ai/") and "/auth/callback" not in u:
            break
        # Google interstitial: try known advance buttons by label AND id
        for lbl in ("Continue", "Allow", "Lanjutkan", "Setuju", "I understand",
                    "Next", "Confirm", "Yes"):
            try:
                b = page.ele("tag:button@@text():%s" % lbl, timeout=1)
                if b:
                    b.click(); time.sleep(2); break
            except Exception:
                continue
        for bid in ("confirm", "submit", "next", "identifierNext"):
            try:
                page.run_cdp("Runtime.evaluate",
                    expression=('(()=>{const b=document.getElementById("%s");'
                                'if(b){b.click();return "ok"}return null})()' % bid),
                    returnByValue=True)
            except Exception:
                pass
        time.sleep(2)
        if i == 6 and "accounts.google.com" in u:
            try:
                h = page.html or ""
                txt = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", h))
                log("STUCK-TEXT:", txt[:600])
                for t in _re.findall(r"<input[^>]*>", h)[:6]:
                    log("STUCK-IN:", _re.sub(r"\s+", " ", t)[:180])
            except Exception as e:
                log("dump err", e)
    log("FINAL URL:", page.url[:120])
    try:
        page.get_screenshot(path="/tmp/rg_final.png", full_page=True)
        log("shot /tmp/rg_final.png")
    except Exception as e:
        log("shot err", e)
    time.sleep(1.5)
    try:
        cookies = page.cookies(as_dict=True)
    except Exception:
        cookies = {}
    outp = os.path.expanduser("~/river-farmer/google_state_%s.json"
                              % EMAIL.split("@")[0])
    json.dump({"email": EMAIL, "url": page.url, "cookies": cookies},
              open(outp, "w"), indent=2)
    log("saved", outp)


if __name__ == "__main__":
    main()

