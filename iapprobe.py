#!/usr/bin/env python3
"""Drive email->password->dump whatever /challenge/iap shows."""
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
    for _ in range(20):
        time.sleep(4)
        h = page.html or ""
        if "failed to load" in h.lower():
            try:
                page.ele("tag:button@@text():Reload", timeout=2).click()
            except Exception:
                page.get(su)
            continue
        try:
            a = page.ele("tag:a@@href:GoogleOAuth", timeout=2)
        except Exception:
            a = None
        if a:
            glink = a.attr("href")
            break
    page.get(glink)
    time.sleep(5)
    page.ele("tag:input@id=identifierId", timeout=15).input(EMAIL, clear=True)
    time.sleep(1)
    try:
        page.ele("tag:div@id=identifierNext", timeout=5).click()
    except Exception:
        page.actions.key_down("Enter").key_up("Enter")
    pw = None
    for _ in range(20):
        time.sleep(2)
        try:
            pw = page.ele("tag:input@id=password", timeout=2)
        except Exception:
            pw = None
        if pw:
            break
    log("pw found:", bool(pw))
    if pw:
        try:
            pw.input(PW, clear=True)
        except Exception:
            pw.clear(by_js=True); pw.input(PW)
        time.sleep(1)
        try:
            page.ele("tag:div@id=passwordNext", timeout=3).click()
        except Exception:
            page.actions.key_down("Enter").key_up("Enter")
    time.sleep(8)
    log("url:", page.url[:120])
    html = page.html or ""
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    log("TEXT:", txt[:700])
    ins = re.findall(r"<input[^>]*>", html)
    for t in ins[:8]:
        log("IN:", re.sub(r"\s+", " ", t)[:200])
    try:
        page.get_screenshot(path="/tmp/iap_step.png", full_page=True)
        log("shot /tmp/iap_step.png")
    except Exception as e:
        log("shot err", e)
    page.quit()


main()
