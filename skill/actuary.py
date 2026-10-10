#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦抽奖精算师 CLI —— 麦当劳积分抽奖期望值计算器
================================================
不依赖第三方库，Python 3.8+ 开箱即用。

用法：
    set MCD_MCP_TOKEN=你的token        (Windows)
    export MCD_MCP_TOKEN=你的token      (Linux/macOS)
    python actuary.py                   # 完整精算报告
    python actuary.py --json            # 输出原始 JSON

精算框架（三档价值分层）：
    1. 无条件回血：积分奖（中 X 积分，直接回本 X/成本）
    2. 有条件变现：立减券/满减券（面值 ¥X，但需消费激活）
    3. 增值型：折扣券（价值 = 原价 × (1-折扣)，需消费且价值随客单波动）
    麦当劳官方未公示中奖概率 → 用区间分析替代点估计，绝不编造概率。
"""

import argparse
import json
import os
import re
import statistics
import sys
import urllib.request

MCP_URL = "https://mcp.mcd.cn"
_TOKEN = os.environ.get("MCD_MCP_TOKEN", "")
_req_id = [100]


# ─────────────────────────── MCP 调用层 ───────────────────────────

def mcp_call(tool_name: str, arguments: dict = None) -> dict:
    """调用麦当劳 MCP 工具，返回解析后的 JSON。"""
    if not _TOKEN:
        sys.exit("❌ 未设置环境变量 MCD_MCP_TOKEN（前往 https://open.mcd.cn/mcp 申请）")
    _req_id[0] += 1
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": _req_id[0],
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }).encode("utf-8")
    req = urllib.request.Request(MCP_URL, data=payload, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer " + _TOKEN,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if "error" in data:
        sys.exit(f"❌ MCP 错误: {data['error']}")
    content = data["result"]["content"][0]["text"]
    return extract_json(content) or {"_raw_text": content}


def extract_json(text: str):
    """MCP 返回的 text 里嵌着原始 JSON（可能带前后缀说明文字），稳健提取。"""
    # 路径1：标准 "## Original Response" 段
    m = re.search(r"## Original Response\s*\n(.*?)(?:\n\n|$)", text, re.S)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 路径2：直接找 {"success":... 开头的平衡 JSON
    m = re.search(r'\{"success"', text)
    if m:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[m.start():])
            return obj
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def walk(node):
    """深度遍历，产出所有 dict 节点。"""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def to_num(v):
    try:
        return float(str(v).replace(",", "").replace("¥", "").strip())
    except (TypeError, ValueError):
        return None


# ─────────────────────────── 数据解析层 ───────────────────────────

def parse_account(data: dict) -> dict:
    """query-my-account → 积分资产。"""
    for d in walk(data):
        if "availablePoint" in d:
            def g(*keys):
                for k in keys:
                    if k in d:
                        return to_num(d[k]) or 0
                return 0
            return {
                "available": g("availablePoint"),
                "expire_this_month": g("currentMouthExpirePoint"),
                "expire_next_month": g("nextMouthExpirePoint"),
            }
    return {"available": None, "expire_this_month": None, "expire_next_month": None}


def parse_mall(data: dict) -> dict:
    """mall-points-products → 商城结构分析（汇率锚点勘察）。"""
    items = []
    for d in walk(data):
        if "spuName" in d:
            items.append({
                "name": d.get("spuName"), "point": to_num(d.get("point")) or 0,
                "price": to_num(d.get("price")) or 0, "cat": d.get("catName"),
                "shopId": d.get("shopId"),
            })
    return {
        "total": len(items),
        "pure_points": [i for i in items if i["point"] > 0 and i["price"] == 0],
        "cash_items": [i for i in items if i["price"] > 0],
    }


def classify_prize(name: str) -> dict:
    """把奖品名解析成结构化价值档位。"""
    name = name or ""
    # 积分奖：无条件回血
    m = re.search(r"(\d+)\s*会员积分", name)
    if m:
        return {"tier": "points", "name": name, "points_back": float(m.group(1)), "note": "无条件回血"}
    # 霸王餐
    if "霸王餐" in name:
        return {"tier": "jackpot", "name": name, "note": "大奖，价值高（一餐约 ¥30-60）"}
    # 会员卡/权益卡
    if "卡" in name and "天" in name:
        return {"tier": "card", "name": name, "note": "权益卡，价值取决于使用频次"}
    # 立减券：满减或直接立减，面值可定价，需消费激活
    m = re.search(r"满\s*\d+\s*减\s*(\d+(?:\.\d+)?)\s*元", name)
    if m:
        return {"tier": "cash_coupon", "name": name, "value": float(m.group(1)),
                "note": f"需消费满额激活，面值 ¥{m.group(1)}"}
    m = re.search(r"立减\s*(\d+(?:\.\d+)?)\s*元", name)
    if m:
        return {"tier": "cash_coupon", "name": name, "value": float(m.group(1)),
                "note": f"需消费激活，面值 ¥{m.group(1)}"}
    # X元优惠券（如 可乐3.6元券 / 麦旋风7元券）：抵扣到指定价
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", name)
    if m and "券" in name:
        return {"tier": "cash_coupon", "name": name, "value": float(m.group(1)),
                "note": f"指定商品抵扣券，面值约 ¥{m.group(1)}"}
    # 折扣券
    m = re.search(r"(\d+(?:\.\d+)?)\s*折", name)
    if m:
        return {"tier": "discount", "name": name, "discount": float(m.group(1)) / 10,
                "note": f"需消费激活，价值随客单价波动"}
    return {"tier": "other", "name": name, "note": "价值待估"}


def parse_lottery(data: dict) -> dict:
    """query-lottery-info → 抽奖结构（按实测 schema）。"""
    d = (data or {}).get("data") or {}
    prizes = [classify_prize(p.get("name")) for p in (d.get("prizes") or [])]
    return {
        "name": d.get("activityName"),
        "status": d.get("activityStatusText"),
        "cost_per_draw": to_num(d.get("drawPoint")),
        "available_point": to_num(d.get("availablePoint")),
        "has_probability": "prob" in json.dumps(d).lower(),
        "prizes": prizes,
    }


# ─────────────────────────── 报告层 ───────────────────────────

def fmt(n):
    if n is None:
        return "?"
    return f"{n:,.0f}" if n is not None and float(n) == int(n) else f"{n:,.2f}"


def build_report(account, mall, lottery, prizes_hist=None):
    lines = []
    lines.append("📊 麦麦抽奖精算报告")
    lines.append("─" * 46)

    # 1) 资产
    warn = ""
    if account["expire_this_month"]:
        warn = f"（⚠️ {fmt(account['expire_this_month'])} 分本月过期！）"
    lines.append(f"💰 积分资产：可用 {fmt(account['available'])} 分 {warn}")
    if account["available"] and lottery["cost_per_draw"]:
        n_draws = int(account["available"] // lottery["cost_per_draw"])
        lines.append(f"    → 可抽 {n_draws} 次（{fmt(lottery['cost_per_draw'])} 分/次）")

    # 2) 活动
    if not lottery["prizes"]:
        lines.append("🎰 抽奖活动：当前无进行中的活动")
        lines.append("─" * 46)
        lines.append("💡 建议：积分用于商城兑换，等下一期活动")
        return "\n".join(lines)

    lines.append(f"🎰 活动：{lottery['name']}（{lottery['status']}）")
    lines.append(f"    消耗 {fmt(lottery['cost_per_draw'])} 积分/次 · {len(lottery['prizes'])} 档奖品"
                 f" · {'概率已公示 ✅' if lottery['has_probability'] else '官方未公示概率 ⚠️'}")

    # 3) 奖品分层
    points_p = [p for p in lottery["prizes"] if p["tier"] == "points"]
    cash_p = [p for p in lottery["prizes"] if p["tier"] == "cash_coupon"]
    disc_p = [p for p in lottery["prizes"] if p["tier"] == "discount"]
    other_p = [p for p in lottery["prizes"] if p["tier"] in ("jackpot", "card", "other")]

    if points_p:
        pb = [p["points_back"] for p in points_p]
        lines.append("")
        lines.append(f"📌 第①层 · 无条件回血（积分奖，{len(points_p)} 档）")
        lines.append(f"    回血 {fmt(min(pb))} ~ {fmt(max(pb))} 分/次 → 回收率 "
                     f"{min(pb)/lottery['cost_per_draw']*100:.0f}% ~ {max(pb)/lottery['cost_per_draw']*100:.0f}%")
    if cash_p:
        vals = [p["value"] for p in cash_p if p.get("value")]
        lines.append(f"📌 第②层 · 有条件变现（立减/抵扣券，{len(cash_p)} 档）")
        lines.append(f"    面值 ¥{fmt(min(vals))} ~ ¥{fmt(max(vals))}，需下单消费激活；不消费则价值=0")
    if disc_p:
        lines.append(f"📌 第③层 · 增值型（折扣券，{len(disc_p)} 档）")
        lines.append(f"    4折~6.8折，价值=原价×(1-折扣)，需消费且随客单价波动")
    if other_p:
        lines.append(f"📌 第④层 · 大奖/权益（{len(other_p)} 档）")
        for p in other_p:
            lines.append(f"    · {p['name']}（{p['note']}）")

    # 4) 结论
    lines.append("")
    lines.append("─" * 46)
    cost = lottery["cost_per_draw"]
    if not lottery["has_probability"]:
        lines.append("📈 期望值：概率未公示，无法点估计，给出区间判断：")
        if points_p:
            worst = min(p["points_back"] for p in points_p)
            lines.append(f"    · 最差情形：全中最低积分奖 → 每抽净亏 {fmt(cost - worst)} 分（-{(cost-worst)/cost*100:.0f}%）")
        no_blank = "无「谢谢参与」档" if not any("谢谢" in (p['name'] or '') for p in lottery["prizes"]) else "存在「谢谢参与」档"
        lines.append(f"    · 奖池结构：{no_blank}，即每次抽奖必有回报（最低回报为积分回血）")
        if cash_p:
            max_v = max(p["value"] for p in cash_p if p.get("value"))
            lines.append(f"    · 若中最高现金券 ¥{fmt(max_v)}，需为它消费 → 券只对『本来就要吃的人』值钱")
        # 决策规则
        lines.append("")
        lines.append("🎯 精算师结论：")
        if points_p:
            avg_pb = statistics.mean(p["points_back"] for p in points_p)
            lines.append(f"    1. 若你把积分当现金（不打算近期消费麦当劳）：抽奖≈负期望，"
                         f"积分奖均值回血 {avg_pb/cost*100:.0f}%，其余奖品不消费不值钱")
            lines.append(f"    2. 若你本周本来就吃麦当劳：券类奖品能激活，抽奖期望明显改善，可小抽怡情")
        if account["expire_this_month"] and account["expire_this_month"] >= cost:
            lines.append(f"    3. ⚠️ 你有 {fmt(account['expire_this_month'])} 分本月过期，放着=100%归零；"
                         f"抽掉哪怕全中最低档也回血 33%，过期的分抽掉优于烂掉")
    if prizes_hist:
        lines.append(f"📜 历史战绩：约 {prizes_hist} 条中奖记录（query-my-prizes）")
    lines.append("─" * 46)
    lines.append("⚖️ 声明：本报告由公开接口数据计算，奖品概率以麦当劳官方公示为准")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    ap.add_argument("--no-prizes", action="store_true", help="跳过历史中奖记录")
    args = ap.parse_args()

    print("⏳ 拉取积分账户…")
    account = parse_account(mcp_call("query-my-account"))
    print("⏳ 拉取麦麦商城…")
    mall = parse_mall(mcp_call("mall-points-products"))
    print("⏳ 拉取抽奖活动…")
    lottery = parse_lottery(mcp_call("query-lottery-info"))

    if args.json:
        print(json.dumps({"account": account, "mall": mall, "lottery": lottery},
                         ensure_ascii=False, indent=2))
        return

    hist = None
    if not args.no_prizes:
        try:
            print("⏳ 拉取历史中奖…")
            raw = json.dumps(mcp_call("query-my-prizes"), ensure_ascii=False)
            hist = max(0, raw.count("奖品") + raw.count("prize") - 1)
        except Exception as e:
            hist = None
            print(f"（历史中奖查询失败：{e}）")

    print()
    print(build_report(account, mall, lottery, hist))


if __name__ == "__main__":
    main()
