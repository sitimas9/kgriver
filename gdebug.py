#!/usr/bin/env python3
"""Dump what DrissionPage actually sees on the Google signin page."""
import sys, time, os, json
from DrissionPage import ChromiumPage, ChromiumOptions

PORT = sys.argv[1] if len(sys.argv) > 1 else "9130"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else "AbimanaTanishaNovitasari@deaal.xyz"
CID = "client_01M0TG5W3GBNMFS87K40PYHM98"


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
        try:
            r = page.run_cdp("Runtime.evaluate",
                expression=("(()=>{for(const a of document.querySelectorAll('a[href]'))"
                            "{if(/google/i.test(a.innerText||''))return a.href;}return null})()"),
                returnByValue=True)
            glink = r.get("result", {}).get("value")
        except Exception:
            glink = None
        if glink:
            break
    print("[*] glink:", (glink or "NONE")[:110], flush=True)
    if not glink:
        return
    page.get(glink)
    time.sleep(5)
    print("[*] url:", page.url[:110], flush=True)
    r = page.run_cdp("Runtime.evaluate",
        expression="""(()=>{
          const out={inputs:[],buttons:[],text:(document.body.innerText||'').slice(0,400)};
          document.querySelectorAll('input').forEach(i=>out.inputs.push(
            `type=${i.type}|id=${i.id}|name=${i.name}|vis=${i.offsetParent!==null}|val=${(i.value||'').slice(0,20)}`));
          document.querySelectorAll('button,div[role=button]').forEach(b=>out.buttons.push(
            ((b.innerText||b.getAttribute('aria-label')||'').trim().slice(0,30))));
          return out;})()""",
        returnByValue=True)
    v = r.get("result", {}).get("value") or {}
    print("[inputs]", json.dumps(v.get("inputs", []), indent=1), flush=True)
    print("[buttons]", json.dumps(v.get("buttons", []), indent=1), flush=True)
    print("[text]", v.get("text", "")[:400], flush=True)
    page.get_screenshot(path="/tmp/gdebug.png", full_page=True)
    print("[*] shot /tmp/gdebug.png", flush=True)
    page.quit()


main()
