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


def get_all_fund_codes():
    """获取全部基金代码（去重）"""
    all_codes = []
    code_names = {}
    for fund_type, size in [("all", 5000), ("gp", 5000), ("hh", 3000), ("zs", 3000), ("qdii", 2000)]:
        try:
            rank_df = fetch_fund_rank(fund_type, size)
            if rank_df is not None and len(rank_df) > 0:
                for _, row in rank_df.iterrows():
                    code = row["code"]
                    if code not in code_names:
                        all_codes.append(code)
                        code_names[code] = row.get("name", code)
                print(f"  {fund_type}: {len(rank_df)}只, 累计去重: {len(all_codes)}只")
            del rank_df
            gc.collect()
        except Exception as e:
            print(f"  {fund_type} 失败: {e}")
        time.sleep(0.5)
    return all_codes, code_names


def get_progress():
    """从 Supabase 读取已完成的预测进度"""
    _, all_preds, _ = load_predictions()
    return set(all_preds.keys()) if all_preds else set()


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
    all_codes, code_names = get_all_fund_codes()
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
            pred = predictor.predict(code)
            del df
            gc.collect()

            if "error" not in pred:
                results[code] = {
                    "name": code_names.get(code, code),
                    "probability": pred["probability"],
                    "confidence": pred["confidence"],
                    "factors": pred.get("factors", [])[:3],
                    "nav_at_predict": latest_nav
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
        top10 = sorted(results.items(),
                       key=lambda x: x[1].get("probability", 0), reverse=True)[:10]
        top10_list = [{"code": c, **v} for c, v in top10]
        save_predictions(top10_list, results)

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
