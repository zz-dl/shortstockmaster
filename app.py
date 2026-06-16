# app.py — ShortStockMaster Flask backend
# Short-term trading signals: sentiment, capital flow, momentum, news, AI
import json, math, os, re, threading, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _clip(value, low, high):
    return max(low, min(high, value))


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

    params = {"secid": f"{mkt}.{clean}", "lmt": days, "klt": 101,
              "fields1": "f1,f2,f3,f7",
              "fields2": "f51,f52,f53,f54,f55,f56,f57,f58"}
    headers = {**_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    # 务必 https：Render 出口不放行明文 http（排行榜用 https 能工作，本函数原先用 http→
    # 失败→退假代理）。多节点 + 重试，最大化拿到真实主力净流入（与中信同口径：超大+大单）。
    for host in ("push2his.eastmoney.com", "1.push2his.eastmoney.com", "7.push2his.eastmoney.com"):
        for _attempt in range(2):
            try:
                r = _req.get(
                    f"https://{host}/api/qt/stock/fflow/daykline/get",
                    params=params, headers=headers, timeout=12,
                )
                klines = r.json().get("data", {}).get("klines", []) or []
                result = []
                for kl in klines:
                    p = kl.split(",")
                    if len(p) < 7:
                        continue
                    result.append({
                        "date":      p[0],
                        "main_net":  round(float(p[1]) / 1e8, 3),   # 主力净=超大+大单（亿）f52
                        "small_net": round(float(p[2]) / 1e8, 3),   # 小单净（亿）f53
                        "mid_net":   round(float(p[3]) / 1e8, 3),   # 中单净（亿）f54
                        "large_net": round(float(p[4]) / 1e8, 3),   # 大单净（亿）f55
                        "super_net": round(float(p[5]) / 1e8, 3),   # 超大单净（亿）f56
                        "main_pct":  round(float(p[6]), 2),          # 主力净占比% f57
                    })
                if result:
                    return result
            except Exception:
                continue
    # 拿不到真实数据时绝不用「涨跌幅×成交额」假代理（常与真实主力净流入反号、误导交易）。
    # 宁可返回空、前端显示「暂无主力资金数据」，也不展示方向错误的资金流。
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


def _beijing_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=8)


def _in_tail_window(now: datetime | None = None) -> bool:
    now = now or _beijing_now()
    minutes = now.hour * 60 + now.minute
    return 14 * 60 + 30 <= minutes <= 14 * 60 + 55


def _news_stats(news: list | None) -> dict:
    neg_kw = ["立案", "调查", "违规", "处罚", "下调", "警示", "问询", "亏损", "暴跌", "崩盘",
              "退市", "造假", "虚假陈述", "被罚", "减持", "质押", "违约"]
    pos_kw = ["涨停", "大涨", "突破", "创新高", "买入", "上调", "超预期", "爆发", "龙头",
              "获批", "利好", "战略合作", "业绩预增", "扭亏", "回购", "增持", "重大合同"]
    items = news or []
    neg_hits = sum(1 for n in items if any(kw in str(n.get("title", "")) for kw in neg_kw))
    pos_hits = sum(1 for n in items if any(kw in str(n.get("title", "")) for kw in pos_kw))
    return {"positive": pos_hits, "negative": neg_hits}


def _build_trade_plan(stock: dict, market_sentiment: dict | None = None,
                      now: datetime | None = None, news_stats: dict | None = None) -> dict:
    """Turn a raw short-term score into an actionable, risk-aware trade plan."""
    price = _to_float(stock.get("price") or stock.get("current_price"), 0)
    score = _to_float(stock.get("score"), 0)
    chg_pct = _to_float(stock.get("chg_pct") or stock.get("chg"), 0)
    vol_ratio = _to_float(stock.get("vol_ratio"), 1)
    turnover = _to_float(stock.get("turnover") or stock.get("turnover_rate"), 0)
    capital_net = _to_float(stock.get("capital_net") or stock.get("main_net"), 0)
    market = str(stock.get("market", ""))
    is_astock = market in ("A股", "科创板", "A股SH", "A股SZ") or re.match(r"^\d{6}$", str(stock.get("code", "")))
    sentiment_score = _to_float((market_sentiment or {}).get("score"), 50)
    sentiment_label = str((market_sentiment or {}).get("label", "中性"))
    news_stats = news_stats or {"positive": 0, "negative": 0}

    drivers = []
    risk_flags = []
    factor_scores = {"technical": 0, "capital": 0, "market": 0, "news": 0, "risk": 0}

    if 2 <= chg_pct <= 6.5 and 1.2 <= vol_ratio <= 3.5:
        factor_scores["technical"] += 25
        drivers.append("量价温和放大")
    elif 0.5 <= chg_pct < 2 and 0.8 <= vol_ratio <= 2.5:
        factor_scores["technical"] += 12
        drivers.append("低位启动观察")
    elif chg_pct >= 7 and is_astock:
        factor_scores["technical"] -= 18
        risk_flags.append("涨幅过热，次日回落风险高")
    elif chg_pct <= -2:
        factor_scores["technical"] -= 14
        risk_flags.append("日内走势转弱")

    if vol_ratio >= 5:
        factor_scores["risk"] -= 14
        risk_flags.append("量比过高，可能是情绪冲顶")
    elif vol_ratio < 0.7:
        factor_scores["risk"] -= 8
        risk_flags.append("量能不足")

    if 3 <= turnover <= 12:
        factor_scores["technical"] += 8
        drivers.append("换手活跃但未失控")
    elif turnover >= 18:
        factor_scores["risk"] -= 16
        risk_flags.append("换手过高，追高风险大")

    if capital_net > 2:
        factor_scores["capital"] += 28
        drivers.append("主力大幅净流入")
    elif capital_net > 0.5:
        factor_scores["capital"] += 20
        drivers.append("主力净流入")
    elif capital_net > 0.1:
        factor_scores["capital"] += 10
        drivers.append("主力小幅净流入")
    elif capital_net < -0.5:
        factor_scores["capital"] -= 24
        risk_flags.append("主力资金净流出")
    elif capital_net < -0.1:
        factor_scores["capital"] -= 10
        risk_flags.append("主力资金偏流出")

    if sentiment_score >= 55:
        factor_scores["market"] += 8
        drivers.append(f"市场情绪{sentiment_label}")
    elif sentiment_score < 35:
        factor_scores["market"] -= 14
        risk_flags.append(f"市场情绪{sentiment_label}")

    if news_stats.get("negative", 0) >= 2 and news_stats.get("negative", 0) > news_stats.get("positive", 0):
        factor_scores["news"] -= 18
        risk_flags.append("近期利空新闻占优")
    elif news_stats.get("positive", 0) > news_stats.get("negative", 0):
        factor_scores["news"] += min(14, news_stats.get("positive", 0) * 6)
        drivers.append("消息面偏多")

    composite = score + sum(factor_scores.values()) * 0.35
    hard_reject = (
        price <= 0 or
        (is_astock and chg_pct >= 8) or
        vol_ratio >= 6 or
        turnover >= 22 or
        news_stats.get("negative", 0) >= 3
    )

    if hard_reject or composite < 18:
        decision = "回避"
        confidence = "低"
        position_pct = 0
    elif capital_net < -0.5 or len(risk_flags) >= 3 or sentiment_score < 30:
        decision = "观察"
        confidence = "低"
        position_pct = 0
    elif composite >= 58 and capital_net > 0.5 and len(risk_flags) <= 1:
        decision = "买入" if _in_tail_window(now) else "尾盘确认"
        confidence = "高"
        position_pct = 20
    elif composite >= 38 and capital_net >= 0 and len(risk_flags) <= 2:
        decision = "买入" if _in_tail_window(now) else "尾盘确认"
        confidence = "中"
        position_pct = 10 if risk_flags else 15
    else:
        decision = "观察"
        confidence = "低"
        position_pct = 0

    stop_loss_pct = 3.2 if confidence == "高" else 2.6
    take_profit_pct = 6.5 if confidence == "高" else 4.8
    stop_loss_price = round(price * (1 - stop_loss_pct / 100), 2) if price else None
    take_profit_price = round(price * (1 + take_profit_pct / 100), 2) if price else None

    if decision == "买入":
        buy_plan = f"14:30-14:55 已进入确认窗口；价格不跌破日内均价/VWAP且主力仍净流入时，小仓买入。"
    elif decision == "尾盘确认":
        buy_plan = f"先观察，14:30-14:55 再确认；若仍在现价±1.0%内、未放量跳水、主力不转流出，再考虑买入。"
    elif decision == "观察":
        buy_plan = "暂不买入；等尾盘重新确认资金和价格是否继续共振。"
    else:
        buy_plan = "不建议买入；当前风险收益比不合适。"

    sell_plan = (
        f"止损参考 {stop_loss_price}（约-{stop_loss_pct:.1f}%）；"
        f"止盈先看 {take_profit_price}（约+{take_profit_pct:.1f}%）。"
        "若次日高开后量能跟不上或跌破买入日收盘/VWAP，优先减仓。最长持有3个交易日。"
    )

    invalidations = []
    invalidations.extend(risk_flags[:3])
    if capital_net <= 0:
        invalidations.append("主力资金转为净流出")
    invalidations.append("跌破买入日收盘价或盘中VWAP")
    invalidations.append("板块热度退潮或出现突发利空")

    return {
        "decision": decision,
        "confidence": confidence,
        "position_pct": position_pct,
        "buy_plan": buy_plan,
        "sell_plan": sell_plan,
        "stop_loss_pct": stop_loss_pct,
        "stop_loss_price": stop_loss_price,
        "take_profit_pct": take_profit_pct,
        "take_profit_price": take_profit_price,
        "max_holding_days": 3,
        "drivers": drivers[:4],
        "risk_flags": risk_flags[:5],
        "invalidations": invalidations[:5],
        "factor_scores": factor_scores,
        "composite_score": round(composite, 1),
    }


def _apply_plan_to_rank_item(stock: dict, market_sentiment: dict | None = None,
                             now: datetime | None = None) -> dict:
    plan = _build_trade_plan(stock, market_sentiment=market_sentiment, now=now)
    enriched = dict(stock)
    enriched["trade_plan"] = plan
    enriched["decision"] = plan["decision"]
    enriched["confidence"] = plan["confidence"]
    enriched["position_pct"] = plan["position_pct"]
    if plan["decision"] in ("买入", "尾盘确认"):
        enriched["rec"] = plan["decision"]
        enriched["rec_color"] = "#00cc55" if plan["confidence"] == "高" else "#55ee99"
    elif plan["decision"] == "观察":
        enriched["rec"] = "观察"
        enriched["rec_color"] = "#ffcc00"
    else:
        enriched["rec"] = "回避"
        enriched["rec_color"] = "#ff4455"
    return enriched


def _merge_rank_item_with_detail(rank_item: dict, detail: dict) -> dict:
    """Use the stock detail analysis as the canonical rank-row display state."""
    merged = dict(rank_item)
    merged["quick_score"] = rank_item.get("score")
    merged["quick_rec"] = rank_item.get("rec")
    merged["quick_decision"] = rank_item.get("decision")

    canonical_fields = (
        "name", "market", "price", "chg_pct", "vol_ratio", "turnover",
        "score", "rec", "rec_color", "trade_plan", "decision", "confidence",
        "position_pct", "market_sentiment",
    )
    for field in canonical_fields:
        if field in detail and detail[field] is not None:
            merged[field] = detail[field]

    detail_capital = detail.get("capital_net")
    if detail_capital is None:
        flows = detail.get("capital_flow") or []
        if flows:
            detail_capital = flows[-1].get("main_net")
    if detail_capital is not None:
        merged["capital_net"] = round(_to_float(detail_capital), 2)

    merged["detail_synced"] = True
    return merged


def _sync_rank_items_with_detail(items: list[dict], limit: int = 15) -> list[dict]:
    """Hydrate rank rows with the same detailed score used by /api/stock."""
    selected = list(items[:limit])
    if not selected:
        return []

    def _load(stock: dict) -> dict:
        try:
            detail = _short_signal_score(str(stock.get("code", "")))
            return _merge_rank_item_with_detail(stock, detail)
        except Exception as exc:
            fallback = dict(stock)
            fallback["detail_synced"] = False
            fallback["detail_sync_error"] = str(exc)
            return fallback

    max_workers = min(6, len(selected))
    output: list[dict | None] = [None] * len(selected)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_load, stock): idx for idx, stock in enumerate(selected)}
        for future in as_completed(futures):
            output[futures[future]] = future.result()
    return [item for item in output if item]


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

    market_label = "A股" if mkt in ("SH", "SZ") else ("港股" if mkt == "HK" else "美股")
    market_sentiment = _market_sentiment() if market_label == "A股" else None
    plan_stock = {
        "code": code,
        "name": name,
        "market": market_label,
        "price": price,
        "score": score,
        "chg_pct": chg_pct,
        "vol_ratio": vol_ratio,
        "turnover": turnover,
        "capital_net": (cf[-1].get("main_net") if cf else 0),
    }
    trade_plan = _build_trade_plan(
        plan_stock,
        market_sentiment=market_sentiment,
        news_stats={"positive": pos_hits, "negative": neg_hits},
    )

    return {
        "code": code, "name": name, "market": market_label, "price": price,
        "chg_pct": chg_pct, "vol_ratio": vol_ratio, "turnover": turnover,
        "score": score, "rec": rec, "rec_color": rc,
        "signals": signals, "news": news[:6],
        "capital_flow": cf,
        "capital_net": plan_stock["capital_net"],
        "market_sentiment": market_sentiment,
        "trade_plan": trade_plan,
        "decision": trade_plan["decision"],
        "confidence": trade_plan["confidence"],
        "position_pct": trade_plan["position_pct"],
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

# 动态股票池缓存：EastMoney成功后缓存10分钟，避免间歇性封禁导致结果交替
_em_cache: dict = {}          # {"A股": {"stocks": [...], "ts": datetime}}
_EM_CACHE_TTL = 60            # 秒（防抖：60秒内重复扫描用缓存，超过则重新拉取）

# Tencent 全量扫描缓存（EastMoney 被封时使用）
_tencent_scan_cache: dict = {}   # {"A股": {"stocks": [...], "ts": datetime}}
_TENCENT_SCAN_TTL = 120          # 2分钟


def _gen_astock_qtcodes(market: str) -> list:
    """生成指定市场的腾讯行情代码列表"""
    codes = []
    if market == "A股":
        for n in range(600000, 608000):
            codes.append(f"sh{n}")
        for n in range(1, 4000):
            codes.append(f"sz{str(n).zfill(6)}")
        for n in range(300000, 302000):
            codes.append(f"sz{n}")
    elif market == "科创板":
        for n in range(688000, 689000):
            codes.append(f"sh{n}")
    return codes


def _tencent_scan_fallback(market: str, top_n: int = 60) -> list:
    """
    腾讯全量扫描兜底：EastMoney 被封时使用。
    返回 [(code, name), ...] 格式，取活跃度最高的 top_n 只。
    20 个并发线程，约 5-10 秒完成全市场扫描。
    """
    cached = _tencent_scan_cache.get(market)
    if cached and (datetime.now() - cached["ts"]).total_seconds() < _TENCENT_SCAN_TTL:
        return cached["stocks"]

    qtcodes = _gen_astock_qtcodes(market)
    if not qtcodes:
        return []

    BATCH = 60
    WORKERS = 20
    all_data: list = []
    lock = threading.Lock()

    def _sf(s):
        try:
            return float(s) if s and str(s).strip() else 0.0
        except Exception:
            return 0.0

    def fetch(batch: list) -> None:
        try:
            r = _req.get(
                f"http://qt.gtimg.cn/q={','.join(batch)}",
                headers=_HEADERS, timeout=10,
            )
            for seg in r.text.strip().split(";"):
                if "~" not in seg or "=" not in seg:
                    continue
                m = re.search(r'v_(\w+)="([^"]+)"', seg)
                if not m:
                    continue
                qtcode = m.group(1)
                parts = m.group(2).split("~")
                if len(parts) < 40:
                    continue
                price = _sf(parts[3])
                if price <= 0:
                    continue
                name = parts[1]
                if not name or "ST" in name.upper():
                    continue
                chg_pct = _sf(parts[32])
                vol_ratio = _sf(parts[49]) if len(parts) > 49 else 0
                turnover = _sf(parts[38])
                # 活跃度评分：量比 × 换手率，用于筛选最有意义的 top_n 只
                activity = vol_ratio * turnover
                with lock:
                    all_data.append({
                        "qtcode": qtcode,
                        "code": qtcode[2:],
                        "name": name,
                        "chg_pct": chg_pct,
                        "activity": activity,
                    })
        except Exception:
            pass

    batches = [qtcodes[i:i + BATCH] for i in range(0, len(qtcodes), BATCH)]
    active = []
    for b in batches:
        t = threading.Thread(target=fetch, args=(b,), daemon=True)
        active.append(t)
        t.start()
        if len(active) >= WORKERS:
            for t2 in active:
                t2.join(timeout=15)
            active = []
    for t2 in active:
        t2.join(timeout=15)

    # 按活跃度降序，取 top_n
    all_data.sort(key=lambda x: -x["activity"])
    result = [(d["code"], d["name"]) for d in all_data[:top_n]]
    print(f"Tencent scan fallback {market}: {len(all_data)} valid → top {len(result)}")
    _tencent_scan_cache[market] = {"stocks": result, "ts": datetime.now()}
    return result

# 东方财富 clist API 市场过滤参数
_MARKET_FS = {
    "A股":   "m:1+t:2,m:0+t:6,m:0+t:80",  # 沪主板+深主板+创业板（不含科创板）
    "科创板": "m:1+t:23",                    # 科创板
    "港股":   "m:116+t:3,m:116+t:4",        # 港股主板+创业板
}

# 兜底列表：EastMoney被Render海外IP封禁时使用
_MARKET_FALLBACK = {
    "A股": [
        ("600519", "贵州茅台"), ("300750", "宁德时代"), ("000858", "五粮液"),
        ("601318", "中国平安"), ("600036", "招商银行"), ("000333", "美的集团"),
        ("002594", "比亚迪"), ("601166", "兴业银行"), ("600276", "恒瑞医药"),
        ("000725", "京东方A"), ("002415", "海康威视"), ("600031", "三一重工"),
        ("603259", "药明康德"), ("601899", "紫金矿业"), ("600900", "长江电力"),
        ("000001", "平安银行"), ("601668", "中国建筑"), ("600887", "伊利股份"),
        ("002714", "牧原股份"), ("300760", "迈瑞医疗"), ("600030", "中信证券"),
        ("601728", "中国电信"), ("601857", "中国石油"), ("600028", "中国石化"),
        ("601988", "中国银行"), ("601398", "工商银行"), ("601288", "农业银行"),
        ("600016", "民生银行"), ("002001", "新和成"),   ("300059", "东方财富"),
        ("002475", "立讯精密"), ("300015", "爱尔眼科"), ("601111", "中国国航"),
        ("600585", "海螺水泥"), ("000651", "格力电器"), ("000002", "万科A"),
        ("600050", "中国联通"), ("601800", "中国交建"), ("601601", "中国太保"),
        ("600104", "上汽集团"),
    ],
    "科创板": [
        ("688981", "中芯国际"), ("688111", "金山办公"), ("688036", "传音控股"),
        ("688012", "中微公司"), ("688008", "澜起科技"), ("688256", "寒武纪"),
        ("688041", "海光信息"), ("688561", "奇安信"),   ("688049", "深信服"),
        ("688271", "联影医疗"), ("688223", "晶科能源"), ("688126", "沪硅产业"),
        ("688009", "中国通号"), ("688005", "容百科技"), ("688617", "圣湘生物"),
        ("688047", "龙芯中科"), ("688599", "天合光能"), ("688169", "石头科技"),
        ("688232", "新点软件"), ("688187", "时代电气"), ("688598", "金博股份"),
        ("688180", "君实生物"), ("688138", "清科环境"), ("688116", "天奈科技"),
        ("688395", "正弦电气"), ("688456", "有研粉材"), ("688578", "艾力斯"),
        ("688001", "华兴源创"), ("688007", "光峰科技"), ("688234", "天岳先进"),
    ],
    "港股": [
        ("0700.HK", "腾讯控股"), ("9988.HK", "阿里巴巴"), ("3690.HK", "美团"),
        ("9618.HK", "京东集团"), ("1211.HK", "比亚迪H"), ("0941.HK", "中国移动"),
        ("1810.HK", "小米集团"), ("9999.HK", "网易"),    ("0388.HK", "港交所"),
    ],
    "美股": [
        ("NVDA", "英伟达"), ("AAPL", "苹果"), ("MSFT", "微软"),
        ("TSLA", "特斯拉"), ("META", "Meta"), ("GOOGL", "谷歌"),
        ("AMZN", "亚马逊"), ("AMD", "AMD"), ("COIN", "Coinbase"),
    ],
}


def _fetch_eastmoney_top(market: str, n: int = 50) -> list:
    """从东方财富拉当日涨幅 Top N，返回 [(code, name), ...]。
    成功结果缓存10分钟，避免Render间歇性封禁导致结果忽动忽静。"""
    # 检查缓存
    cached = _em_cache.get(market)
    if cached and (datetime.now() - cached["ts"]).total_seconds() < _EM_CACHE_TTL:
        return cached["stocks"]

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
        if result:
            _em_cache[market] = {"stocks": result, "ts": datetime.now()}
            return result
        if market in ("A股", "科创板"):
            return _tencent_scan_fallback(market)
        return _MARKET_FALLBACK.get(market, [])
    except Exception as e:
        print(f"EastMoney {market} fetch error: {e}")
        if market in ("A股", "科创板"):
            return _tencent_scan_fallback(market)
        return _MARKET_FALLBACK.get(market, [])

# 后台任务存储
_rank_jobs: dict = {}


def _rank_score_quick(code: str, market: str, tencent_data: dict, capital_net: float = 0) -> dict:
    """用腾讯数据 + 主力资金净流入快速计算短线评分，针对隔夜持仓优化。"""
    clean = re.sub(r"\.(SS|SZ|HK)$", "", code)

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

    name      = q.get("name", code)
    price     = q.get("price", 0)
    chg_pct   = q.get("chg_pct", 0)
    vol_ratio = q.get("vol_ratio", 1.0)
    turnover  = q.get("turnover", 0)

    score = 0
    is_astock = market.startswith("A股") or market == "科创板"
    limit_thr = 19.5 if market == "科创板" else 9.5

    # ── 量比：全量回测显示 1.5-4 更稳，极端放量不再追高。
    if vol_ratio >= 6.0:   score -= 8
    elif vol_ratio >= 4.0: score -= 2
    elif vol_ratio >= 2.5: score += 14
    elif vol_ratio >= 1.5: score += 12
    elif vol_ratio >= 0.8: score += 2
    elif vol_ratio < 0.5:  score -= 10

    # ── 当日涨幅：3-7% 是回测支持的候选区，7%以上降权。
    if chg_pct >= limit_thr:
        score -= 30                       # 涨停，当日无法买入
    elif chg_pct >= 7 and is_astock:
        score -= 5                        # 已过热，次日高开低走概率大
    elif chg_pct >= 5:   score += 20
    elif chg_pct >= 3:   score += 16     # 甜蜜点：有动能且未过热
    elif chg_pct >= 1:   score += 6
    elif chg_pct >= 0:   score += 1
    elif chg_pct <= -5:  score -= 22
    elif chg_pct <= -3:  score -= 14
    elif chg_pct <= -1:  score -= 6

    # ── 换手率：>15% 疑似主力出货，红色预警
    if turnover >= 20:   score -= 10     # 严重过度换手，主力出货红警
    elif turnover >= 15: score += 3      # 偏高，观望
    elif turnover >= 8:  score += 13
    elif turnover >= 5:  score += 9
    elif turnover >= 3:  score += 5
    elif turnover >= 1:  score += 2

    # ── 主力资金净流入（亿元，第二阶段传入）
    if capital_net > 2.0:    score += 18
    elif capital_net > 0.5:  score += 12
    elif capital_net > 0.1:  score += 6
    elif capital_net < -2.0: score -= 18
    elif capital_net < -0.5: score -= 12
    elif capital_net < -0.1: score -= 6

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
        "turnover": turnover, "capital_net": round(capital_net, 2),
        "score": score, "rec": rec, "rec_color": rc,
    }


def _is_rank_candidate(stock: dict) -> bool:
    """Apply the full-history backtest-supported A-share candidate filter."""
    market = stock.get("market", "")
    if stock.get("decision") == "回避":
        return False
    if market not in ("A股", "科创板", "A股SH", "A股SZ"):
        return True
    chg_pct = float(stock.get("chg_pct") or 0)
    vol_ratio = float(stock.get("vol_ratio") or 0)
    turnover = float(stock.get("turnover") or 0)
    capital_net = float(stock.get("capital_net") or 0)
    score = float(stock.get("score") or 0)
    if not (0.5 <= chg_pct < 7.0):
        return False
    if not (0.7 <= vol_ratio < 5.0):
        return False
    if turnover >= 20:
        return False
    if capital_net < -0.5:
        return False
    if stock.get("decision") == "观察" and score < 35:
        return False
    return True


def _run_rank_job(job_id: str, markets: list):
    """后台线程：批量抓取行情并评分排名。"""
    job = _rank_jobs[job_id]
    job["status"] = "running"
    job["progress"] = 0

    try:
        # 收集候选股
        candidates = []
        for mkt in markets:
            stocks = _fetch_eastmoney_top(mkt, n=50)
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
    seen_codes: set = set()

    def _add_candidate(code, name, mkt, seed_capital_net=0):
        if not code or code in seen_codes:
            return
        clean = re.sub(r"\.(SS|SZ|HK)$", "", code)
        if mkt == "A股":
            m2 = "A股SH" if code.startswith("6") or code.startswith("9") else "A股SZ"
            tk = f"{'sh' if m2 == 'A股SH' else 'sz'}{clean}"
        elif mkt == "科创板":
            m2, tk = "科创板", f"sh{clean}"
        elif mkt == "港股":
            m2, tk = "港股", f"hk{clean.lstrip('0').zfill(5)}"
        else:
            m2, tk = "美股", f"us{clean}"
        candidates.append((code, name, m2, seed_capital_net))
        tencent_codes.append(tk)
        seen_codes.add(code)

    # 第一来源：涨幅 Top50（原有）
    for mkt in markets:
        for code, name in _fetch_eastmoney_top(mkt, n=50):
            _add_candidate(code, name, mkt)

    # 第二来源：A股主力净流入 Top30（补充稳健积累型，不依赖涨幅排名）
    if "A股" in markets:
        try:
            r_cf = _req.get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "fid": "f62", "po": 1, "pz": 50, "pn": 1,
                    "np": 1, "fltt": 2, "invt": 2,
                    "fs": "m:1+t:2,m:0+t:6,m:0+t:80,m:1+t:23",
                    "fields": "f2,f3,f5,f6,f8,f10,f12,f13,f14,f20,f21,f62",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                },
                timeout=8,
            )
            for s in r_cf.json().get("data", {}).get("diff", []) or []:
                code = str(s.get("f12") or "")
                name = str(s.get("f14") or "")
                if code and "ST" not in name.upper():
                    _add_candidate(code, name, "A股", _to_float(s.get("f62"), 0) / 1e8)
        except Exception as e:
            print(f"Capital flow supplement failed: {e}")

    all_tencent = {}
    for i in range(0, len(tencent_codes), 20):
        all_tencent.update(_tencent_quote(tencent_codes[i:i+20]))

    # 第一阶段：用腾讯数据打分，取 Top30 候选
    phase1 = []
    for code, name, market, seed_capital_net in candidates:
        r = _rank_score_quick(code, market, all_tencent, capital_net=seed_capital_net)
        if r and r.get("price", 0) > 0:
            phase1.append(r)
    phase1 = [r for r in phase1 if _is_rank_candidate(r)]
    phase1.sort(key=lambda x: x["score"], reverse=True)
    top30 = phase1[:50]

    # 第二阶段：并行拉主力资金净流入，仅 A股/科创板
    capital_nets: dict = {}
    cf_lock = threading.Lock()

    def _fetch_cf(stock: dict) -> None:
        code = stock["code"]
        if stock["market"] not in ("A股", "科创板"):
            return
        mkt_str = _detect_market(code)
        cf = _capital_flow(code, mkt_str, days=1)
        if cf:
            with cf_lock:
                capital_nets[code] = cf[-1].get("main_net", 0)

    cf_threads = [threading.Thread(target=_fetch_cf, args=(s,), daemon=True) for s in top30]
    for t in cf_threads: t.start()
    for t in cf_threads: t.join(timeout=10)

    market_sentiment = _market_sentiment()

    # 第三阶段：把资金流加入评分，重新排序，并生成买卖计划
    for r in top30:
        net = capital_nets.get(r["code"], r.get("capital_net", 0))
        r["capital_net"] = round(net, 2)
        if net > 2.0:    delta = 18
        elif net > 0.5:  delta = 12
        elif net > 0.1:  delta = 6
        elif net < -2.0: delta = -18
        elif net < -0.5: delta = -12
        elif net < -0.1: delta = -6
        else:            delta = 0
        r["score"] = max(-100, min(100, r["score"] + delta))
        s = r["score"]
        if s >= 30:    r["rec"], r["rec_color"] = "短线做多", "#00cc55"
        elif s >= 10:  r["rec"], r["rec_color"] = "偏多观望", "#55ee99"
        elif s >= -10: r["rec"], r["rec_color"] = "中性",     "#ffcc00"
        elif s >= -30: r["rec"], r["rec_color"] = "偏空观望", "#ff9944"
        else:          r["rec"], r["rec_color"] = "短线做空", "#ff4444"
        r.update(_apply_plan_to_rank_item(r, market_sentiment))

    top30 = [r for r in top30 if _is_rank_candidate(r)]
    top30.sort(key=lambda x: x["score"], reverse=True)
    detailed_top = _sync_rank_items_with_detail(top30, limit=15)
    if detailed_top:
        top30 = detailed_top
        top30 = [r for r in top30 if _is_rank_candidate(r)]
        top30.sort(key=lambda x: x["score"], reverse=True)
    return Response(jdump({"status": "done", "total": len(candidates),
                            "results": top30[:15]}), mimetype="application/json")


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
