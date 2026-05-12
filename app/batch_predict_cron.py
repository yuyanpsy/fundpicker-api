"""
独立批量预测 cron 脚本
每次运行预测 500 只新基金，然后退出
通过 Supabase 记录进度，下次从断点继续
Render cron 每 5 分钟调用一次，持续积累直到全部完成
"""
import gc
import os
import sys
import time
import json
import requests
from datetime import datetime

# 添加 app 目录到 path
sys.path.insert(0, os.path.dirname(__file__))

from model_trainer import FundPredictor
from data_collector import (load_nav_data, fetch_fund_nav_from_pingzhongdata,
                            save_single_nav, fetch_fund_rank, DATA_DIR)
from supabase_store import save_predictions, load_predictions, SUPABASE_URL, SUPABASE_KEY

BATCH_SIZE = 500  # 每次跑 500 只
HEADERS_SB = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation,resolution=merge-duplicates"
}


def calc_risk_metrics(df) -> tuple:
    """
    从净值 DataFrame 计算夏普比率、最大回撤、正收益概率
    返回 (sharpe, max_drawdown_pct, positive_pct)
    """
    import numpy as np
    try:
        navs = df.sort_values("date")["nav"].astype(float).values
        if len(navs) < 20:
            return (0.0, 0.0, 0.0)

        # 日收益率
        returns = np.diff(navs) / navs[:-1]
        returns = returns[np.isfinite(returns)]
        if len(returns) < 10:
            return (0.0, 0.0, 0.0)

        # 年化波动率
        daily_vol = np.std(returns)
        annual_vol = daily_vol * np.sqrt(252)

        # 年化收益
        total_return = (navs[-1] - navs[0]) / navs[0]
        n_days = len(returns)
        annual_return = (1 + total_return) ** (252.0 / n_days) - 1

        # 夏普比率（无风险利率 2%）
        sharpe = (annual_return - 0.02) / annual_vol if annual_vol > 0 else 0.0

        # 最大回撤
        peak = navs[0]
        max_dd = 0.0
        for nav in navs:
            if nav > peak:
                peak = nav
            dd = (nav - peak) / peak
            if dd < max_dd:
                max_dd = dd
        max_dd_pct = abs(max_dd) * 100  # 正数百分比

        # 正收益概率
        positive_count = sum(1 for i in range(len(navs)) if navs[-1] > navs[i])
        positive_pct = positive_count / len(navs) * 100

        return (sharpe, max_dd_pct, positive_pct)
    except Exception:
        return (0.0, 0.0, 0.0)


def get_all_fund_codes():
    """获取全部基金代码（去重），目标 20000 只，同时保存涨跌幅"""
    all_codes = []
    code_names = {}  # code -> name
    code_changes = {}  # code -> {day/week/month/3m/6m/year change}
    # 多类型 + 多页拉取，覆盖 20000 只
    fetch_configs = [
        ("all", 5000, 1), ("all", 5000, 2), ("all", 5000, 3), ("all", 5000, 4),
        ("gp", 5000, 1), ("gp", 5000, 2),
        ("hh", 5000, 1), ("hh", 5000, 2),
        ("zs", 5000, 1), ("zs", 5000, 2),
        ("qdii", 3000, 1),
        ("zq", 5000, 1), ("zq", 5000, 2),
    ]
    for fund_type, size, page in fetch_configs:
        if len(all_codes) >= 20000:
            break
        try:
            rank_df = fetch_fund_rank_paged(fund_type, size, page)
            if rank_df is not None and len(rank_df) > 0:
                new_count = 0
                for _, row in rank_df.iterrows():
                    code = row["code"]
                    if code not in code_names:
                        all_codes.append(code)
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
                print(f"  {fund_type} p{page}: {len(rank_df)}只, 新增{new_count}, 累计{len(all_codes)}只")
            del rank_df
            gc.collect()
        except Exception as e:
            print(f"  {fund_type} p{page} 失败: {e}")
        time.sleep(0.5)
    return all_codes[:20000], code_names, code_changes


def fetch_fund_rank_paged(fund_type="all", page_size=5000, page=1):
    """获取基金排行（支持分页）"""
    import re
    url = (f"https://fund.eastmoney.com/data/rankhandler.aspx"
           f"?op=ph&dt=kf&ft={fund_type}&rs=&gs=0&sc=6yzf&st=desc"
           f"&sd=2025-01-01&ed=2026-12-31&qdii=&tabSubtype=,,,,,&pi={page}&pn={page_size}&dx=1"
           f"&v={int(time.time()*1000)}")
    import pandas as pd
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


def get_progress():
    """从 Supabase 读取已完成的预测进度（必须有 sharpe 字段才算完成）"""
    _, all_preds, _ = load_predictions()
    if not all_preds:
        return set()
    # 只有同时有 probability 和 sharpe 的才算完成
    return {k for k, v in all_preds.items() if v.get("sharpe") is not None}


def main():
    start_time = time.time()
    print(f"=== 批量预测 cron 启动 ({datetime.now().isoformat()}) ===")

    # 加载模型
    predictor = FundPredictor(horizon=30)
    try:
        predictor.load("model_30d")
        print(f"模型加载成功")
    except Exception as e:
        print(f"模型加载失败: {e}")
        return

    # 获取全部基金代码
    print("获取基金列表...")
    all_codes, code_names, code_changes = get_all_fund_codes()
    if not all_codes:
        print("无基金代码，退出")
        return
    print(f"总计 {len(all_codes)} 只基金")

    # 获取已预测的（跳过）
    done_codes = get_progress()
    print(f"已预测 {len(done_codes)} 只，跳过")

    # 找出待预测的
    pending = [c for c in all_codes if c not in done_codes]
    print(f"待预测 {len(pending)} 只")

    if not pending:
        print("全部已完成！")
        return

    # 只跑 BATCH_SIZE 只
    batch = pending[:BATCH_SIZE]
    print(f"本次预测 {len(batch)} 只")

    # 加载已有结果（用于追加）
    _, existing_preds, _ = load_predictions()
    results = dict(existing_preds) if existing_preds else {}

    predicted = 0
    for i, code in enumerate(batch):
        try:
            df = load_nav_data(code)
            if df is None or len(df) < 60:
                df = fetch_fund_nav_from_pingzhongdata(code)
                if df is not None and len(df) > 60:
                    save_single_nav(code, df)
                else:
                    continue

            latest_nav = float(df.sort_values("date").iloc[-1]["nav"])

            # 计算风险收益指标（夏普/回撤/正收益率）
            sharpe, max_dd, positive_pct = calc_risk_metrics(df)

            pred = predictor.predict(code)
            del df
            gc.collect()

            if "error" not in pred:
                # 板块归类
                fund_name = code_names.get(code, code)
                sector = ""
                SECTOR_KW = [
                    ("科技",["科技","信息","互联网","数字","电子","计算机","软件","通信","5G","智联","智选"]),
                    ("半导体",["半导体","芯片","集成电路"]),("人工智能",["人工智能","AI","智能","机器人","算力"]),
                    ("医药",["医药","医疗","健康","生物","创新药","中药"]),
                    ("新能源",["新能源","光伏","风电","碳中和"]),
                    ("消费",["消费","食品","饮料","白酒","家电","零售","乐享生活"]),
                    ("金融",["金融","银行","证券","保险"]),("军工",["军工","国防","航天","航空"]),
                    ("新能车",["新能车","汽车","智能驾驶","电动车","锂电"]),
                    ("制造",["制造","工业","机械","装备","智造"]),
                    ("资源",["资源","有色","钢铁","煤炭","化工","材料","黄金"]),
                    ("港股",["港股","恒生","H股","沪港深"]),("海外",["海外","QDII","美国","全球","纳斯达克"]),
                    ("红利",["红利","高股息","分红"]),("成长",["成长","企业成长"]),
                    ("创新",["创新驱动","创新成长","创新"]),
                    ("指数",["沪深300","中证500","中证1000","指数","ETF"]),
                    ("债券",["债券","纯债","信用债","可转债"]),
                    ("混合",["混合","灵活配置","平衡","回报","精选","优选"]),
                ]
                for s_name, s_kws in SECTOR_KW:
                    if any(kw in fund_name for kw in s_kws):
                        sector = s_name
                        break

                changes = code_changes.get(code, {})
                results[code] = {
                    "name": fund_name,
                    "probability": pred["probability"],
                    "confidence": pred["confidence"],
                    "factors": pred.get("factors", [])[:3],
                    "nav_at_predict": latest_nav,
                    "sharpe": round(sharpe, 2),
                    "max_drawdown": round(max_dd, 2),
                    "positive_pct": round(positive_pct, 1),
                    "sector": sector,
                    "year_change": round(changes.get("year_change", 0), 2),
                    "six_month_change": round(changes.get("six_month_change", 0), 2),
                    "three_month_change": round(changes.get("three_month_change", 0), 2),
                    "month_change": round(changes.get("month_change", 0), 2),
                    "week_change": round(changes.get("week_change", 0), 2),
                }
                predicted += 1
            del pred

            # 删除本地 CSV 释放磁盘
            nav_path = os.path.join(DATA_DIR, "nav", f"{code}.csv")
            if os.path.exists(nav_path):
                try:
                    os.remove(nav_path)
                except:
                    pass

        except Exception as e:
            pass

        # 每 100 只打印进度
        if (i + 1) % 100 == 0:
            elapsed = time.time() - start_time
            print(f"  进度 {i+1}/{len(batch)}, 新预测 {predicted} 只, 耗时 {elapsed:.0f}s")
            gc.collect()

        time.sleep(0.15)

    # 保存到 Supabase
    if predicted > 0:
        # TOP10 选择：从 Supabase 全量数据中选金色基金（不只是本批 results）
        # 重新加载最新全量数据确保包含所有已有 sharpe 的基金
        _, fresh_all, _ = load_predictions()
        all_data = fresh_all if fresh_all else results

        golden_funds = [(c, v) for c, v in all_data.items()
                        if v.get("probability", 0) >= 70
                        and v.get("confidence", 0) >= 3
                        and (v.get("sharpe") or 0) > 1.5
                        and (v.get("max_drawdown") or 100) < 15
                        and (v.get("positive_pct") or 0) > 60]
        golden_funds.sort(key=lambda x: x[1].get("probability", 0), reverse=True)

        if len(golden_funds) >= 10:
            top10 = golden_funds[:10]
        else:
            golden_codes = {c for c, _ in golden_funds}
            remaining = [(c, v) for c, v in all_data.items() if c not in golden_codes]
            remaining.sort(key=lambda x: x[1].get("probability", 0), reverse=True)
            top10 = golden_funds + remaining[:10 - len(golden_funds)]

        top10_list = [{"code": c, **v} for c, v in top10]
        print(f"TOP10: 金色{len(golden_funds)}只, 选入{len(top10_list)}只")

        # 分开保存：先写 all_predictions，再单独 PATCH top10（避免大 JSON 超限）
        save_predictions(top10_list, results)
        # 单独更新 top10 字段确保生效
        try:
            import requests as _req
            _req.patch(
                f"{SUPABASE_URL}/rest/v1/fund_predictions?id=eq.latest",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                         "Content-Type": "application/json"},
                json={"top10": top10_list},
                timeout=30
            )
        except Exception:
            pass

        # 快照（只存有 nav 的新预测）
        try:
            from backtest import snapshot_today_predictions
            new_preds = {k: v for k, v in results.items() if v.get("nav_at_predict", 0) > 0}
            snapshot_today_predictions(new_preds, horizon=30)
        except Exception as e:
            print(f"快照失败: {e}")

    elapsed = time.time() - start_time
    print(f"=== 完成: 新预测 {predicted} 只, 总计 {len(results)} 只, 耗时 {elapsed:.0f}s ===")


if __name__ == "__main__":
    main()
