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

def plain_imgs(url):
    """Static HTML fallback for sites that block headless browsers."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    import time
    r = None
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "he-IL,he;q=0.9"}, timeout=40); break
        except Exception as e:
            print("  plain fetch failed", attempt, e); time.sleep(5 * (attempt + 1))
    if r is None: return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        ss = img.get("srcset") or img.get("data-srcset")
        if ss:
            parts = sorted((p.strip().split() for p in ss.split(",") if p.strip()), key=lambda p: -int((p[1] if len(p) > 1 else "0").rstrip("wx") or 0))
            src = parts[0][0]
        p, ctx = img.parent, ""
        for _ in range(4):
            if p is None: break
            ctx = p.get_text(" ", strip=True)
            if ctx: break
            p = p.parent
        if src and not re.search(r"\.svg|facebook|logo|pixel|icon", src, re.I):
            out.append({"alt": img.get("alt", ""), "ctx": ctx[:120], "src": urljoin(r.url, src), "w": 0, "h": 0})
    return out

def visit(pg, url):
    try:
        pg.goto(url, wait_until="networkidle", timeout=60000)
    except Exception as e:
        print("  goto timeout/err", url, str(e)[:80])
    pg.wait_for_timeout(2000)
    for _ in range(6):
        pg.mouse.wheel(0, 4000); pg.wait_for_timeout(500)
    return pg.evaluate(COLLECT)

FACES = None
def face_box(im):
    """Largest frontal face as (x, y, w, h), or None."""
    global FACES
    import cv2, numpy as np
    if FACES is None:
        FACES = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    g = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2GRAY)
    found = FACES.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(max(40, im.width // 25),) * 2)
    if len(found) == 0: return None
    return max(found, key=lambda f: f[2] * f[3])

REMBG = None
BACKDROP = (214, 218, 224)
def same_backdrop(im):
    """Cut the person out and put them on one neutral backdrop, so the studio colour of
    each party's photo shoot doesn't give the answer away."""
    global REMBG
    try:
        from rembg import remove, new_session
        if REMBG is None: REMBG = new_session("u2net_human_seg")
        cut = remove(im, session=REMBG, alpha_matting=True)
        bg = Image.new("RGBA", cut.size, BACKDROP + (255,))
        return Image.alpha_composite(bg, cut).convert("RGB")
    except Exception as e:
        print("  background removal failed:", e)
        return im

def save(src, dest, referer):
    import time
    for attempt in range(4):
        try:
            r = requests.get(src, timeout=40, headers={"User-Agent": UA, "Referer": referer, "Accept": "image/avif,image/webp,image/*,*/*"})
            r.raise_for_status(); break
        except Exception:
            if attempt == 3: raise
            time.sleep(6 * (attempt + 1))
    im = Image.open(io.BytesIO(r.content)).convert("RGBA")
    bg = Image.new("RGBA", im.size, BACKDROP + (255,))   # neutral grey behind transparent cut-outs
    im = Image.alpha_composite(bg, im).convert("RGB")
    w, h = im.size
    try:
        f = face_box(im)
    except Exception as e:
        print("  face detection unavailable:", e); f = None
    if f is not None:
        fx, fy, fw, fh = f
        side = int(min(w, h, fh * 2.6))
        cx, cy = fx + fw / 2, fy + fh / 2 + fh * 0.22
        left = int(min(max(cx - side / 2, 0), w - side))
        top = int(min(max(cy - side / 2, 0), h - side))
        how = "face"
    else:
        side = min(w, h); left = (w - side) // 2; top = min(int(h * 0.04), h - side) if h > w else 0
        how = "no-face"
    im = im.crop((left, top, left + side, top + side)).resize((360, 360), Image.LANCZOS)
    im = same_backdrop(im)
    im.save(dest, "JPEG", quality=82, optimize=True, progressive=True)
    return how

FORCE = "--force" in sys.argv
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--disable-http2"])
        pg = b.new_page(user_agent=UA, locale="he-IL", viewport={"width": 1400, "height": 1000})
        for party, cfg in LISTS.items():
            names = [cfg.get("aliases", {}).get(n, n) for n in cfg["names"]]
            skip = {int(k) for k in cfg.get("skip", [])}
            todo = [i for i in range(len(names)) if i not in skip and (FORCE or not (OUT / f"{party}-{i + 1:02d}.jpg").exists())]
            if not todo:
                print(f"[{party}] complete"); continue
            pages = list(cfg.get("pages", []))
            found = {}
            for k, url in cfg.get("direct", {}).items():
                found[int(k)] = (url, pages[0] if pages else url, "direct")
            pool = []
            members = {}   # idx -> (url, og, imgs)
            for u in pages:
                if all(i in found for i in todo): break
                d = visit(pg, u)
                print(f"[{party}] {u} -> {len(d['imgs'])} imgs, title {d['title'][:50]!r}")
                if len(d["imgs"]) < 10:
                    d["imgs"] += plain_imgs(u)
                    print(f"[{party}] plain fetch -> {len(d['imgs'])} imgs")
                pool += [(i, u) for i in d["imgs"]]
                if cfg.get("follow"):
                    for l in d["links"]:
                        if re.search(cfg["follow"], l["h"]):
                            for idx, n in enumerate(names):
                                if idx not in members and score(n, l["t"]) >= 0.99:
                                    md = visit(pg, l["h"])
                                    members[idx] = (l["h"], md["og"], md["imgs"])
            if members:
                # An image that shows up on several member pages is site chrome, not the candidate.
                from collections import Counter
                og_count = Counter(m[1] for m in members.values())
                img_count = Counter(src for m in members.values() for src in {i["src"] for i in m[2]})
                for idx, (url, og, imgs) in members.items():
                    if og and og_count[og] == 1:
                        found[idx] = (og, url, "member og"); continue
                    own = [i for i in imgs if img_count[i["src"]] == 1 and score(names[idx], i["alt"] + " " + i["ctx"] + " " + requests.utils.unquote(i["src"]).split("/")[-1].replace("-", " ")) >= 0.5]
                    if own:
                        best = max(own, key=lambda i: (score(names[idx], i["alt"] + " " + i["ctx"]), (i["w"] or 0) * (i["h"] or 0)))
                        found[idx] = (best["src"], url, "member img")
            for idx, n in enumerate(names):
                if idx in found: continue
                ranked = sorted(pool, key=lambda x: -max(score(n, x[0]["alt"]), score(n, x[0]["ctx"]), score(n, requests.utils.unquote(x[0]["src"]).split("/")[-1])))
                if ranked:
                    b0 = ranked[0][0]
                    s0 = max(score(n, b0["alt"]), score(n, b0["ctx"]), score(n, requests.utils.unquote(b0["src"]).split("/")[-1]))
                    if s0 >= 0.99: found[idx] = (b0["src"], ranked[0][1], f"list ({b0['alt'] or b0['ctx'][:40]})")
                    else:
                        print(f"  miss {party} {idx+1} {n}; nearest:", [ (r[0]['alt'][:30], r[0]['ctx'][:50], r[0]['src'][-60:]) for r in ranked[:3] ])
            from collections import Counter
            dup = Counter(v[0] for v in found.values())
            for idx in [i for i, v in found.items() if dup[v[0]] > 1]:
                print(f"  shared photo dropped for {party} {idx + 1}"); del found[idx]
            for idx, n in enumerate(names):
                if idx not in todo: continue
                dest = OUT / f"{party}-{idx + 1:02d}.jpg"
                if idx not in found:
                    report.append(f"MISS {party} {idx + 1} {n}"); continue
                src, ref, how = found[idx]
                try:
                    crop = save(src, dest, ref)
                    report.append(f"OK   {party} {idx + 1} {n} <- {how} [{crop}] {src}")
                except Exception as e:
                    report.append(f"FAIL {party} {idx + 1} {n} {src} {e}")
        b.close()
    print("\n".join(report))
    src_file = OUT / "SOURCES.txt"
    old = src_file.read_text(encoding="utf-8").splitlines() if src_file.exists() and not FORCE else []
    src_file.write_text("\n".join(old + [r for r in report if r.startswith("OK")]) + "\n", encoding="utf-8")
    have = sorted(f.stem for f in OUT.glob("*.jpg"))
    (OUT / "manifest.json").write_text(json.dumps(have) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
