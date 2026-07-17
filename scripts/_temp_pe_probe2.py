import json, re, urllib.request, ssl
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json,*/*"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")

results = {}

# StockAnalysis index NDX
for path in [
    "https://stockanalysis.com/etf/qqq/",
    "https://stockanalysis.com/index/ndx/",
    "https://stockanalysis.com/index/^ndx/",
    "https://stockanalysis.com/quote/index/NDX/",
]:
    try:
        html = get(path)
        pe = None
        for pat in [
            r'data-test="pe-ratio"[^>]*>([0-9.]+)',
            r'>PE Ratio</span>\s*<span[^>]*>([0-9.]+)',
            r'PE Ratio.*?text-gray-900[^>]*>([0-9.]+)',
            r'"peRatio"\s*:\s*"?([0-9.]+)"?',
            r'PE Ratio</[^>]+>.*?>([0-9]+\.[0-9]+)<',
        ]:
            m = re.search(pat, html, re.I|re.S)
            if m:
                pe = m.group(1)
                break
        results[path] = {"pe": pe, "status": "ok" if pe else "no_pe", "code": html[:200]}
    except Exception as e:
        results[path] = {"err": str(e)}

# MarketWatch QQQ
try:
    html = get("https://www.marketwatch.com/investing/fund/qqq")
    m = re.search(r"Trailing P/E[^0-9]*([0-9]+\.[0-9]+)", html, re.I)
    results["marketwatch_qqq"] = {"pe": m.group(1) if m else None, "has_trailing": "Trailing" in html}
except Exception as e:
    results["marketwatch_qqq"] = {"err": str(e)}

# CNBC QQQ
try:
    html = get("https://www.cnbc.com/quotes/QQQ")
    m = re.search(r"PE Ratio[^0-9]*([0-9]+\.[0-9]+)", html, re.I|re.S)
    results["cnbc"] = {"pe": m.group(1) if m else None}
except Exception as e:
    results["cnbc"] = {"err": str(e)}

# Multpl search nasdaq in sitemap-ish
try:
    html = get("https://www.multpl.com/")
    links = re.findall(r'href="(/[^"]*nasdaq[^"]*)"', html, re.I)
    results["multpl_links"] = links[:20]
except Exception as e:
    results["multpl"] = {"err": str(e)}

# Nasdaq data link API (public)
try:
    url = "https://api.nasdaq.com/api/quote/QQQ/summary?assetclass=etf"
    html = get(url)
    data = json.loads(html)
    results["nasdaq_api_qqq"] = data
except Exception as e:
    results["nasdaq_api_qqq"] = {"err": str(e)}

# Nasdaq NDX index summary
try:
    url = "https://api.nasdaq.com/api/quote/NDX/summary?assetclass=index"
    html = get(url)
    data = json.loads(html)
    # dig for pe
    s = json.dumps(data)[:3000]
    results["nasdaq_api_ndx"] = {"keys": list(data.keys()), "snippet": s}
except Exception as e:
    results["nasdaq_api_ndx"] = {"err": str(e)}

# Finviz quote (not etf page)
try:
    html = get("https://finviz.com/quote.ashx?t=QQQ")
    m = re.search(r"P/E</td>\s*<td[^>]*>([^<]+)", html, re.I|re.S)
    results["finviz_quote"] = {"pe_raw": m.group(1).strip() if m else None}
except Exception as e:
    results["finviz_quote"] = {"err": str(e)}

# yfinance with curl-like headers
try:
    import yfinance as yf
    t = yf.Ticker("QQQ")
    results["yfinance_info"] = {"trailingPE": (t.info or {}).get("trailingPE"), "forwardPE": (t.info or {}).get("forwardPE")}
except Exception as e:
    results["yfinance_info"] = {"err": str(e)}

# akshare US?
try:
    import akshare as ak
    # search akshare for nasdaq pe
    funcs = [x for x in dir(ak) if 'nasdaq' in x.lower() or 'us' in x.lower() and 'pe' in x.lower()]
    results["akshare_funcs_sample"] = funcs[:15]
except Exception as e:
    results["akshare"] = {"err": str(e)}

print(json.dumps(results, ensure_ascii=False, indent=2))
