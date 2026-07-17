"""Temp probe for QQQ/Nasdaq100 PE sources - delete after test"""
import json, re, urllib.request, urllib.error, ssl
from html.parser import HTMLParser

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def get(url, headers=None, timeout=25):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")

results = {}

# 1 Multpl Nasdaq 100
for url in [
    "https://www.multpl.com/nasdaq-100-pe-ratio",
    "https://www.multpl.com/nasdaq-pe-ratio",
    "https://www.multpl.com/nasdaq-100-pe-ratio/table/by-month",
]:
    try:
        html = get(url)
        m = re.search(r"Current.*?PE Ratio.*?:?\s*</b>\s*([0-9.]+)", html, re.I|re.S)
        if not m:
            m = re.search(r"Current Nasdaq.*?PE Ratio is ([0-9.]+)", html, re.I)
        if not m:
            m = re.search(r"PE Ratio.*?([3-5][0-9]\.[0-9]+)", html[:8000])
        results[url] = {"ok": bool(m), "pe": float(m.group(1)) if m else None, "len": len(html)}
    except Exception as e:
        results[url] = {"ok": False, "err": str(e)}

# 2 StockAnalysis ETF QQQ
try:
    html = get("https://stockanalysis.com/etf/qqq/")
    m = re.search(r"PE Ratio.*?([0-9]+\.[0-9]+)", html, re.I|re.S)
    # try structured
    m2 = re.search(r'"peRatio"\s*:\s*([0-9.]+)', html)
    m3 = re.search(r'>PE Ratio</[^>]+>.*?>([0-9.]+)<', html, re.S)
    results["stockanalysis_qqq"] = {"pe_json": m2.group(1) if m2 else None, "pe_html": m3.group(1) if m3 else None, "len": len(html)}
except Exception as e:
    results["stockanalysis_qqq"] = {"err": str(e)}

# 3 Macrotrends NDX
try:
    html = get("https://www.macrotrends.net/2577/nasdaq-100-pe-ratio")
    nums = re.findall(r"([3-5][0-9]\.[0-9]{1,2})", html[:15000])
    results["macrotrends"] = {"sample_nums": nums[:5], "title_ok": "Nasdaq" in html}
except Exception as e:
    results["macrotrends"] = {"err": str(e)}

# 4 Stooq QQQ
try:
    html = get("https://stooq.com/q/?s=qqq.us")
    results["stooq"] = {"snippet": html[html.find("P/E"):html.find("P/E")+80] if "P/E" in html else "no pe"}
except Exception as e:
    results["stooq"] = {"err": str(e)}

# 5 Yahoo chart API (lighter than yfinance)
try:
    url = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/QQQ?modules=defaultKeyStatistics,summaryDetail"
    html = get(url, headers={"Accept": "application/json"})
    data = json.loads(html)
    ks = data["quoteSummary"]["result"][0]
    pe = ks.get("defaultKeyStatistics", {}).get("trailingPE", {}).get("raw")
    if pe is None:
        pe = ks.get("summaryDetail", {}).get("trailingPE", {}).get("raw")
    results["yahoo_quoteSummary"] = {"pe": pe}
except Exception as e:
    results["yahoo_quoteSummary"] = {"err": str(e)}

# 6 Eastmoney push2 US QQQ
try:
    url = "https://push2.eastmoney.com/api/qt/stock/get?secid=105.QQQ&fields=f43,f57,f58,f162,f167,f116,f117"
    html = get(url)
    data = json.loads(html)
    d = data.get("data") or {}
    results["eastmoney_push2"] = {k: d.get(k) for k in ["f162","f167","f116","f117","f58"]}
except Exception as e:
    results["eastmoney_push2"] = {"err": str(e)}

# 7 Nasdaq.com index page
try:
    html = get("https://www.nasdaq.com/market-activity/quotes/nasdaq-ndx-pe-ratio")
    results["nasdaq_pe_page"] = {"has_pe": "P/E" in html or "PE Ratio" in html, "len": len(html)}
except Exception as e:
    results["nasdaq_pe_page"] = {"err": str(e)}

# 8 GuruFocus free snippet
try:
    html = get("https://www.gurufocus.com/etf/QQQ/summary")
    m = re.search(r"PE Ratio.*?([0-9]+\.[0-9]+)", html, re.I|re.S)
    results["gurufocus"] = {"pe": m.group(1) if m else None}
except Exception as e:
    results["gurufocus"] = {"err": str(e)}

# 9 Wallstreetzen / etf.com
try:
    html = get("https://www.etf.com/QQQ")
    m = re.search(r"P/E[^0-9]*([0-9]+\.[0-9]+)", html, re.I)
    results["etf_com"] = {"pe": m.group(1) if m else None}
except Exception as e:
    results["etf_com"] = {"err": str(e)}

# 10 investing.com indices nasdaq 100 pe
try:
    html = get("https://www.investing.com/indices/nq-100")
    m = re.search(r"P/E.*?([0-9]+\.[0-9]+)", html, re.I|re.S)
    results["investing_nq100"] = {"pe": m.group(1) if m else None, "blocked": "Just a moment" in html[:500]}
except Exception as e:
    results["investing_nq100"] = {"err": str(e)}

print(json.dumps(results, ensure_ascii=False, indent=2))
