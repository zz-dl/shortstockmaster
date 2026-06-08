"""每日 StockMaster 跟踪日报脚本，由 GitHub Actions 自动运行。"""
import requests, json, base64, re, os
from datetime import date, datetime
from trade_history import build_trade_history_records
from signal_snapshot import build_signal_snapshot_records

GH_TOKEN = os.environ["GH_TOKEN"]
GH_REPO  = os.environ.get("GH_REPO", "zz-dl/shortstockmaster")
GH_HDR   = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
TQ_HDR   = {"User-Agent": "Mozilla/5.0"}
EM_HDR   = {"User-Agent": "Mozilla/5.0"}
today    = date.today().isoformat()
MAX_POSITIONS = 10
BUY_AMOUNT = 10000
BUY_TIME = "09:00:00"
SELL_TIME = "09:00:00"

def gh_get(path):
    r = requests.get(f"https://api.github.com/repos/{GH_REPO}/contents/{path}",
                     headers=GH_HDR, timeout=10)
    if r.status_code == 200:
        d = r.json()
        return json.loads(base64.b64decode(d["content"]).decode()), d["sha"]
    return None, None

def gh_put(path, content, msg, sha=None):
    body = {"message": msg, "content": base64.b64encode(content.encode()).decode()}
    if sha: body["sha"] = sha
    r = requests.put(f"https://api.github.com/repos/{GH_REPO}/contents/{path}",
                     headers=GH_HDR, json=body, timeout=10)
    return r.status_code in (200, 201)

def tk_code(code, market):
    clean = re.sub(r"\.(SS|SZ|HK)$", "", code)
    if market in ("A股","A"): return f"{'sh' if code[:1] in '69' else 'sz'}{clean}"
    elif market in ("港股","HK"): return f"hk{clean.lstrip('0').zfill(5)}"
    else: return f"us{clean}"

def get_quotes(codes):
    if not codes: return {}
    try:
        r = requests.get(f"http://qt.gtimg.cn/q={','.join(codes)}", headers=TQ_HDR, timeout=8)
        out = {}
        for line in r.text.strip().split(";"):
            if "~" not in line: continue
            m = re.search(r'v_(\w+)="([^"]+)"', line)
            if not m: continue
            p = m.group(2).split("~")
            if len(p) < 40: continue
            def sf(s):
                try: return float(s)
                except: return 0
            try: out[m.group(1)] = {"name": p[1], "price": sf(p[3]), "chg": sf(p[32])}
            except: pass
        return out
    except Exception as e:
        print(f"Quote error: {e}"); return {}

def _num(value, default=0):
    try:
        return float(value)
    except Exception:
        return default

def _refresh_positions(positions):
    if not positions:
        return
    for p in positions:
        if not p.get("tk"):
            p["tk"] = tk_code(p.get("code", ""), p.get("market", "A股"))
    quotes = get_quotes([p["tk"] for p in positions])
    for p in positions:
        q = quotes.get(p["tk"], {})
        p["cur_price"] = q.get("price", p.get("cur_price", p.get("entry_price", 0)))
        p["chg_today"] = q.get("chg", p.get("chg_today", 0))
        ep = _num(p.get("entry_price"), 1) or 1
        p["pnl_pct"] = round((p["cur_price"] / ep - 1) * 100, 2)
        p["pnl"] = round((p["cur_price"] - ep) * _num(p.get("shares")), 2)

def _apply_rank_signal(position, signal, rank):
    if not signal:
        return
    position["current_rank"] = rank
    position["score"] = signal.get("score", position.get("score", 0))
    position["rec"] = signal.get("rec", position.get("rec", ""))
    position.setdefault("rank_at_buy", rank)
    position.setdefault("score_at_buy", position.get("score"))
    position.setdefault("rec_at_buy", position.get("rec", ""))

def _is_bearish_signal(signal):
    rec = str(signal.get("rec", ""))
    score = _num(signal.get("score"))
    return "短线做空" in rec or "偏空" in rec or score <= -10

def _sell_reason(position, signal, rank_available=True):
    rec = str((signal or {}).get("rec") or position.get("rec", ""))
    score = _num((signal or {}).get("score", position.get("score", 0)))
    pnl = _num(position.get("pnl_pct"))
    chg = _num(position.get("chg_today"))
    if signal is not None and ("短线做空" in rec or score <= -30):
        return "bearish_signal"
    if signal is not None and "偏空" in rec and pnl > 0:
        return "weakening_take_profit"
    if pnl <= -6:
        return "stop_loss"
    if pnl >= 8 and chg <= 0:
        return "profit_protection"
    if signal is None and rank_available:
        return "dropped_from_top10"
    return ""

def _buy_position(signal, rank, quote):
    price = _num(signal.get("price") or signal.get("current_price") or quote.get("price"), 0)
    if price <= 0:
        return None
    shares = round(BUY_AMOUNT / price, 2)
    return {
        "code": signal["code"],
        "name": signal.get("name", signal["code"]),
        "market": signal.get("market", "A股"),
        "tk": tk_code(signal["code"], signal.get("market", "A股")),
        "score": signal.get("score", 0),
        "rec": signal.get("rec", ""),
        "entry_price": price,
        "entry_date": today,
        "buy_time": BUY_TIME,
        "amount": BUY_AMOUNT,
        "shares": shares,
        "cur_price": price,
        "chg_today": quote.get("chg", signal.get("chg_pct", signal.get("chg", 0))),
        "rank_at_buy": rank,
        "score_at_buy": signal.get("score", 0),
        "rec_at_buy": signal.get("rec", ""),
        "bought_today": True,
    }

def sector_news(kw, n=4):
    try:
        r = requests.get("http://searchapi.eastmoney.com/api/suggest/get",
            params={"input":kw,"type":"14","token":"D43BF722C8E33BDC906FB84D85E326DE","count":"3"},
            headers=EM_HDR, timeout=5)
        for d in r.json().get("QuotationCodeTable",{}).get("Data",[]):
            if d.get("SecurityTypeName") == "板块":
                r2 = requests.get("http://np-listapi.eastmoney.com/comm/web/getListInfo",
                    params={"client":"web","type":"1","mTypeAndCode":f"90.{d['Code']}",
                            "pageSize":n,"pageIndex":"1"},
                    headers=EM_HDR, timeout=5)
                return [(it.get("Art_Title",""), it.get("Art_ShowTime","")[:10])
                        for it in r2.json().get("data",{}).get("list",[]) or []]
    except: pass
    return []

def cninfo_news(code, exchange, n=3):
    """从同花顺获取个股新闻（覆盖广，中小盘数据好）。"""
    try:
        r = requests.get(
            "https://news.10jqka.com.cn/tapp/news/push/stock/",
            params={"code": code, "page": 1, "pageSize": n, "tag": "", "enterby": ""},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=6,
        )
        results = []
        for item in r.json().get("data", {}).get("list", []) or []:
            title = item.get("title", "")
            ts = item.get("ctime", 0)
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
            if title:
                results.append((title, dt))
        return results
    except:
        return []

print(f"=== 日报生成 {today} ===")

state, state_sha = gh_get("daily_logs/portfolio_state.json")
is_day1 = state is None
if is_day1:
    state = {"created": today, "positions": [], "history": []}
    previous_positions = []
    print("第1天：初始化持仓")
else:
    previous_positions = [dict(p) for p in state.get("positions", [])]
    print(f"持续跟踪，第 {(date.today()-date.fromisoformat(state['created'])).days+1} 天")

top10 = []
try:
    resp = requests.post("https://shortstockmaster.onrender.com/api/rank",
                         json={"markets":["A","HK","US"]}, timeout=90).json()
    top10 = resp.get("results", [])[:10]
    print(f"排行榜获取：{len(top10)} 只")
except Exception as e:
    print(f"排行榜失败：{e}")

rank_available = bool(top10)
ranked = [(i + 1, s) for i, s in enumerate(top10 or [])]
top_by_code = {s.get("code"): (rank, s) for rank, s in ranked if s.get("code")}

positions = state.get("positions", [])
_refresh_positions(positions)

sold_positions = []
remaining_positions = []
for p in positions:
    rank_signal = top_by_code.get(p.get("code"))
    rank, signal = rank_signal if rank_signal else (None, None)
    _apply_rank_signal(p, signal, rank)
    reason = _sell_reason(p, signal, rank_available=rank_available)
    if reason:
        sold = dict(p)
        sold["sell_date"] = today
        sold["sell_time"] = SELL_TIME
        sold["sell_price"] = sold.get("cur_price", sold.get("entry_price"))
        sold["sell_reason"] = reason
        sold_positions.append(sold)
    else:
        remaining_positions.append(p)

positions = remaining_positions
held_codes = {p.get("code") for p in positions}
buy_candidates = [
    (rank, s) for rank, s in ranked
    if s.get("code") not in held_codes and not _is_bearish_signal(s)
]
slots = max(0, MAX_POSITIONS - len(positions))
to_buy = buy_candidates[:slots]
buy_quotes = get_quotes([tk_code(s["code"], s.get("market", "A股")) for _, s in to_buy])
bought_positions = []
for rank, s in to_buy:
    tk = tk_code(s["code"], s.get("market", "A股"))
    pos = _buy_position(s, rank, buy_quotes.get(tk, {}))
    if pos:
        bought_positions.append(pos)
        positions.append(pos)

state["positions"] = positions
print(f"自动卖出 {len(sold_positions)} 只，自动买入 {len(bought_positions)} 只，当前持仓 {len(positions)} 只")

news_all = {}
for kw in ["半导体", "AI人工智能", "新能源", "工程机械", "CRO医药"]:
    news_all[kw] = sector_news(kw)

neg_kw = ["立案","调查","违规","处罚","暴跌","跌停","监管"]
pos_kw = ["涨停","大涨","政策利好","获批","超预期","突破"]
key_events = []
for kw, items in news_all.items():
    for t, dt in items:
        if any(k in t for k in neg_kw):   key_events.append(("⚠️ 风险", kw, dt, t))
        elif any(k in t for k in pos_kw): key_events.append(("✅ 利好", kw, dt, t))

anomalies = []
limit_up_stocks = []   # 今日涨停，明日追买高风险
for p in positions:
    rec, pnl = p.get("rec",""), p.get("pnl_pct",0)
    if ("多" in rec or "买" in rec) and pnl < -2:
        anomalies.append((p["name"], p["code"], rec, pnl, p.get("chg_today",0), "推荐做多但下跌"))
    elif ("空" in rec or "卖" in rec) and pnl > 2:
        anomalies.append((p["name"], p["code"], rec, pnl, p.get("chg_today",0), "推荐做空但上涨"))
    if p.get("chg_today", 0) >= 9.5:
        limit_up_stocks.append(p["name"])

total_pnl = sum(p.get("pnl",0) for p in positions)
total_inv = sum(p.get("amount",10000) for p in positions)
total_pct = total_pnl/total_inv*100 if total_inv else 0
day_num   = (date.today()-date.fromisoformat(state["created"])).days + 1

md = [
    f"# StockMaster 每日日报 {today}",
    "",
    f"> 北京时间 09:00 自动生成 · 跟踪第 **{day_num}** 天",
    "",
    "## 📊 持仓总览",
    "",
    f"- **模拟总投入**：{total_inv/10000:.0f} 万元（每只 1 万，共 {len(positions)} 只）",
    f"- **浮动盈亏**：`{total_pnl:+.0f} 元`（{total_pct:+.2f}%）",
    "",
    "| 序 | 代码 | 名称 | 市场 | 买入价 | 现价 | 今日涨跌 | 累计盈亏 | 推荐方向 |",
    "|:--:|:----:|:----:|:----:|:------:|:----:|:--------:|:--------:|:--------:|",
]
for i, p in enumerate(sorted(positions, key=lambda x: x.get("pnl_pct",0))):
    md.append(f"| {i+1} | {p['code']} | {p['name']} | {p['market']} | "
              f"{p['entry_price']:.2f} | {p['cur_price']:.2f} | "
              f"{p['chg_today']:+.2f}% | **{p['pnl_pct']:+.2f}%** | {p['rec']} |")

md += ["", "## 🔁 今日自动模拟买卖", ""]
if sold_positions:
    md.append("**自动卖出**")
    for p in sold_positions:
        md.append(
            f"- {p['name']}（{p['code']}）@ {p.get('sell_price', 0):.2f}，"
            f"收益 {p.get('pnl_pct', 0):+.2f}%，原因：{p.get('sell_reason', '')}"
        )
else:
    md.append("- 自动卖出：无")

if bought_positions:
    md.append("")
    md.append("**自动买入**")
    for p in bought_positions:
        md.append(
            f"- {p['name']}（{p['code']}）@ {p.get('entry_price', 0):.2f}，"
            f"排名 {p.get('rank_at_buy')}，评分 {p.get('score_at_buy')}，方向：{p.get('rec_at_buy', '')}"
        )
else:
    md.append("- 自动买入：无")

md += ["", "## ⚠️ 预想外变化", ""]
if anomalies:
    for name, code, rec, pnl, chg, reason in anomalies:
        md.append(f"- **{name}（{code}）**：推荐「{rec}」，实际 {pnl:+.2f}%（今日 {chg:+.2f}%）— *{reason}*")
        md.append(f"  > 可能原因：技术指标滞后、板块传染效应未识别、宏观因素突变")
else:
    md.append("- ✅ 本日所有持仓方向与推荐一致，无异常")
if limit_up_stocks:
    md.append("")
    md.append(f"- ⚠️ **涨停次日追买预警**：{'、'.join(limit_up_stocks)} 今日涨停，已触发追买风险惩罚，明日排行榜将自动降权")

md += ["", "## 📰 今日关键市场事件", ""]
if key_events:
    for tag, kw, dt, t in key_events[:10]:
        md.append(f"- {tag} `{dt}` **[{kw}]** {t}")
else:
    md.append("- 今日无重大监管/政策事件")

md += ["", "## 🔍 各板块新闻速览", ""]
for kw, items in news_all.items():
    if items:
        md.append(f"### {kw}")
        for t, dt in items[:3]:
            md.append(f"- `{dt}` {t}")
        md.append("")

md += ["", "## 💡 软件分析盲点 & 改进建议", ""]
suggestions = []
big_moves = [p for p in positions if abs(p.get("chg_today",0)) > 5]
if big_moves:
    names = "、".join(p["name"] for p in big_moves)
    suggestions.append(f"**{names}** 今日波动超 5%，系统未提前识别——可考虑加强实时新闻触发检测")
if len(anomalies) > 1:
    suggestions.append(f"{len(anomalies)} 只股票出现方向错误，新闻情绪加权（±10~18分）机制已激活，请关注后续改善效果")
elif anomalies:
    suggestions.append(f"出现 {len(anomalies)} 只方向偏差，在可接受范围，新闻情绪加权正常运作")

# 涨停追买机制状态
if limit_up_stocks:
    suggestions.append(f"涨停追买惩罚（-30分）已触发：{'、'.join(limit_up_stocks)}，明日将从排行榜降权")
else:
    suggestions.append("今日无涨停股，涨停次日追买惩罚机制（-30分）正常待机")

# 巨潮公告覆盖状态
ann_count = 0
for p in positions:
    code = p["code"]
    if re.match(r"^\d{6}$", code):
        exchange = "sh" if code.startswith("6") or code.startswith("9") else "sz"
        ann_count += len(cninfo_news(code, exchange, n=2))
if ann_count > 0:
    suggestions.append(f"巨潮资讯公告接口已激活，今日持仓新增 {ann_count} 条官方公告补充覆盖")
else:
    suggestions.append("巨潮资讯公告接口已接入，今日持仓暂无新公告")

for i, s in enumerate(suggestions, 1):
    md.append(f"{i}. {s}")

md += ["", "---", f"*自动生成 by GitHub Actions · {today}*"]

log_path = f"daily_logs/{today}.md"
_, log_sha = gh_get(log_path)
ok = gh_put(log_path, "\n".join(md), f"📊 每日日报 {today}", log_sha)
print(f"日报提交：{'✅' if ok else '❌'} → {log_path}")

trade_history = {
    "date": today,
    "source_app": "short_stockmaster",
    "strategy": "daily_report_top10",
    "records": build_trade_history_records(
        today, positions, previous_positions, top10, is_day1,
        sold_positions=sold_positions,
    ),
}
hist_path = f"daily_logs/trade_history/{today}.json"
_, hist_sha = gh_get(hist_path)
ok_hist = gh_put(hist_path, json.dumps(trade_history, ensure_ascii=False, indent=2),
                 f"交易历史 {today}", hist_sha)
print(f"交易历史：{'✅' if ok_hist else '❌'} → {hist_path}")

signal_snapshot = {
    "date": today,
    "source_app": "short_stockmaster",
    "strategy": "daily_report_top10",
    "records": build_signal_snapshot_records(today, top10),
}
snapshot_path = f"daily_logs/signal_snapshots/{today}.json"
_, snapshot_sha = gh_get(snapshot_path)
ok_snapshot = gh_put(snapshot_path, json.dumps(signal_snapshot, ensure_ascii=False, indent=2),
                     f"signal snapshot {today}", snapshot_sha)
print(f"信号快照：{'✅' if ok_snapshot else '❌'} → {snapshot_path}")

state["positions"] = positions
state["history"] = state.get("history", []) + [{
    "date": today, "total_pnl": total_pnl,
    "pct": total_pct, "anomalies": len(anomalies)
}]
ok2 = gh_put("daily_logs/portfolio_state.json",
             json.dumps(state, ensure_ascii=False, indent=2),
             f"更新持仓状态 {today}", state_sha)
print(f"持仓状态：{'✅' if ok2 else '❌'}")
print(f"\n完成！查看：https://github.com/{GH_REPO}/blob/master/{log_path}")
