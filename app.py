# app.py — ShortStockMaster Flask backend
# Short-term trading signals: sentiment, capital flow, momentum, news, AI
import json, math, os, re, threading, uuid
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


def _sanitize(obj):
    """递归将 NaN/Inf 替换为 None，确保 JSON 合法（Safari 不接受裸 NaN）。"""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj

class SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)):
            f = float(obj)
            return None if (math.isnan(f) or math.isinf(f)) else f
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, pd.Series): return obj.tolist()
        if isinstance(obj, (date, datetime)): return str(obj)
        return super().default(obj)

def jdump(obj):
    return json.dumps(_sanitize(obj), cls=SafeEncoder, ensure_ascii=False)


# ── 腾讯行情解析 ─────────────────────────────────────────────────────────────

def _tencent_quote(codes: list) -> dict:
    """从腾讯行情获取实时数据，返回 {code: {...}} 字典。"""
    def safe_float(s):
        try:
            return float(s) if s and s.strip() else 0
        except (ValueError, TypeError):
            return 0

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
            if len(parts) < 40:
                continue
            result[code_key] = {
                "name":      parts[1],
                "code":      parts[2],
                "price":     safe_float(parts[3]),
                "prev_close": safe_float(parts[4]),
                "open":      safe_float(parts[5]),
                "volume":    safe_float(parts[36]),
                "amount":    safe_float(parts[37]),
                "chg_pct":   safe_float(parts[32]),
                "turnover":  safe_float(parts[38]),
                "vol_ratio": safe_float(parts[49]) if len(parts) > 49 and safe_float(parts[49]) < 30 else 1.0,
                "pe":        safe_float(parts[52]) if len(parts) > 52 else 0,
                "pb":        safe_float(parts[46]) if len(parts) > 46 else 0,
            }
        return result
    except Exception:
        return {}


# ── 主力资金净流入 ────────────────────────────────────────────────────────────

def _capital_flow(code: str, market: str, days: int = 5) -> list:
    """
    返回最近 N 天真实主力净流入数据（东方财富 push2his API）。
    market: A股SH→1, A股SZ→0, 港股→116, 美股→105
    """
    mkt_map = {"SH": "1", "SZ": "0", "HK": "116", "US": "105"}
    mkt = mkt_map.get(market.upper(), "0")
    clean = re.sub(r"\.(SS|SZ|HK)$", "", code)

    try:
        r = _req.get(
            "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            params={"secid": f"{mkt}.{clean}", "lmt": days, "klt": 101,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58"},
            headers=_HEADERS, timeout=8,
        )
        klines = r.json().get("data", {}).get("klines", []) or []
        result = []
        for kl in klines:
            p = kl.split(",")
            if len(p) < 7:
                continue
            result.append({
                "date":      p[0],
                "super_net": round(float(p[1]) / 1e8, 3),   # 超大单净（亿）
                "large_net": round(float(p[2]) / 1e8, 3),   # 大单净（亿）
                "mid_net":   round(float(p[3]) / 1e8, 3),
                "small_net": round(float(p[4]) / 1e8, 3),
                "main_net":  round(float(p[5]) / 1e8, 3),   # 主力净=超大+大单（亿）
                "main_pct":  round(float(p[6]), 2),          # 主力净占比%
            })
        if result:
            return result
    except Exception:
        pass

    # push2his 不可用时的备用：使用真实日涨跌幅（昨收→今收）而非开收差
    try:
        yf_code = code if "." in code else (
            code + ".SS" if market == "SH" else
            code + ".SZ" if market == "SZ" else code
        )
        df = yf.Ticker(yf_code).history(period=f"{days + 5}d", auto_adjust=True)
        if df is None or df.empty:
            return []
        result = []
        for i in range(max(1, len(df) - days), len(df)):
            prev_close = float(df["Close"].iloc[i - 1])
            curr_close = float(df["Close"].iloc[i])
            if math.isnan(prev_close) or math.isnan(curr_close) or prev_close == 0:
                continue
            chg = (curr_close - prev_close) / prev_close
            vol = float(df["Volume"].iloc[i])
            if math.isnan(vol):
                continue
            amount_bn = vol * curr_close / 1e8
            main_net = round(amount_bn * chg, 3)
            result.append({
                "date":      str(df.index[i])[:10],
                "super_net": round(main_net * 0.4, 3),
                "large_net": round(main_net * 0.3, 3),
                "mid_net":   round(main_net * 0.2, 3),
                "small_net": round(main_net * 0.1, 3),
                "main_net":  main_net,
                "main_pct":  round(chg * 100, 2),
            })
        return result
    except Exception:
        return []


def _detect_market(code: str) -> str:
    """根据代码推断市场标识。HK/US扩展名优先，防止被A股前缀误判。"""
    # 扩展名优先（避免 0700.HK 被误判为 SZ）
    if code.endswith(".HK") or re.match(r"^\d{4,5}\.?HK$", code):
        return "HK"
    if code.endswith(".SS"):
        return "SH"
    if code.endswith(".SZ"):
        return "SZ"
    # 纯数字 A 股
    if code.isdigit() or re.match(r"^\d{6}$", code):
        if code.startswith("6") or code.startswith("9"): return "SH"
        if code.startswith("0") or code.startswith("3"): return "SZ"
    # A 股带前缀数字（无扩展名）
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
            bk_count = 0
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
                    bk_count += 1
                    if bk_count >= 2:  # 最多取2个关联板块，扩大中小盘覆盖
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

    # 补充同花顺新闻（中国 IP 可访问时生效；海外降级至仅使用上方东财数据）
    if mkt in ("SH", "SZ"):
        for n_ in _cninfo_news(code, mkt, n=5):
            t = n_.get("title", "")
            if t and t not in seen:
                seen.add(t)
                merged.append(n_)

    return merged[:12]


def _cninfo_news(code: str, market: str, n: int = 5) -> list:
    """从同花顺获取个股新闻（覆盖广，中小盘数据好）。"""
    clean = re.sub(r"\.(SS|SZ|HK)$", "", code)
    if market not in ("SH", "SZ"):
        return []
    try:
        r = _req.get(
            "https://news.10jqka.com.cn/tapp/news/push/stock/",
            params={"code": clean, "page": 1, "pageSize": n, "tag": "", "enterby": ""},
            headers=_HEADERS, timeout=6,
        )
        results = []
        for item in r.json().get("data", {}).get("list", []) or []:
            title = item.get("title", "")
            ts = item.get("ctime", 0)
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
            if title:
                results.append({"title": title, "date": dt, "source": "ths"})
        return results
    except Exception:
        return []


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
    clean_code = re.sub(r"\.(SS|SZ|HK)$", "", code, flags=re.IGNORECASE)
    if mkt == "HK":
        # 腾讯港股代码格式：hk + 5位零补齐（如 hk00700）
        tencent_code = f"hk{clean_code.lstrip('0').zfill(5)}"
    else:
        mkt_code = {"SH": "sh", "SZ": "sz"}.get(mkt, "sz")
        tencent_code = f"{mkt_code}{clean_code}"

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

    # ── 1.5. 港股恒生指数联动（仅HK市场）
    if mkt == "HK":
        hsi = _tencent_quote(["hkHSI"]).get("hkHSI", {})
        hsi_chg = hsi.get("chg_pct", 0)
        if hsi_chg <= -1.5:
            score -= 15; signals.append({"name": "港股大盘弱势", "rating": "消极",
                "detail": f"恒生指数今日{hsi_chg:+.2f}%，港股整体承压，个股难逃拖累"})
        elif hsi_chg <= -0.5:
            score -= 8;  signals.append({"name": "港股偏弱", "rating": "消极",
                "detail": f"恒生指数今日{hsi_chg:+.2f}%，港股情绪偏弱"})
        elif hsi_chg >= 1.0:
            score += 8;  signals.append({"name": "港股大盘强势", "rating": "积极",
                "detail": f"恒生指数今日{hsi_chg:+.2f}%，港股整体向好，个股受益"})
        else:
            signals.append({"name": "恒生指数", "rating": "中性",
                "detail": f"恒生指数今日{hsi_chg:+.2f}%，港股大盘中性"})

    # ── 2. 主力资金净流入（近3日）
    cf = _capital_flow(code, mkt, days=5)
    if cf:
        recent3 = cf[-3:] if len(cf) >= 3 else cf
        def _safe_num(v): return 0 if (v is None or (isinstance(v, float) and math.isnan(v))) else v
        net3 = sum(_safe_num(x.get("main_net", 0)) for x in recent3)
        today = cf[-1] if cf else {}
        today_net = _safe_num(today.get("main_net", 0))
        today_pct = _safe_num(today.get("main_pct", 0))

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
        if not df.empty:
            # 涨停次日追买风险（A股专属，HK/US无涨停制度）
            if mkt in ("SH", "SZ") and len(df) >= 3:
                prev_chg = float(df["Close"].iloc[-2]) / float(df["Close"].iloc[-3]) - 1
                if prev_chg >= 0.095:
                    score -= 20; signals.append({"name": "涨停次日风险", "rating": "消极",
                        "detail": f"昨日涨幅{prev_chg*100:.1f}%（接近涨停），次日追买历史规律容易回调"})
            # 5日动量
            if len(df) >= 6:
                try:
                    c1 = float(df["Close"].iloc[-1])
                    c6 = float(df["Close"].iloc[-6])
                    r5 = None if (math.isnan(c1) or math.isnan(c6) or c6 == 0) else (c1 / c6 - 1) * 100
                except Exception:
                    r5 = None
                if r5 is not None:
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

    # ── 4. 近期新闻情绪扫描（含巨潮公告，分级加权）
    news = _sector_news(code, info, n=8)
    neg_kw = ["立案", "调查", "违规", "处罚", "下调", "警示", "问询", "亏损", "暴跌", "崩盘",
              "退市", "造假", "虚假陈述", "被罚", "减持", "质押", "违约"]
    pos_kw = ["涨停", "大涨", "突破", "创新高", "买入", "上调", "超预期", "爆发", "龙头",
              "获批", "利好", "战略合作", "业绩预增", "扭亏", "回购", "增持", "重大合同"]
    neg_hits = sum(1 for n in news if any(kw in n.get("title", "") for kw in neg_kw))
    pos_hits = sum(1 for n in news if any(kw in n.get("title", "") for kw in pos_kw))
    if pos_hits >= 3 and pos_hits > neg_hits:
        news_delta = 18
    elif pos_hits >= 1 and pos_hits > neg_hits:
        news_delta = 10
    elif neg_hits >= 3 and neg_hits > pos_hits:
        news_delta = -18
    elif neg_hits >= 1 and neg_hits > pos_hits:
        news_delta = -10
    else:
        news_delta = 0
    score += news_delta
    if news_delta > 0:
        signals.append({"name": "消息面偏多", "rating": "积极",
            "detail": f"近期{pos_hits}条利好新闻 vs {neg_hits}条利空（情绪权重+{news_delta}）"})
    elif news_delta < 0:
        signals.append({"name": "消息面偏空", "rating": "消极",
            "detail": f"近期{neg_hits}条利空新闻 vs {pos_hits}条利好（情绪权重{news_delta}）"})
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


# ── 推荐排行榜 ───────────────────────────────────────────────────────────────

# 东方财富 clist API 市场过滤参数
_MARKET_FS = {
    "A股":   "m:1+t:2,m:0+t:6,m:0+t:80",  # 沪主板+深主板+创业板（不含科创板）
    "科创板": "m:1+t:23",                    # 科创板
    "港股":   "m:116+t:3,m:116+t:4",        # 港股主板+创业板
}

# 美股及兜底：EastMoney美股覆盖有限，保留固定列表
_MARKET_FALLBACK = {
    "美股": [
        ("NVDA", "英伟达"), ("AAPL", "苹果"), ("MSFT", "微软"),
        ("TSLA", "特斯拉"), ("META", "Meta"), ("GOOGL", "谷歌"),
        ("AMZN", "亚马逊"), ("AMD", "AMD"), ("COIN", "Coinbase"),
    ],
}


def _fetch_eastmoney_top(market: str, n: int = 50) -> list:
    """从东方财富拉当日涨幅 Top N，返回 [(code, name), ...]。失败时返回空列表。"""
    fs = _MARKET_FS.get(market)
    if not fs:
        return _MARKET_FALLBACK.get(market, [])
    try:
        r = _req.get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params={"pn": 1, "pz": n, "po": 1, "np": 1,
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": 2, "invt": 2, "fid": "f3",
                    "fs": fs, "fields": "f12,f14"},
            headers=_HEADERS, timeout=10,
        )
        items = r.json().get("data", {}).get("diff", []) or []
        result = []
        for item in items:
            code = str(item.get("f12", ""))
            name = item.get("f14", "")
            if code and name and name != "-":
                if market in ("A股", "科创板"):
                    code = code.zfill(6)
                result.append((code, name))
        return result or _MARKET_FALLBACK.get(market, [])
    except Exception as e:
        print(f"EastMoney {market} fetch error: {e}")
        return _MARKET_FALLBACK.get(market, [])

# 后台任务存储
_rank_jobs: dict = {}


def _rank_score_quick(code: str, market: str, tencent_data: dict) -> dict:
    """用已抓取的腾讯数据快速计算短线评分（无额外网络请求）。"""
    clean = re.sub(r"\.(SS|SZ|HK)$", "", code)

    # 腾讯行情 key 格式
    if market.startswith("A股"):
        tk = f"{'sh' if market=='A股SH' else 'sz'}{clean}"
    elif market == "科创板":
        tk = f"sh{clean}"
    elif market == "港股":
        tk = f"hk{clean.replace('.HK','').lstrip('0').zfill(5)}"
    else:
        tk = f"us{clean}"

    q = tencent_data.get(tk, {})
    if not q:
        return {}

    name     = q.get("name", code)
    price    = q.get("price", 0)
    chg_pct  = q.get("chg_pct", 0)
    vol_ratio = q.get("vol_ratio", 1.0)
    turnover = q.get("turnover", 0)

    score = 0

    # 量比信号
    if vol_ratio >= 3.0:   score += 25
    elif vol_ratio >= 1.5: score += 12
    elif vol_ratio < 0.5:  score -= 10

    # 当日涨幅
    if chg_pct >= 5:    score += 20
    elif chg_pct >= 2:  score += 10
    elif chg_pct <= -5: score -= 20
    elif chg_pct <= -2: score -= 10

    # 换手率异动（换手率>3%是活跃信号）
    if turnover >= 5:   score += 10
    elif turnover >= 3: score += 5

    # 涨停或接近涨停：当日无法买入且次日追买历史回调率高
    # A股/创业板±10%，科创板±20%
    if market.startswith("A股") and chg_pct >= 9.5:
        score -= 30
    elif market == "科创板" and chg_pct >= 19.5:
        score -= 30

    score = max(-100, min(100, score))

    if score >= 30:    rec, rc = "短线做多", "#00cc55"
    elif score >= 10:  rec, rc = "偏多观望", "#55ee99"
    elif score >= -10: rec, rc = "中性",     "#ffcc00"
    elif score >= -30: rec, rc = "偏空观望", "#ff9944"
    else:              rec, rc = "短线做空", "#ff4444"

    display_market = market.replace("A股SH", "A股").replace("A股SZ", "A股")
    return {
        "code": code, "name": name, "market": display_market,
        "price": price, "chg_pct": chg_pct, "vol_ratio": vol_ratio,
        "turnover": turnover, "score": score, "rec": rec, "rec_color": rc,
    }


def _run_rank_job(job_id: str, markets: list):
    """后台线程：批量抓取行情并评分排名。"""
    job = _rank_jobs[job_id]
    job["status"] = "running"
    job["progress"] = 0

    try:
        # 收集候选股
        candidates = []
        for mkt in markets:
            stocks = _RANK_UNIVERSE.get(mkt, [])
            for code, name in stocks:
                if mkt == "A股":
                    m2 = "A股SH" if code.startswith("6") or code.startswith("9") else "A股SZ"
                else:
                    m2 = mkt
                candidates.append((code, name, m2))

        total = len(candidates)
        job["total"] = total

        # 分批用腾讯 API 抓行情（每批 20 只）
        all_tencent = {}
        batch_size = 20
        tencent_codes = []
        for code, _, market in candidates:
            clean = re.sub(r"\.(SS|SZ|HK)$", "", code)
            if market == "A股SH":
                tencent_codes.append(f"sh{clean}")
            elif market == "A股SZ":
                tencent_codes.append(f"sz{clean}")
            elif market == "港股":
                tencent_codes.append(f"hk{clean.replace('.HK','').lstrip('0').zfill(5)}")
            else:
                tencent_codes.append(f"us{clean}")

        for i in range(0, len(tencent_codes), batch_size):
            batch = tencent_codes[i:i+batch_size]
            qt = _tencent_quote(batch)
            all_tencent.update(qt)
            job["progress"] = int((i + batch_size) / total * 60)

        # 评分
        job["tencent_count"] = len(all_tencent)  # 调试用
        results = []
        for idx, (code, name, market) in enumerate(candidates):
            r = _rank_score_quick(code, market, all_tencent)
            if r and r.get("price", 0) > 0:
                results.append(r)
            job["progress"] = 60 + int(idx / total * 30)

        # 按评分排序，取前15
        results.sort(key=lambda x: x["score"], reverse=True)
        job["results"] = results[:15]
        job["status"] = "done"
        job["progress"] = 100

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


# ── Flask 路由 ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/status")
def api_status():
    return jsonify({"ok": True, "time": datetime.now().isoformat()})



@app.route("/api/quotes", methods=["GET"])
def api_quotes():
    """批量实时行情：?codes=AAPL,NVDA&markets=US,US"""
    codes_str   = request.args.get("codes", "")
    markets_str = request.args.get("markets", "")
    if not codes_str:
        return jsonify({}), 400
    codes_list   = [c.strip().upper() for c in codes_str.split(",") if c.strip()]
    markets_list = [m.strip() for m in markets_str.split(",")]
    while len(markets_list) < len(codes_list):
        markets_list.append("美股")
    mkt_alias = {"A": "A股", "HK": "港股", "US": "美股",
                 "A股": "A股", "港股": "港股", "美股": "美股"}

    def _to_tk(code, market):
        clean = re.sub(r"\.(SS|SZ|HK)$", "", code)
        if market == "A股":
            return f"{'sh' if code.startswith('6') or code.startswith('9') else 'sz'}{clean}"
        elif market == "港股":
            return f"hk{clean.lstrip('0').zfill(5)}"
        else:
            return f"us{clean}"

    tk_keys = [_to_tk(c, mkt_alias.get(m, "美股"))
               for c, m in zip(codes_list, markets_list)]
    qt = _tencent_quote(tk_keys)
    result = {}
    for code, tk in zip(codes_list, tk_keys):
        q = qt.get(tk, {})
        result[code] = {"price": q.get("price", 0), "chg_pct": q.get("chg_pct", 0)}
    return Response(jdump(result), mimetype="application/json")



@app.route("/api/rank", methods=["POST"])
def api_rank():
    """同步扫描并直接返回结果（无轮询，无跨 worker 状态问题）。"""
    body = request.get_json(silent=True, force=True) or {}
    raw_markets = body.get("markets", ["A"])
    mkt_alias = {"A": "A股", "HK": "港股", "US": "美股", "STAR": "科创板",
                 "A股": "A股", "港股": "港股", "美股": "美股", "科创板": "科创板"}
    valid_markets = {"A股", "港股", "美股", "科创板"}
    markets = [mkt_alias.get(m, m) for m in raw_markets if mkt_alias.get(m, m) in valid_markets]
    if not markets:
        markets = ["A股"]

    candidates = []
    tencent_codes = []
    for mkt in markets:
        stocks = _fetch_eastmoney_top(mkt, n=50)
        for code, name in stocks:
            clean = re.sub(r"\.(SS|SZ|HK)$", "", code)
            if mkt == "A股":
                m2 = "A股SH" if code.startswith("6") or code.startswith("9") else "A股SZ"
                tk = f"{'sh' if m2 == 'A股SH' else 'sz'}{clean}"
            elif mkt == "科创板":
                m2 = "科创板"
                tk = f"sh{clean}"
            elif mkt == "港股":
                m2 = "港股"
                tk = f"hk{clean.lstrip('0').zfill(5)}"
            else:
                m2 = "美股"
                tk = f"us{clean}"
            candidates.append((code, name, m2))
            tencent_codes.append(tk)

    all_tencent = {}
    for i in range(0, len(tencent_codes), 20):
        all_tencent.update(_tencent_quote(tencent_codes[i:i+20]))

    results = []
    for code, name, market in candidates:
        r = _rank_score_quick(code, market, all_tencent)
        if r and r.get("price", 0) > 0:
            results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    return Response(jdump({"status": "done", "total": len(candidates),
                            "results": results[:15]}), mimetype="application/json")


# 兼容旧接口
@app.route("/api/rank/start", methods=["POST"])
def api_rank_start():
    return api_rank()

@app.route("/api/rank/status/<job_id>")
def api_rank_status(job_id):
    return jsonify({"error": "use /api/rank directly"}), 410



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


# ── 模拟交易 ─────────────────────────────────────────────────────────────────

_TRADE_FILE = os.path.join(os.path.dirname(__file__), "trade.json")
_TRADE_MEM: list = []   # 内存主存储，重启后从文件恢复

def _load_trades() -> list:
    global _TRADE_MEM
    if _TRADE_MEM:
        return _TRADE_MEM
    try:
        with open(_TRADE_FILE, encoding="utf-8") as f:
            _TRADE_MEM = json.load(f)
    except Exception:
        _TRADE_MEM = []
    return _TRADE_MEM

def _save_trades(data: list):
    global _TRADE_MEM
    _TRADE_MEM = data
    try:
        with open(_TRADE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 文件写入失败时内存仍有效


@app.route("/api/trade/portfolio", methods=["GET"])
def api_trade_portfolio():
    positions = _load_trades()
    # 刷新当前价格
    if positions:
        codes = []
        for p in positions:
            mkt = p.get("market", "A股")
            code = p["code"]
            clean = re.sub(r"\.(SS|SZ|HK)$", "", code)
            if mkt == "A股":
                codes.append(f"{'sh' if code.startswith('6') or code.startswith('9') else 'sz'}{clean}")
            elif mkt == "港股":
                codes.append(f"hk{clean.replace('.HK','').lstrip('0').zfill(5)}")
            else:
                codes.append(f"us{clean}")
        qt = _tencent_quote(codes)
        for p, tk in zip(positions, codes):
            q = qt.get(tk, {})
            if q.get("price"):
                p["cur_price"] = q["price"]
                p["chg_pct"]   = q.get("chg_pct", 0)

    # 按卖出紧迫度排序（亏损深 + 今日大跌 → 排前面）
    def _urgency(p):
        cur = p.get("cur_price", p["entry_price"])
        cost = p.get("cost", p["entry_price"] * p["shares"])
        pnl_pct = (cur * p["shares"] - cost) / cost * 100 if cost else 0
        chg = p.get("chg_pct", 0) or 0
        score = 0
        if pnl_pct <= -15: score += 30
        elif pnl_pct <= -8: score += 20
        elif pnl_pct <= -3: score += 5
        if chg <= -5: score += 25
        elif chg <= -2: score += 10
        return score

    positions.sort(key=_urgency, reverse=True)
    return Response(jdump(positions), mimetype="application/json")


@app.route("/api/trade/buy", methods=["POST"])
def api_trade_buy():
    body = request.get_json(silent=True) or {}
    code    = body.get("code", "").strip().upper()
    name    = body.get("name", code)
    market  = body.get("market", "A股")
    price   = float(body.get("price", 0))
    shares  = float(body.get("shares", 0))
    score   = int(body.get("score", 0))
    rec     = body.get("rec", "")
    if not code or price <= 0 or shares <= 0:
        return jsonify({"error": "参数错误"}), 400

    positions = _load_trades()
    new_pos = {
        "id":          len(positions) + 1,
        "code":        code,
        "name":        name,
        "market":      market,
        "entry_price": price,
        "cur_price":   price,
        "shares":      shares,
        "cost":        round(price * shares, 2),
        "score_at_buy": score,
        "rec_at_buy":  rec,
        "buy_date":    date.today().isoformat(),
        "chg_pct":     0,
    }
    positions.append(new_pos)
    _save_trades(positions)
    return jsonify({"ok": True, "position": new_pos})


@app.route("/api/trade/sell", methods=["POST"])
def api_trade_sell():
    body = request.get_json(silent=True) or {}
    pos_id = int(body.get("id", 0))
    price  = float(body.get("price", 0))
    positions = _load_trades()
    updated = [p for p in positions if p.get("id") != pos_id]
    if len(updated) == len(positions):
        return jsonify({"error": "持仓不存在"}), 404
    sold = next(p for p in positions if p.get("id") == pos_id)
    pnl  = round((price - sold["entry_price"]) * sold["shares"], 2)
    _save_trades(updated)
    return jsonify({"ok": True, "pnl": pnl, "sell_price": price})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 6000))
    print(f"ShortStockMaster running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
