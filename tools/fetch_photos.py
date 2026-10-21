"""Download candidate photos from the party websites into img/candidates/<party>-NN.jpg.

Runs in GitHub Actions (needs open internet). Each party has pages to visit; on every page
we collect images with their alt text, nearby text, and the page title/og:image, then match
them to candidate names with a fuzzy token match.
"""
import io, json, re, sys, difflib, pathlib, requests
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "img" / "candidates"
LISTS = json.loads((ROOT / "tools" / "candidates.json").read_text(encoding="utf-8"))

COLLECT = r"""() => {
  const ctx = (el) => { let p = el; for (let i = 0; i < 4 && p; i++, p = p.parentElement) { const t = (p.innerText || '').trim(); if (t) return t.replace(/\s+/g,' ').slice(0, 120); } return ''; };
  const big = (s) => { if (!s) return ''; const parts = s.split(',').map(x => x.trim().split(/\s+/)); parts.sort((a,b)=>parseInt(b[1]||0)-parseInt(a[1]||0)); return parts[0][0]; };
  const og = document.querySelector('meta[property="og:image"]');
  const imgs = [...document.images].map(i => ({ alt: i.alt || '', ctx: ctx(i), src: big(i.srcset) || i.currentSrc || i.src, w: i.naturalWidth, h: i.naturalHeight }))
    .filter(x => x.src && !/\.svg|facebook|logo|pixel|icon/i.test(x.src));
  const bgs = [...document.querySelectorAll('[style*="background-image"]')].map(e => ({ alt: '', ctx: ctx(e), src: (e.style.backgroundImage.match(/url\(["']?([^"')]+)/) || [])[1] || '' })).filter(x => x.src);
  const links = [...document.links].map(a => ({ t: (a.innerText || '').trim().replace(/\s+/g, ' '), h: a.href }));
  return { title: document.title, og: og && og.content, imgs: imgs.concat(bgs), links };
}"""

TITLES = r'ח"כ|עו"ד|ד"ר|הרב|אלוף|במיל\.?|במיל|פרופ\'?|רב-אלוף|\(|\)'
def norm(s):
    s = re.sub(TITLES, " ", s)
    s = re.sub(r"[\"'׳״`\-–.,:|!]", " ", s)
    return [t for t in s.split() if len(t) > 1]

def score(name, text):
    want, have = norm(name), norm(text)
    if not want or not have: return 0
    hit = 0
    for w in want:
        best = max(difflib.SequenceMatcher(None, w, h).ratio() for h in have)
        hit += 1 if best >= 0.8 else 0
    return hit / len(want)

def visit(pg, url):
    try:
        pg.goto(url, wait_until="networkidle", timeout=60000)
    except Exception as e:
        print("  goto timeout/err", url, str(e)[:80])
    pg.wait_for_timeout(2000)
    for _ in range(6):
        pg.mouse.wheel(0, 4000); pg.wait_for_timeout(500)
    return pg.evaluate(COLLECT)

def save(src, dest, referer):
    r = requests.get(src, timeout=40, headers={"User-Agent": UA, "Referer": referer})
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content))
    im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, (200, 204, 210, 255))   # neutral grey behind transparent cut-outs
    im = Image.alpha_composite(bg, im).convert("RGB")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = 0 if h > w else 0          # portraits: keep the top (faces are high in the frame)
    if h > w: top = min(int(h * 0.04), h - side)
    im = im.crop((left, top, left + side, top + side)).resize((320, 320), Image.LANCZOS)
    im.save(dest, "JPEG", quality=82, optimize=True, progressive=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA, locale="he-IL", viewport={"width": 1400, "height": 1000})
        for party, cfg in LISTS.items():
            names = cfg["names"]
            pages = list(cfg.get("pages", []))
            found = {}
            # Party list pages
            pool = []
            for u in pages:
                d = visit(pg, u)
                print(f"[{party}] {u} -> {len(d['imgs'])} imgs")
                pool += [(i, u) for i in d["imgs"]]
                # member pages linked by name (one page per candidate)
                if cfg.get("follow"):
                    for l in d["links"]:
                        if re.search(cfg["follow"], l["h"]):
                            for idx, n in enumerate(names):
                                if idx not in found and score(n, l["t"]) >= 0.99:
                                    md = visit(pg, l["h"])
                                    src = md["og"] or (max(md["imgs"], key=lambda x: (x["w"] or 0) * (x["h"] or 0))["src"] if md["imgs"] else None)
                                    if src: found[idx] = (src, l["h"], "member-page")
            for idx, n in enumerate(names):
                if idx in found: continue
                best = max(pool, key=lambda x: max(score(n, x[0]["alt"]), score(n, x[0]["ctx"])), default=None)
                if best:
                    s = max(score(n, best[0]["alt"]), score(n, best[0]["ctx"]))
                    if s >= 0.99: found[idx] = (best[0]["src"], best[1], f"list ({best[0]['alt'] or best[0]['ctx'][:40]})")
            for idx, n in enumerate(names):
                dest = OUT / f"{party}-{idx + 1:02d}.jpg"
                if idx not in found:
                    report.append(f"MISS {party} {idx + 1} {n}"); continue
                src, ref, how = found[idx]
                try:
                    save(src, dest, ref)
                    report.append(f"OK   {party} {idx + 1} {n} <- {how} {src}")
                except Exception as e:
                    report.append(f"FAIL {party} {idx + 1} {n} {src} {e}")
        b.close()
    print("\n".join(report))
    (OUT / "SOURCES.txt").write_text("\n".join(r for r in report if r.startswith("OK")) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
