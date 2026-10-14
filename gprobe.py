#!/usr/bin/env python3
"""Native-only probe: email -> Next -> dump REAL page.html on the password step."""
import sys, time, re
from DrissionPage import ChromiumPage, ChromiumOptions

PORT = sys.argv[1] if len(sys.argv) > 1 else "9130"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else "AbimanaTanishaNovitasari@deaal.xyz"
PW = sys.argv[3] if len(sys.argv) > 3 else "qwertyui"
CID = "client_01M0TG5W3GBNMFS87K40PYHM98"


def log(*a):
    print("[*]", *a, flush=True)


def main():
    co = ChromiumOptions()
    co.headless(True)
    co.set_argument("--proxy-server=127.0.0.1:%s" % PORT)
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    page = ChromiumPage(co)
    su = ("https://login.console.river.ai/sign-up?client_id=%s"
          "&redirect_uri=https%%3A%%2F%%2Fconsole.river.ai%%2Fauth%%2Fcallback" % CID)
    page.get(su)
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
        for sel in ("tag:a@@text():Continue with Google",
                    "tag:a@@text():Google",
                    "tag:a@@href:GoogleOAuth"):
            try:
                a = page.ele(sel, timeout=2)
            except Exception:
                a = None
            if a:
                glink = a.attr("href")
                break
        if glink:
            log("glink found @", i)
            break
        if i in (4, 9, 14):
            log("  still waiting @", i, "| url:", (page.url or "")[:60])
    log("glink:", (glink or "NONE")[:100])
    if not glink:
        page.quit(); return
    page.get(glink)
    time.sleep(5)
    log("url1:", page.url[:100])
    # email via native ele
    try:
        inp = page.ele("tag:input@id=identifierId", timeout=15)
    except Exception:
        inp = None
    if not inp:
        log("EMAIL FIELD NOT FOUND")
        try:
            log("html snippet:", re.sub(r"\s+", " ", page.html)[:400])
        except Exception:
            pass
        page.quit(); return
    inp.input(EMAIL, clear=True)
    time.sleep(1)
    try:
        page.ele("tag:div@id=identifierNext", timeout=5).click()
    except Exception:
        page.actions.key_down("Enter").key_up("Enter")
    # wait for password step, poll by URL
    for i in range(15):
        time.sleep(2)
        u = page.url or ""
        if "challenge/pwd" in u or "signin/v2/challenge" in u:
            log("reached pw step @", i, u[:90])
            break
    time.sleep(3)
    log("url2:", page.url[:110])
    html = page.html or ""
    log("html len:", len(html))
    # find all input tags
    for m in re.finditer(r"<input[^>]*>", html):
        t = m.group(0)
        if "password" in t.lower() or "passwd" in t.lower() or "identifier" in t.lower():
            log("INPUT:", re.sub(r"\s+", " ", t)[:220])
    # text
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = re.sub(r"\s+", " ", txt)
    log("TEXT:", txt[:400])
    try:
        page.get_screenshot(path="/tmp/pw_step.png", full_page=True)
        log("shot /tmp/pw_step.png")
    except Exception as e:
        log("shot err", e)
    page.quit()


main()
