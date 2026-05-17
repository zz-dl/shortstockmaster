# app.py — ShortStockMaster Flask backend
# Short-term trading signals: sentiment, capital flow, momentum, news, AI
import json, os, re
from datetime import date, datetime, timedelta
import numpy as np
import pandas as pd
import requests as _req
import yfinance as yf
from flask import Flask, Response, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="/static")

_HEADERS = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"}
_DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-20cfd605d2a64ff5ab379a97c9c77365")
_DEEPSEEK_BASE = "https://api.deepseek.com"


class SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, pd.Series): return obj.tolist()
        if isinstance(obj, (date, datetime)): return str(obj)
        return super().default(obj)

def jdump(obj):
    return json.dumps(obj, cls=SafeEncoder, ensure_ascii=False)


# ── 腾讯行情解析 ─────────────────────────────────────────────────────────────

def _tencent_quote(codes: list) -> dict:
    """从腾讯行情获取实时数据，返回 {code: {...}} 字典。"""
    query = ",".join(codes)
    try:
        r = _req.get(f"http://qt.gtimg.cn/q={query}", headers=_HEADERS, timeout=6)
        result = {}
        for line in r.text.strip().split(";"):
            if '~' not in line:
                continue
            m = re.search(r'v_(\w+)="([^"]+)"', line)
            if not m:
                continue
            code_key = m.group(1)
            parts = m.group(2).split("~")
            if len(parts) < 50:
                continue
            result[code_key] = {
                "name":     parts[1],
                "code":     parts[2],
                "price":    float(parts[3]) if parts[3] else 0,
                "prev_close": float(parts[4]) if parts[4] else 0,
                "open":     float(parts[5]) if parts[5] else 0,
                "volume":   float(parts[36]) if parts[36] else 0,  # 手
                "amount":   float(parts[37]) if parts[37] else 0,  # 万元
                "chg_pct":  float(parts[32]) if parts[32] else 0,
                "turnover": float(parts[38]) if parts[38] else 0,  # 换手率%
                "vol_ratio": float(parts[49]) if parts[49] else 0, # 量比
                "pe":       float(parts[52]) if parts[52] else 0,
                "pb":       float(parts[46]) if parts[46] else 0,
            }
        return result
    except Exception:
        return {}


# ── 主力资金净流入 ────────────────────────────────────────────────────────────

def _capital_flow(code: str, market: str, days: int = 5) -> list:
    """
    返回最近 N 天主力净流入数据。
    market: A股SH→1, A股SZ→0, 港股→116, 美股→105
    格式: [{date, super_net, large_net, main_net, main_pct}, ...]
    """
    mkt_map = {"SH": "1", "SZ": "0", "HK": "116", "US": "105"}
    mkt = mkt_map.get(market.upper(), "0")
    clean = re.sub(r"\.(SS|SZ|HK)$", "", code)

    # 方法1: push2his（本地可用，Render 可能被拦）
    try:
        r = _req.get(
            "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            params={"secid": f"{mkt}.{clean}", "lmt": days, "klt": 101,
                    "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58"},
            headers=_HEADERS, timeout=5,
        )
        klines = r.json().get("data", {}).get("klines", []) or []
        if klines:
            result = []
            for kl in klines:
                p = kl.split(",")
                if len(p) < 7:
                    continue
                result.append({
                    "date": p[0], "super_net": float(p[1]) / 1e8,
                    "large_net": float(p[2]) / 1e8, "mid_net": float(p[3]) / 1e8,
                    "small_net": float(p[4]) / 1e8, "main_net": float(p[5]) / 1e8,
                    "main_pct": float(p[6]),
                })
            return result
    except Exception:
        pass

    # 方法2: yfinance 量价估算（备用，云端使用）
    try:
        yf_code = code if "." in code else (
            code + ".SS" if market == "SH" else
            code + ".SZ" if market == "SZ" else code
        )
        df = yf.Ticker(yf_code).history(period=f"{days + 3}d", auto_adjust=True)
        if df is None or df.empty:
            return []
        avg_vol = float(df["Volume"].mean()) or 1
        result = []
        for i in range(max(0, len(df) - days), len(df)):
            row = df.iloc[i]
            chg = (float(row["Close"]) - float(row["Open"])) / float(row["Open"]) if float(row["Open"]) else 0
            vol_ratio = float(row["Volume"]) / avg_vol
            amount_bn = float(row["Volume"]) * float(row["Close"]) / 1e8
            main_net = round(amount_bn * chg * vol_ratio, 3)
            result.append({
                "date": str(df.index[i])[:10],
                "super_net": round(main_net * 0.4, 3),
                "large_net": round(main_net * 0.3, 3),
                "mid_net": round(main_net * 0.2, 3),
                "small_net": round(main_net * 0.1, 3),
                "main_net": main_net,
                "main_pct": round(chg * vol_ratio * 100, 1),
            })
        return result
    except Exception:
        return []


def _detect_market(code: str) -> str:
    """根据代码推断市场标识。"""
    if code.endswith(".SS") or code.startswith("6") or code.startswith("0"):
        return "SH" if (code.startswith("6") or code.endswith(".SS")) else "SZ"
    if code.endswith(".SZ") or code.startswith("0") or code.startswith("3"):
        return "SZ"
    if code.endswith(".HK") or re.match(r"^\d{4,5}\.?HK$", code):
        return "HK"
    # 纯数字
    if code.isdigit():
        if code.startswith("6"): return "SH"
        if code.startswith("0") or code.startswith("3"): return "SZ"
    return "SH"


# ── 板块新闻 ─────────────────────────────────────────────────────────────────

def _sector_news(code: str, info: dict, n: int = 8) -> list:
    """获取个股所属板块的最新新闻（东方财富）。"""
    mkt = _detect_market(code)
    mkt_prefix = "1" if mkt == "SH" else "0"
    clean = re.sub(r"\.(SS|SZ|HK)$", "", code)

    # 个股新闻
    stock_news = []
    try:
        r = _req.get(
            "http://np-listapi.eastmoney.com/comm/web/getListInfo",
            params={"client": "web", "type": "1",
                    "mTypeAndCode": f"{mkt_prefix}.{clean}", "pageSize": n, "pageIndex": "1"},
            headers=_HEADERS, timeout=6,
        )
        for it in r.json().get("data", {}).get("list", []) or []:
            stock_news.append({"title": it.get("Art_Title", ""), "date": it.get("Art_ShowTime", "")[:10]})
    except Exception:
        pass

    # 板块新闻
    sector_news = []
    sector   = (info or {}).get("sector", "") or ""
    industry = (info or {}).get("industry", "") or ""
    combined = (sector + " " + industry).lower()
    industry_map = [
        (["diagnostics","research","cro","cdmo","contract","pharmaceutical"], "CRO"),
        (["biotechnology","biotech"], "CRO"),
        (["drug manufacturer","specialty pharma"], "医药"),
        (["medical device","medical instrument"], "医疗器械"),
        (["semiconductor","chip"], "半导体"),
        (["software","internet","technology"], "科技"),
        (["bank","financial","insurance"], "银行"),
        (["solar","wind","renewable","new energy"], "新能源"),
        (["consumer","retail","food"], "消费"),
        (["real estate","property"], "房地产"),
    ]
    search_kw = next((zh for kws, zh in industry_map if any(kw in combined for kw in kws)), None)
    if search_kw:
        try:
            r2 = _req.get(
                "http://searchapi.eastmoney.com/api/suggest/get",
                params={"input": search_kw, "type": "14",
                        "token": "D43BF722C8E33BDC906FB84D85E326DE", "count": "5"},
                headers=_HEADERS, timeout=5,
            )
            for d in r2.json().get("QuotationCodeTable", {}).get("Data", []):
                if d.get("SecurityTypeName") == "板块" and d.get("Code", "").startswith("BK"):
                    bk = d["Code"]
                    r3 = _req.get(
                        "http://np-listapi.eastmoney.com/comm/web/getListInfo",
                        params={"client": "web", "type": "1",
                                "mTypeAndCode": f"90.{bk}", "pageSize": n, "pageIndex": "1"},
                        headers=_HEADERS, timeout=6,
                    )
                    for it in r3.json().get("data", {}).get("list", []) or []:
                        sector_news.append({"title": it.get("Art_Title", ""), "date": it.get("Art_ShowTime", "")[:10]})
                    break
        except Exception:
            pass

    # 板块新闻优先
    seen = set()
    merged = []
    for n_ in sector_news + stock_news:
        t = n_.get("title", "")
        if t and t not in seen:
            seen.add(t)
            merged.append(n_)
    return merged[:12]


# ── 市场情绪综合评分 ─────────────────────────────────────────────────────────

def _market_sentiment() -> dict:
    """
    计算市场情绪综合指数 (0~100, 50=中性, >70=贪婪, <30=恐慌)
    数据来源：沪深指数涨跌幅、量比、VIX
    """
    score = 50
    details = []

    # 1. 主要指数涨跌幅
    indices = {"sh000001": "上证", "sz399001": "深证成指", "sz399006": "创业板"}
    try:
        qt = _tencent_quote(list(indices.keys()))
        for code, name in indices.items():
            q = qt.get(code, {})
            chg = q.get("chg_pct", 0)
            if chg > 1.5:   score += 8
            elif chg > 0.5: score += 4
            elif chg < -1.5: score -= 8
            elif chg < -0.5: score -= 4
            details.append({"label": name, "value": f"{chg:+.2f}%", "code": code})
    except Exception:
        pass

    # 2. VIX（恐慌指数，低=贪婪，高=恐慌）
    try:
        vix_df = yf.Ticker("^VIX").history(period="2d")
        if not vix_df.empty:
            vix = float(vix_df["Close"].iloc[-1])
            if vix > 35:    score -= 15; vix_label = f"VIX {vix:.1f} 极度恐慌"
            elif vix > 28:  score -= 8;  vix_label = f"VIX {vix:.1f} 恐慌"
            elif vix > 20:  score -= 2;  vix_label = f"VIX {vix:.1f} 警惕"
            elif vix < 14:  score += 10; vix_label = f"VIX {vix:.1f} 过度贪婪"
            elif vix < 18:  score += 5;  vix_label = f"VIX {vix:.1f} 乐观"
            else:           vix_label = f"VIX {vix:.1f} 中性"
            details.append({"label": "VIX恐慌指数", "value": vix_label})
    except Exception:
        pass

    # 3. 北向资金（用沪深港通ETF涨跌代理）
    try:
        hgt = yf.Ticker("513500.SS").history(period="2d")  # 标普500 ETF 代理外资情绪
        if len(hgt) >= 2:
            chg = float(hgt["Close"].iloc[-1]) / float(hgt["Close"].iloc[-2]) - 1
            if chg > 0.01:  score += 5; north_label = f"外资ETF +{chg*100:.1f}% 流入信号"
            elif chg < -0.01: score -= 5; north_label = f"外资ETF {chg*100:.1f}% 流出信号"
            else: north_label = f"外资ETF {chg*100:.1f}% 中性"
            details.append({"label": "外资情绪", "value": north_label})
    except Exception:
        pass

    score = max(0, min(100, score))
    if score >= 70:   label, color = "极度贪婪", "#ff4444"
    elif score >= 55: label, color = "贪婪",     "#ff8800"
    elif score >= 45: label, color = "中性",     "#ffcc00"
    elif score >= 30: label, color = "恐慌",     "#4488ff"
    else:             label, color = "极度恐慌", "#0044ff"

    return {"score": score, "label": label, "color": color, "details": details}


# ── 短线信号评分 ─────────────────────────────────────────────────────────────

def _short_signal_score(code: str) -> dict:
    """
    综合评分：资金流向(35%) + 量价动量(30%) + 消息面(20%) + 市场情绪(15%)
    返回 score(-100~+100), signals, recommendation
    """
    mkt = _detect_market(code)
    mkt_code = {"SH": "sh", "SZ": "sz", "HK": "hk"}.get(mkt, "sz")
    tencent_code = f"{mkt_code}{re.sub(r'.(SS|SZ|HK)$', '', code)}"

    signals = []
    score = 0
    info = {}

    # ── 1. 实时行情（量比/换手率/涨幅）
    qt = _tencent_quote([tencent_code])
    q = qt.get(tencent_code, {})
    name = q.get("name", code)
    price = q.get("price", 0)
    chg_pct = q.get("chg_pct", 0)
    vol_ratio = q.get("vol_ratio", 1.0)
    turnover = q.get("turnover", 0)

    # 量比评分
    if vol_ratio >= 3.0:
        score += 20; signals.append({"name": "量比异动", "rating": "极强", "detail": f"量比={vol_ratio:.1f}，成交量爆发，市场高度关注"})
    elif vol_ratio >= 1.5:
        score += 10; signals.append({"name": "量比放大", "rating": "积极", "detail": f"量比={vol_ratio:.1f}，资金流入加速"})
    elif vol_ratio < 0.5:
        score -= 8;  signals.append({"name": "缩量萎靡", "rating": "消极", "detail": f"量比={vol_ratio:.1f}，市场冷淡，缺乏人气"})
    else:
        signals.append({"name": "量比正常", "rating": "中性", "detail": f"量比={vol_ratio:.1f}，成交量与近期持平"})

    # 当日涨幅评分
    if chg_pct >= 5:
        score += 15; signals.append({"name": "强势上涨", "rating": "积极", "detail": f"今日涨幅{chg_pct:+.2f}%，多头强势"})
    elif chg_pct >= 2:
        score += 8;  signals.append({"name": "温和上涨", "rating": "积极", "detail": f"今日涨幅{chg_pct:+.2f}%"})
    elif chg_pct <= -5:
        score -= 15; signals.append({"name": "大幅下跌", "rating": "消极", "detail": f"今日跌幅{chg_pct:+.2f}%，空头压制"})
    elif chg_pct <= -2:
        score -= 8;  signals.append({"name": "温和下跌", "rating": "消极", "detail": f"今日跌幅{chg_pct:+.2f}%"})
    else:
        signals.append({"name": "涨跌平稳", "rating": "中性", "detail": f"今日涨跌{chg_pct:+.2f}%"})

    # ── 2. 主力资金净流入（近3日）
    cf = _capital_flow(code, mkt, days=5)
    if cf:
        recent3 = cf[-3:] if len(cf) >= 3 else cf
        net3 = sum(x["main_net"] for x in recent3)
        today = cf[-1] if cf else {}
        today_net = today.get("main_net", 0)
        today_pct = today.get("main_pct", 0)

        if today_net > 0.5:
            score += 20; signals.append({"name": "主力净流入", "rating": "积极",
                "detail": f"今日主力净流入{today_net:.2f}亿（占比{today_pct:.1f}%），近3日合计{net3:.2f}亿"})
        elif today_net > 0:
            score += 8;  signals.append({"name": "主力小幅流入", "rating": "积极",
                "detail": f"今日主力净流入{today_net:.2f}亿"})
        elif today_net < -0.5:
            score -= 20; signals.append({"name": "主力净流出", "rating": "消极",
                "detail": f"今日主力净流出{abs(today_net):.2f}亿（占比{today_pct:.1f}%），近3日合计{net3:.2f}亿"})
        else:
            score -= 5;  signals.append({"name": "主力小幅流出", "rating": "消极",
                "detail": f"今日主力净流出{abs(today_net):.2f}亿"})

    # ── 3. 近期价格动量（用yfinance）
    try:
        yf_code = code if "." in code else (code + ".SS" if mkt == "SH" else code + ".SZ")
        df = yf.Ticker(yf_code).history(period="10d", auto_adjust=True)
        if not df.empty and len(df) >= 5:
            r5 = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-6]) - 1) * 100
            r3 = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-4]) - 1) * 100
            if r5 > 8:
                score += 12; signals.append({"name": "5日强势", "rating": "积极", "detail": f"近5日涨幅{r5:+.1f}%，短线动能强劲"})
            elif r5 > 3:
                score += 5;  signals.append({"name": "5日上涨", "rating": "积极", "detail": f"近5日涨幅{r5:+.1f}%"})
            elif r5 < -8:
                score -= 12; signals.append({"name": "5日弱势", "rating": "消极", "detail": f"近5日跌幅{abs(r5):.1f}%，短线动能疲软"})
            elif r5 < -3:
                score -= 5;  signals.append({"name": "5日下跌", "rating": "消极", "detail": f"近5日跌幅{abs(r5):.1f}%"})
            else:
                signals.append({"name": "5日横盘", "rating": "中性", "detail": f"近5日涨跌{r5:+.1f}%"})
            info = yf.Ticker(yf_code).info or {}
    except Exception:
        pass

    # ── 4. 近期新闻情绪扫描
    news = _sector_news(code, info, n=8)
    neg_kw = ["立案", "调查", "违规", "处罚", "下调", "警示", "问询", "亏损", "暴跌", "崩盘"]
    pos_kw = ["涨停", "大涨", "突破", "创新高", "买入", "上调", "超预期", "爆发", "龙头"]
    neg_hits = sum(1 for n in news if any(kw in n.get("title", "") for kw in neg_kw))
    pos_hits = sum(1 for n in news if any(kw in n.get("title", "") for kw in pos_kw))
    if pos_hits > neg_hits and pos_hits >= 2:
        score += 10; signals.append({"name": "消息面偏多", "rating": "积极",
            "detail": f"近期{pos_hits}条利好新闻 vs {neg_hits}条利空"})
    elif neg_hits > pos_hits and neg_hits >= 2:
        score -= 10; signals.append({"name": "消息面偏空", "rating": "消极",
            "detail": f"近期{neg_hits}条利空新闻 vs {pos_hits}条利好"})
    else:
        signals.append({"name": "消息面中性", "rating": "中性",
            "detail": f"近期利好{pos_hits}条，利空{neg_hits}条"})

    score = max(-100, min(100, score))
    if score >= 40:   rec, rc = "短线做多", "#00cc55"
    elif score >= 15: rec, rc = "偏多观望", "#55ee99"
    elif score >= -15: rec, rc = "中性观望", "#ffcc00"
    elif score >= -40: rec, rc = "偏空观望", "#ff9944"
    else:              rec, rc = "短线做空", "#ff4444"

    return {
        "code": code, "name": name, "price": price,
        "chg_pct": chg_pct, "vol_ratio": vol_ratio, "turnover": turnover,
        "score": score, "rec": rec, "rec_color": rc,
        "signals": signals, "news": news[:6],
        "capital_flow": cf,
    }


# ── AI 短线分析 ───────────────────────────────────────────────────────────────

def _ai_short_analyze(code: str, name: str, data: dict, news: list) -> str:
    news_block = "\n".join([f"- [{n.get('date','')}] {n.get('title','')}" for n in news[:8]])
    cf = data.get("capital_flow", [])
    cf_text = " | ".join([f"{x['date'][-5:]}主力{x['main_net']:+.2f}亿" for x in cf[-3:]]) if cf else "无"

    prompt = (
        f"今天{date.today()}，请对{name}（{code}）进行短线交易分析（100字以内，不要开场白）：\n\n"
        f"【实时数据】\n"
        f"- 今日涨跌：{data.get('chg_pct',0):+.2f}%  量比：{data.get('vol_ratio',1):.1f}  换手率：{data.get('turnover',0):.2f}%\n"
        f"- 主力资金（近3日）：{cf_text}\n"
        f"- 系统短线评分：{data.get('score',0):+d}/100  信号：{data.get('rec','')}\n\n"
        f"【近期板块新闻】\n{news_block}\n\n"
        f"请给出：1.当前短线方向判断  2.主要风险或催化剂  3.具体操作建议（进场/观望/回避）"
    )
    try:
        r = _req.post(
            f"{_DEEPSEEK_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {_DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "max_tokens": 300},
            timeout=30, verify=False,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return ""


# ── Flask 路由 ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/status")
def api_status():
    return jsonify({"ok": True, "time": datetime.now().isoformat()})


@app.route("/api/market")
def api_market():
    """市场情绪面板：大盘指数 + 情绪评分"""
    sentiment = _market_sentiment()

    # 主要指数行情
    codes = ["sh000001", "sz399001", "sz399006", "sh000300"]
    names = {"sh000001": "上证指数", "sz399001": "深成指", "sz399006": "创业板", "sh000300": "沪深300"}
    qt = _tencent_quote(codes)
    indices = []
    for code in codes:
        q = qt.get(code, {})
        if q:
            indices.append({
                "name": names.get(code, code),
                "price": q.get("price", 0),
                "chg_pct": q.get("chg_pct", 0),
            })

    return Response(jdump({"sentiment": sentiment, "indices": indices}), mimetype="application/json")


@app.route("/api/stock")
def api_stock():
    """个股短线信号（量价+资金+消息+AI）"""
    code = request.args.get("code", "").strip().upper()
    if not code:
        return jsonify({"error": "缺少 code 参数"}), 400
    try:
        data = _short_signal_score(code)
        ai = _ai_short_analyze(code, data.get("name", code), data, data.get("news", []))
        data["ai_analysis"] = ai
        return Response(jdump(data), mimetype="application/json")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sectors")
def api_sectors():
    """板块轮动：今日涨跌幅排行 + 各板块新闻"""
    # 用东方财富新闻推断热门板块
    hot_sectors = ["半导体", "CRO", "新能源", "AI", "军工", "消费", "银行", "房地产"]
    result = []
    for sector in hot_sectors:
        try:
            # 搜索板块代码
            r = _req.get(
                "http://searchapi.eastmoney.com/api/suggest/get",
                params={"input": sector, "type": "14",
                        "token": "D43BF722C8E33BDC906FB84D85E326DE", "count": "3"},
                headers=_HEADERS, timeout=5,
            )
            bk = None
            for d in r.json().get("QuotationCodeTable", {}).get("Data", []):
                if d.get("SecurityTypeName") == "板块":
                    bk = d["Code"]
                    bk_name = d["Name"]
                    break
            if not bk:
                continue

            # 拉板块新闻
            r2 = _req.get(
                "http://np-listapi.eastmoney.com/comm/web/getListInfo",
                params={"client": "web", "type": "1", "mTypeAndCode": f"90.{bk}",
                        "pageSize": "3", "pageIndex": "1"},
                headers=_HEADERS, timeout=5,
            )
            news = []
            for it in r2.json().get("data", {}).get("list", []) or []:
                title = it.get("Art_Title", "")
                d = it.get("Art_ShowTime", "")[:10]
                if title:
                    news.append({"title": title, "date": d})

            if news:
                result.append({"sector": bk_name, "bk": bk, "news": news})
        except Exception:
            continue

    return Response(jdump({"sectors": result}), mimetype="application/json")


@app.route("/api/news")
def api_news():
    """消息雷达：监管公告 + 市场快讯"""
    news_list = []
    # 综合市场新闻
    monitor_codes = [
        ("1.600031", "三一重工"), ("0.002415", "海康威视"),
        ("1.603259", "药明康德"), ("0.300347", "泰格医药"),
    ]
    for mcode, _ in monitor_codes:
        try:
            r = _req.get(
                "http://np-listapi.eastmoney.com/comm/web/getListInfo",
                params={"client": "web", "type": "1", "mTypeAndCode": mcode,
                        "pageSize": "5", "pageIndex": "1"},
                headers=_HEADERS, timeout=5,
            )
            for it in r.json().get("data", {}).get("list", []) or []:
                title = it.get("Art_Title", "")
                dt = it.get("Art_ShowTime", "")[:10]
                url = it.get("Art_Url", "") or it.get("Art_OriginUrl", "")
                if title and dt >= str(date.today() - timedelta(days=3)):
                    news_list.append({"title": title, "date": dt, "url": url})
        except Exception:
            continue

    # 去重 + 按日期排序
    seen = set()
    unique = []
    for n in news_list:
        if n["title"] not in seen:
            seen.add(n["title"])
            unique.append(n)
    unique.sort(key=lambda x: x["date"], reverse=True)

    return Response(jdump({"news": unique[:20]}), mimetype="application/json")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 6000))
    print(f"ShortStockMaster running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
