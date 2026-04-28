"""
基金数据采集模块
直接HTTP请求东方财富公开接口（不依赖AKShare的JS执行）
"""
import pandas as pd
import numpy as np
import requests
import re
import json
import time
import os
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "Referer": "https://fund.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def fetch_fund_rank(fund_type="all", page_size=200):
    """获取基金排行（直接HTTP请求）"""
    print(f"正在获取基金排行 (类型={fund_type}, top={page_size})...")
    url = (f"https://fund.eastmoney.com/data/rankhandler.aspx"
           f"?op=ph&dt=kf&ft={fund_type}&rs=&gs=0&sc=6yzf&st=desc"
           f"&sd=2025-01-01&ed=2026-12-31&qdii=&tabSubtype=,,,,,&pi=1&pn={page_size}&dx=1"
           f"&v={int(time.time()*1000)}")
    resp = requests.get(url, headers={"Referer": "https://fund.eastmoney.com/data/fundranking.html"})
    text = resp.text

    # 解析 datas:["code,name,...", ...]
    match = re.search(r'datas:\[(.*?)\]', text, re.DOTALL)
    if not match:
        print("解析排行数据失败")
        return pd.DataFrame()

    items = re.findall(r'"([^"]+)"', match.group(1))
    rows = []
    for item in items:
        fields = item.split(",")
        if len(fields) < 20:
            continue
        rows.append({
            "code": fields[0],
            "name": fields[1],
            "nav_date": fields[3],
            "nav": float(fields[4]) if fields[4] else 0,
            "acc_nav": float(fields[5]) if fields[5] else 0,
            "day_change": float(fields[6]) if fields[6] else 0,
            "week_change": float(fields[7]) if fields[7] else 0,
            "month_change": float(fields[8]) if fields[8] else 0,
            "three_month_change": float(fields[9]) if fields[9] else 0,
            "six_month_change": float(fields[10]) if fields[10] else 0,
            "year_change": float(fields[11]) if fields[11] else 0,
        })

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA_DIR, f"fund_rank_{fund_type}.csv"), index=False)
    print(f"获取到 {len(df)} 只基金排行")
    return df


def fetch_fund_nav_from_pingzhongdata(fund_code: str):
    """
    从pingzhongdata获取全量净值数据
    这个接口返回JS变量，包含Data_netWorthTrend（成立以来全部净值）
    """
    url = f"https://fund.eastmoney.com/pingzhongdata/{fund_code}.js?v={int(time.time()*1000)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        text = resp.text

        # 提取 Data_netWorthTrend = [{x:timestamp, y:nav, equityReturn:pct}, ...]
        match = re.search(r'Data_netWorthTrend\s*=\s*\[(.*?)\];', text, re.DOTALL)
        if not match:
            return None

        # 解析JSON数组
        array_str = "[" + match.group(1) + "]"
        # 提取 x(时间戳) 和 y(净值) 和 equityReturn(日涨跌幅)
        pattern = re.compile(r'"x":(\d+),"y":([\d.]+),"equityReturn":([-\d.]+)')
        points = pattern.findall(array_str)

        if not points:
            return None

        rows = []
        for ts, nav, ret in points:
            date = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
            rows.append({
                "date": date,
                "nav": float(nav),
                "daily_return": float(ret) / 100  # 转为小数
            })

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)
        return df

    except Exception as e:
        print(f"  获取 {fund_code} 失败: {e}")
        return None


def batch_fetch_nav(fund_codes: list):
    """批量获取基金净值数据"""
    all_data = {}
    total = len(fund_codes)
    success = 0
    for i, code in enumerate(fund_codes):
        print(f"[{i+1}/{total}] 获取 {code} 净值...", end=" ")
        df = fetch_fund_nav_from_pingzhongdata(code)
        if df is not None and len(df) > 60:
            all_data[code] = df
            save_single_nav(code, df)
            success += 1
            print(f"OK ({len(df)}条)")
        else:
            print("失败或数据不足")

        # 每10只暂停1秒，避免被限流
        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{total}, 成功: {success}")
            time.sleep(1)

    print(f"\n采集完成: {success}/{total} 只基金成功")
    return all_data


def save_single_nav(code: str, df: pd.DataFrame):
    """保存单只基金净值"""
    nav_dir = os.path.join(DATA_DIR, "nav")
    os.makedirs(nav_dir, exist_ok=True)
    df.to_csv(os.path.join(nav_dir, f"{code}.csv"), index=False)


def load_nav_data(fund_code: str) -> pd.DataFrame:
    """从本地加载净值数据"""
    path = os.path.join(DATA_DIR, "nav", f"{fund_code}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["date"])
        return df
    return None


if __name__ == "__main__":
    # 1. 获取排行前200只基金
    rank_df = fetch_fund_rank("all", 200)

    if rank_df is not None and len(rank_df) > 0:
        codes = rank_df["code"].tolist()
        print(f"\n开始批量获取 {len(codes)} 只基金的净值数据...")
        print("(使用pingzhongdata接口，每只基金获取成立以来全部净值)\n")
        batch_fetch_nav(codes)
    else:
        print("获取排行失败!")
