"""
补数据脚本：为已有预测结果补充涨跌幅 + 板块归类
不需要跑模型，直接从东方财富排行榜拉涨跌幅，按基金名称归类板块
一次性跑完，覆盖 Supabase 中所有缺失 year_change/sector 的记录
"""
import gc
import re
import time
import requests
import pandas as pd
from datetime import datetime

SUPABASE_URL = "https://edzsmjegnkrbedqpotgu.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkenNtamVnbmtyYmVkcXBvdGd1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYzMDA5NDcsImV4cCI6MjA5MTg3Njk0N30.J1gHxRiRgEBSMtd3WwhmkwiO2bIpNJy2LDsphD0SPQU"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation,resolution=merge-duplicates"
}

SECTOR_KW = [
    ("科技", ["科技", "信息", "互联网", "数字", "电子", "计算机", "软件", "通信", "5G", "智联", "智选"]),
    ("半导体", ["半导体", "芯片", "集成电路"]),
    ("人工智能", ["人工智能", "AI", "智能", "机器人", "算力"]),
    ("医药", ["医药", "医疗", "健康", "生物", "创新药", "中药"]),
    ("新能源", ["新能源", "光伏", "风电", "碳中和"]),
    ("消费", ["消费", "食品", "饮料", "白酒", "家电", "零售", "乐享生活"]),
    ("金融", ["金融", "银行", "证券", "保险"]),
    ("军工", ["军工", "国防", "航天", "航空"]),
    ("新能车", ["新能车", "汽车", "智能驾驶", "电动车", "锂电"]),
    ("制造", ["制造", "工业", "机械", "装备", "智造"]),
    ("资源", ["资源", "有色", "钢铁", "煤炭", "化工", "材料", "黄金"]),
    ("港股", ["港股", "恒生", "H股", "沪港深"]),
    ("海外", ["海外", "QDII", "美国", "全球", "纳斯达克"]),
    ("红利", ["红利", "高股息", "分红"]),
    ("地产", ["地产", "房地产"]),
    ("成长", ["成长", "企业成长"]),
    ("创新", ["创新驱动", "创新成长", "创新"]),
    ("指数", ["沪深300", "中证500", "中证1000", "指数", "ETF"]),
    ("债券", ["债券", "纯债", "信用债", "可转债"]),
    ("混合", ["混合", "灵活配置", "平衡", "回报", "精选", "优选"]),
]


def classify_sector(fund_name: str) -> str:
    for s_name, s_kws in SECTOR_KW:
        if any(kw in fund_name for kw in s_kws):
            return s_name
    return ""


def fetch_fund_rank_paged(fund_type="all", page_size=5000, page=1):
    """获取基金排行（支持分页）"""
    url = (f"https://fund.eastmoney.com/data/rankhandler.aspx"
           f"?op=ph&dt=kf&ft={fund_type}&rs=&gs=0&sc=6yzf&st=desc"
           f"&sd=2025-01-01&ed=2026-12-31&qdii=&tabSubtype=,,,,,&pi={page}&pn={page_size}&dx=1"
           f"&v={int(time.time() * 1000)}")
    resp = requests.get(url, headers={"Referer": "https://fund.eastmoney.com/data/fundranking.html"}, timeout=30)
    text = resp.text
    match = re.search(r'datas:\[(.*?)\]', text, re.DOTALL)
    if not match:
        return None
    items = re.findall(r'"([^"]+)"', match.group(1))
    rows = []
    for item in items:
        fields = item.split(",")
        if len(fields) < 20:
            continue
        rows.append({
            "code": fields[0],
            "name": fields[1],
            "day_change": float(fields[6]) if fields[6] else 0,
            "week_change": float(fields[7]) if fields[7] else 0,
            "month_change": float(fields[8]) if fields[8] else 0,
            "three_month_change": float(fields[9]) if fields[9] else 0,
            "six_month_change": float(fields[10]) if fields[10] else 0,
            "year_change": float(fields[11]) if fields[11] else 0,
        })
    return pd.DataFrame(rows) if rows else None


def fetch_all_fund_changes():
    """从东方财富拉全量涨跌幅数据"""
    code_names = {}
    code_changes = {}
    fetch_configs = [
        ("all", 5000, 1), ("all", 5000, 2), ("all", 5000, 3), ("all", 5000, 4),
        ("gp", 5000, 1), ("gp", 5000, 2),
        ("hh", 5000, 1), ("hh", 5000, 2),
        ("zs", 5000, 1), ("zs", 5000, 2),
        ("qdii", 3000, 1),
        ("zq", 5000, 1), ("zq", 5000, 2),
    ]
    for fund_type, size, page in fetch_configs:
        try:
            rank_df = fetch_fund_rank_paged(fund_type, size, page)
            if rank_df is not None and len(rank_df) > 0:
                new_count = 0
                for _, row in rank_df.iterrows():
                    code = row["code"]
                    if code not in code_names:
                        code_names[code] = row.get("name", code)
                        code_changes[code] = {
                            "day_change": row.get("day_change", 0),
                            "week_change": row.get("week_change", 0),
                            "month_change": row.get("month_change", 0),
                            "three_month_change": row.get("three_month_change", 0),
                            "six_month_change": row.get("six_month_change", 0),
                            "year_change": row.get("year_change", 0),
                        }
                        new_count += 1
                print(f"  {fund_type} p{page}: {len(rank_df)}只, 新增{new_count}, 累计{len(code_names)}只")
            del rank_df
            gc.collect()
        except Exception as e:
            print(f"  {fund_type} p{page} 失败: {e}")
        time.sleep(0.5)
    return code_names, code_changes


def load_predictions():
    """从 Supabase 加载全量预测"""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/fund_predictions?id=eq.latest&select=*",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=30
    )
    if resp.status_code == 200:
        data = resp.json()
        if data:
            row = data[0]
            return row.get("all_predictions", {}), row.get("top10", [])
    return {}, []


def save_predictions(all_predictions, top10):
    """保存到 Supabase"""
    data = [{"id": "latest", "top10": top10, "all_predictions": all_predictions}]
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/fund_predictions?on_conflict=id",
        headers=HEADERS,
        json=data,
        timeout=60
    )
    print(f"保存到 Supabase: {resp.status_code}")
    return resp.status_code < 300


def main():
    start = time.time()
    print(f"=== 补数据脚本启动 ({datetime.now().isoformat()}) ===")

    # 1. 从东方财富拉全量涨跌幅
    print("\n[1/3] 拉取东方财富涨跌幅数据...")
    code_names, code_changes = fetch_all_fund_changes()
    print(f"共获取 {len(code_names)} 只基金的涨跌幅数据")

    # 2. 从 Supabase 加载已有预测
    print("\n[2/3] 加载 Supabase 已有预测...")
    all_preds, top10 = load_predictions()
    print(f"已有预测 {len(all_preds)} 只")

    # 3. 补充数据
    print("\n[3/3] 补充涨跌幅 + 板块归类...")
    updated_count = 0
    for code, pred in all_preds.items():
        changed = False

        # 补涨跌幅
        if code in code_changes:
            changes = code_changes[code]
            if not pred.get("year_change") or pred["year_change"] == 0:
                pred["year_change"] = round(changes["year_change"], 2)
                pred["six_month_change"] = round(changes["six_month_change"], 2)
                pred["three_month_change"] = round(changes["three_month_change"], 2)
                pred["month_change"] = round(changes["month_change"], 2)
                pred["week_change"] = round(changes["week_change"], 2)
                changed = True

        # 补板块归类
        if not pred.get("sector"):
            fund_name = pred.get("name") or code_names.get(code, "")
            if fund_name:
                sector = classify_sector(fund_name)
                if sector:
                    pred["sector"] = sector
                    changed = True

        # 补基金名称（有些早期记录名称缺失）
        if (not pred.get("name") or pred["name"] == code) and code in code_names:
            pred["name"] = code_names[code]
            changed = True

        if changed:
            updated_count += 1

    print(f"补充了 {updated_count} 只基金的数据")

    # 统计
    has_year = sum(1 for v in all_preds.values() if v.get("year_change") and v["year_change"] != 0)
    has_sector = sum(1 for v in all_preds.values() if v.get("sector"))
    print(f"补充后: 有涨跌幅={has_year}只, 有板块={has_sector}只")

    # 4. 保存
    if updated_count > 0:
        print("\n保存到 Supabase...")
        save_predictions(all_preds, top10)

    elapsed = time.time() - start
    print(f"\n=== 完成，耗时 {elapsed:.0f}s ===")


if __name__ == "__main__":
    main()
