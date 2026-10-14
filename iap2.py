#!/usr/bin/env python3
"""email -> password -> dump the /challenge/iap page (no screenshot)."""
import sys, time, re
from DrissionPage import ChromiumPage, ChromiumOptions

PORT = sys.argv[1] if len(sys.argv) > 1 else "9130"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else "AdeliaDeliaKrisnanda@deaal.xyz"
PW = sys.argv[3] if len(sys.argv) > 3 else "qwertyui"
CID = "client_01M0TG5W3GBNMFS87K40PYHM98"


def log(*a):
    print("[*]", *a, flush=True)


def js(page, expr):
    try:
        return page.run_cdp("Runtime.evaluate", expression=expr,
                            returnByValue=True).get("result", {}).get("value")
    except Exception:
        return None


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
        if "failed to load" in (page.html or "").lower():
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
    log("glink found:", bool(glink))
    page.get(glink)
    time.sleep(5)
    page.ele("tag:input@id=identifierId", timeout=15).input(EMAIL, clear=True)
    time.sleep(1.2)
    try:
        page.ele("tag:div@id=identifierNext", timeout=6).click()
    except Exception:
        page.actions.key_down("Enter").key_up("Enter")
    pw = None
    for _ in range(20):
        time.sleep(2)
        try:
            c = page.ele("tag:input@id=password", timeout=2)
        except Exception:
            c = None
        if c is not None:
            pw = c
            break
    log("pw found:", bool(pw))
    if pw:
        pw.input(PW, clear=True)
        time.sleep(1.2)
        try:
            page.ele("tag:div@id=passwordNext", timeout=6).click()
        except Exception:
            page.actions.key_down("Enter").key_up("Enter")
    # wait then dump
    for i in range(8):
        time.sleep(3)
        u = page.url or ""
        log("u%d: %s" % (i, u[:80]))
        if "iap" in u or "challenge" in u:
            break
    title = js(page, "document.title")
    txt = js(page, "(()=>{const b=document.body;return b?(b.innerText||'').slice(0,900):'NOBODY'})()")
    log("TITLE:", title)
    log("BODYTEXT:", (txt or "")[:900])
    html = page.html or ""
    log("html len:", len(html))
    for t in re.findall(r"<input[^>]*>", html)[:10]:
        log("IN:", re.sub(r"\s+", " ", t)[:200])
    for t in re.findall(r"<h1[^>]*>.*?</h1>", html, re.S)[:3]:
        log("H1:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))[:200])
    with open("/tmp/iap_dump.html", "w") as f:
        f.write(html)
    log("saved /tmp/iap_dump.html")
    page.quit()


main()
