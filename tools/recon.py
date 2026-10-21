"""Print the structure of party team pages: images with alt text and nearby text, plus team-like links."""
import sys, re, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
for url in sys.argv[1:]:
    print("=" * 30, url)
    try:
        r = requests.get(url, headers=UA, timeout=25)
    except Exception as e:
        print("ERR", e); continue
    print("status", r.status_code, "final", r.url, "len", len(r.text))
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        t = a.get_text(" ", strip=True)
        if re.search(r"team|נבחרת|רשימ|מועמד|candidate|list|people|אנשים", a["href"] + " " + t, re.I):
            print("LINK", t[:40], "->", urljoin(r.url, a["href"]))
    n = 0
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
        p = img.parent
        ctx = ""
        for _ in range(4):
            if p is None: break
            ctx = p.get_text(" ", strip=True)
            if ctx: break
            p = p.parent
        print("IMG", repr(img.get("alt", ""))[:50], "|", ctx[:70], "|", urljoin(r.url, src)[:160], "| srcset" if img.get("srcset") else "")
        n += 1
        if n > 140: break
    # background-image styles
    for m in re.findall(r"background-image:\s*url\(['\"]?([^)'\"]+)", r.text)[:40]:
        print("BG", m[:160])
