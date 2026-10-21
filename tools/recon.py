"""Load pages in a real browser and print og:image, images (with nearby text) and internal links."""
import sys, json
from playwright.sync_api import sync_playwright

JS = r"""() => {
  const ctx = (el) => { let p = el; for (let i = 0; i < 5 && p; i++, p = p.parentElement) { const t = (p.innerText || '').trim(); if (t) return t.replace(/\s+/g,' ').slice(0, 80); } return ''; };
  const og = document.querySelector('meta[property="og:image"]');
  const imgs = [...document.images].map(i => [i.alt, ctx(i), i.currentSrc || i.src]).filter(x => !/svg|facebook|logo/i.test(x[2]));
  const bgs = [...document.querySelectorAll('[style*="background-image"]')].map(e => [ctx(e), e.style.backgroundImage]);
  const links = [...new Set([...document.links].map(a => (a.innerText||'').trim().replace(/\s+/g,' ').slice(0,40) + ' -> ' + a.href).filter(s => s.includes(location.hostname)))];
  return { title: document.title, og: og && og.content, imgs, bgs, links };
}"""
mode = sys.argv[1]
urls = sys.argv[2:]
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", locale="he-IL")
    for u in urls:
        print("=" * 20, u, flush=True)
        try:
            r = pg.goto(u, wait_until="networkidle", timeout=45000)
            pg.wait_for_timeout(2500)
            pg.mouse.wheel(0, 20000); pg.wait_for_timeout(1500)
            d = pg.evaluate(JS)
            print("status", r.status if r else None, "title", d["title"], "OG", d["og"])
            if mode in ("full", "imgs"):
                for i in d["imgs"][:120]: print("IMG", json.dumps(i, ensure_ascii=False)[:260])
                for i in d["bgs"][:60]: print("BG", json.dumps(i, ensure_ascii=False)[:260])
            if mode == "full":
                for l in d["links"][:150]: print("LINK", l[:200])
        except Exception as e:
            print("ERR", str(e)[:200])
    b.close()
