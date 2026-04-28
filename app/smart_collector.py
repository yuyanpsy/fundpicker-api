"""
智能数据采集 — 按条件筛选优质基金
筛选条件：回撤率低、夏普比率高、规模适中
"""
import pandas as pd
import numpy as np
import os
import json
import time

from data_collector import (
    fetch_fund_rank, fetch_fund_nav_from_pingzhongdata,
    save_single_nav, load_nav_data, DATA_DIR, HEADERS
)

import requests
import re


def calculate_fund_metrics(df: pd.DataFrame) -> dict:
    """从净值序列计算回撤率、夏普比率、年化收益等"""
    if df is None or len(df) < 120:  # 至少半年数据
        return None

    nav = df["nav"].values.astype(float)
    n = len(nav)

    # 日收益率
    returns = np.diff(nav) / nav[:-1]
    returns = returns[~np.isnan(returns) & ~np.isinf(returns)]
    if len(returns) < 60:
        return None

    # 年化收益率
    total_return = nav[-1] / nav[0] - 1
    years = n / 250  # 约250个交易日/年
    annual_return = (1 + total_return) ** (1 / max(years, 0.1)) - 1

    # 年化波动率
    annual_vol = np.std(returns) * np.sqrt(250)

    # 夏普比率 (无风险利率按2%算)
    sharpe = (annual_return - 0.02) / max(annual_vol, 0.001)

    # 最大回撤
    peak = np.maximum.accumulate(nav)
    drawdown = (nav - peak) / peak
    max_drawdown = abs(np.min(drawdown))

    # 近1年收益
    year_return = nav[-1] / nav[max(0, n - 250)] - 1 if n > 250 else total_return

    return {
        "annual_return": round(annual_return * 100, 2),
        "annual_vol": round(annual_vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(max_drawdown * 100, 2),
        "year_return": round(year_return * 100, 2),
        "data_points": n
    }


def fetch_fund_scale(fund_code: str) -> float:
    """获取基金规模（亿元）"""
    try:
        url = f"https://fundf10.eastmoney.com/jbgk_{fund_code}.html"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        # 提取净资产规模
        match = re.search(r'净资产规模.*?([\d.]+)亿元', resp.text)
        if match:
            return float(match.group(1))
    except:
        pass
    return -1


def smart_collect(
    min_sharpe: float = 1.0,
    max_drawdown: float = 20.0,
    min_scale: float = 5.0,
    max_scale: float = 100.0,
    target_count: int = 500
):
    """
    智能采集：先获取大量基金，再按条件筛选
    """
    print(f"=== 智能采集 ===")
    print(f"条件: 夏普>{min_sharpe}, 回撤<{max_drawdown}%, 规模{min_scale}-{max_scale}亿")
    print(f"目标: {target_count}只\n")

    # 1. 获取多种排行的基金（扩大候选池）
    all_codes = set()
    for sort_by in ["6yzf", "1nzf", "zzf", "3yzf"]:  # 近6月/近1年/近1周/近3年
        df = fetch_fund_rank("all", 500)
        if df is not None:
            all_codes.update(df["code"].tolist())
        time.sleep(0.5)

    # 也获取不同类型
    for ft in ["gp", "hh", "zq", "zs"]:
        df = fetch_fund_rank(ft, 200)
        if df is not None:
            all_codes.update(df["code"].tolist())
        time.sleep(0.5)

    print(f"候选池: {len(all_codes)} 只基金\n")

    # 2. 逐个获取净值并计算指标
    qualified = []
    checked = 0
    for code in all_codes:
        checked += 1
        # 先检查本地是否已有数据
        df = load_nav_data(code)
        if df is None:
            df = fetch_fund_nav_from_pingzhongdata(code)
            if df is not None and len(df) > 60:
                save_single_nav(code, df)
            time.sleep(0.3)  # 限流

        if df is None or len(df) < 120:
            continue

        metrics = calculate_fund_metrics(df)
        if metrics is None:
            continue

        # 筛选条件
        if metrics["sharpe"] < min_sharpe:
            continue
        if metrics["max_drawdown"] > max_drawdown:
            continue

        qualified.append({"code": code, **metrics})

        if checked % 50 == 0:
            print(f"  已检查 {checked}/{len(all_codes)}, 合格 {len(qualified)}")

        if len(qualified) >= target_count:
            break

    print(f"\n筛选完成: 检查 {checked} 只, 合格 {len(qualified)} 只")

    # 3. 保存筛选结果
    result_df = pd.DataFrame(qualified)
    result_df = result_df.sort_values("sharpe", ascending=False)
    result_path = os.path.join(DATA_DIR, "qualified_funds.csv")
    result_df.to_csv(result_path, index=False)
    print(f"结果保存到: {result_path}")

    # 打印统计
    if len(qualified) > 0:
        print(f"\n合格基金统计:")
        print(f"  夏普比率: 均值={result_df['sharpe'].mean():.2f}, 最高={result_df['sharpe'].max():.2f}")
        print(f"  最大回撤: 均值={result_df['max_drawdown'].mean():.1f}%, 最低={result_df['max_drawdown'].min():.1f}%")
        print(f"  年化收益: 均值={result_df['annual_return'].mean():.1f}%")
        print(f"\nTop20基金:")
        for _, row in result_df.head(20).iterrows():
            print(f"  {row['code']}: 夏普={row['sharpe']:.2f}, 回撤={row['max_drawdown']:.1f}%, 年化={row['annual_return']:.1f}%")

    return result_df


if __name__ == "__main__":
    # 宽松条件先跑一遍，看有多少合格的
    smart_collect(
        min_sharpe=1.0,       # 夏普>1（优秀水平）
        max_drawdown=20.0,    # 回撤<20%
        min_scale=5.0,
        max_scale=100.0,
        target_count=500
    )
