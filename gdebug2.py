#!/usr/bin/env python3
"""Dump the Google /challenge/pwd DOM after filling the email step."""
import sys, time, json
from DrissionPage import ChromiumPage, ChromiumOptions

PORT = sys.argv[1] if len(sys.argv) > 1 else "9130"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else "AbimanaTanishaNovitasari@deaal.xyz"
CID = "client_01M0TG5W3GBNMFS87K40PYHM98"


def js(page, expr):
    try:
        return page.run_cdp("Runtime.evaluate", expression=expr,
                            returnByValue=True).get("result", {}).get("value")
    except Exception as e:
        return f"ERR {e}"


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
        glink = js(page, ("(()=>{for(const a of document.querySelectorAll('a[href]'))"
                          "{if(/google/i.test(a.innerText||''))return a.href;}return null})()"))
        if glink and glink.startswith("http"):
            break
    print("[*] glink ok", flush=True)
    page.get(glink)
    time.sleep(5)
    print("[*] url1:", page.url[:100], flush=True)
    print("[*] email fill ->", js(page,
          '(()=>{const i=document.querySelector("#identifierId");'
          'if(!i)return "no-input";i.focus();i.value=%s;'
          'i.dispatchEvent(new Event("input",{bubbles:true}));'
          'return "set"})()' % json.dumps(EMAIL)), flush=True)
    time.sleep(1)
    print("[*] next ->", js(page,
          '(()=>{const b=document.getElementById("identifierNext");'
          'if(b){b.click();return "clicked"}return "no-btn"})()'), flush=True)
    for i in range(12):
        time.sleep(2)
        u = page.url or ""
        if "challenge" in u or "pwd" in u:
            print("[*] reached:", u[:100], flush=True)
            break
    print("[*] url2:", page.url[:110], flush=True)
    out = js(page, """(()=>{
      const o={inputs:[],title:document.title,bodylen:(document.body.innerText||'').length,
               txt:(document.body.innerText||'').slice(0,300)};
      document.querySelectorAll('input').forEach(i=>o.inputs.push(
        `t=${i.type}|id=${i.id}|name=${i.name}|cls=${(i.className||'').slice(0,20)}`));
      return o;})()""")
    print("[DOM]", json.dumps(out, indent=1), flush=True)
    page.get_screenshot(path="/tmp/gdebug2.png", full_page=True)
    page.quit()


main()
