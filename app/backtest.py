"""
AI 预测回测闭环
- snapshot_today_predictions: 每天把当前预测存快照（保存当时 nav）
- verify_old_snapshots: 对 30 天前的快照用最新 nav 回填实际涨跌
- aggregate_win_rates: 按档位聚合胜率，写入 fund_prediction_backtest 表
"""
import requests
from datetime import datetime, date, timedelta
from typing import Optional

from supabase_store import SUPABASE_URL, SUPABASE_KEY, HEADERS
from data_collector import load_nav_data, fetch_fund_nav_from_pingzhongdata, save_single_nav


def _get_latest_nav(fund_code: str) -> Optional[float]:
    """获取基金最新净值（本地缓存 + 在线兜底）"""
    df = load_nav_data(fund_code)
    if df is None or len(df) == 0:
        df = fetch_fund_nav_from_pingzhongdata(fund_code)
        if df is not None and len(df) > 0:
            save_single_nav(fund_code, df)
    if df is None or len(df) == 0:
        return None
    try:
        return float(df.sort_values("date").iloc[-1]["nav"])
    except Exception:
        return None


def snapshot_today_predictions(all_predictions: dict, horizon: int = 30) -> int:
    """
    把当前的全量预测 + 当时净值存为一份快照
    每天 cronjob 批量预测完后调用一次
    返回写入条数
    """
    if not all_predictions:
        return 0

    today = date.today().isoformat()
    rows = []
    missing_nav = 0
    for code, pred in all_predictions.items():
        prob = pred.get("probability")
        if prob is None or prob == 0:
            continue
        # 优先用预测时已保存的净值（api_server 存 results 时带上）
        nav = pred.get("nav_at_predict")
        if nav is None or nav <= 0:
            nav = _get_latest_nav(code)
            if nav is None or nav <= 0:
                missing_nav += 1
                continue
        rows.append({
            "snapshot_date": today,
            "fund_code": code,
            "horizon_days": horizon,
            "probability": float(prob),
            "confidence": int(pred.get("confidence", 3)),
            "nav_at_predict": float(nav),
            "fund_name": pred.get("name", code),
        })

    if not rows:
        print(f"[backtest] 快照无有效数据（missing_nav={missing_nav}/{len(all_predictions)}）")
        return 0

    # 分批插入，避免单请求过大
    written = 0
    batch = 500
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        try:
            resp = requests.post(
                f"{SUPABASE_URL}/rest/v1/fund_prediction_snapshots?on_conflict=snapshot_date,fund_code,horizon_days",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates"},
                json=chunk,
                timeout=60
            )
            if resp.status_code < 300:
                written += len(chunk)
            else:
                print(f"snapshot batch {i} 失败: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"snapshot batch {i} 异常: {e}")
    print(f"[backtest] 快照写入 {written}/{len(rows)} 条 (missing_nav={missing_nav})")
    return written


def verify_old_snapshots(horizon: int = 30, batch_size: int = 500) -> int:
    """
    对 horizon 天前（及更早）还没对账的快照，用最新 nav 回填实际涨跌
    每天 cronjob 调用一次
    返回对账条数
    """
    cutoff = (date.today() - timedelta(days=horizon)).isoformat()
    # 拉取未对账的旧快照
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/fund_prediction_snapshots"
            f"?snapshot_date=lte.{cutoff}"
            f"&verified_at=is.null"
            f"&horizon_days=eq.{horizon}"
            f"&select=snapshot_date,fund_code,horizon_days,nav_at_predict"
            f"&limit={batch_size}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=30
        )
        if resp.status_code != 200:
            print(f"[backtest] 拉取未对账快照失败: {resp.status_code}")
            return 0
        pending = resp.json()
    except Exception as e:
        print(f"[backtest] 拉取异常: {e}")
        return 0

    if not pending:
        print("[backtest] 无待对账快照")
        return 0

    print(f"[backtest] 待对账 {len(pending)} 条（horizon={horizon}）")
    updated = 0
    for row in pending:
        code = row["fund_code"]
        nav_before = float(row["nav_at_predict"])
        nav_after = _get_latest_nav(code)
        if nav_after is None or nav_after <= 0 or nav_before <= 0:
            continue
        return_pct = (nav_after - nav_before) / nav_before * 100
        actual_up = return_pct > 0

        try:
            r = requests.patch(
                f"{SUPABASE_URL}/rest/v1/fund_prediction_snapshots"
                f"?snapshot_date=eq.{row['snapshot_date']}"
                f"&fund_code=eq.{code}"
                f"&horizon_days=eq.{horizon}",
                headers=HEADERS,
                json={
                    "actual_nav_after": nav_after,
                    "actual_return_pct": round(return_pct, 4),
                    "actual_up": actual_up,
                    "verified_at": datetime.now().isoformat()
                },
                timeout=15
            )
            if r.status_code < 300:
                updated += 1
        except Exception:
            pass

    print(f"[backtest] 对账完成: {updated}/{len(pending)}")
    return updated


def aggregate_win_rates(horizon: int = 30) -> dict:
    """
    按概率档位聚合胜率，写入 fund_prediction_backtest 表
    档位：50-60 / 60-70 / 70-80 / 80-90 / 90-100
    """
    buckets = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]
    results = {}

    for lo, hi in buckets:
        bucket_key = f"{lo}-{hi}"
        try:
            # 拉取该档位已对账的记录（Supabase REST 不支持聚合，前端拉全量再统计）
            resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/fund_prediction_snapshots"
                f"?horizon_days=eq.{horizon}"
                f"&probability=gte.{lo}"
                f"&probability=lt.{hi}"
                f"&actual_up=not.is.null"
                f"&select=actual_up,actual_return_pct,snapshot_date"
                f"&limit=50000",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                timeout=60
            )
            if resp.status_code != 200:
                continue
            rows = resp.json()
        except Exception as e:
            print(f"[backtest] 档位 {bucket_key} 拉取失败: {e}")
            continue

        total = len(rows)
        wins = sum(1 for r in rows if r.get("actual_up"))
        win_rate = wins / total if total > 0 else 0.0
        avg_return = (
            sum(r["actual_return_pct"] for r in rows if r.get("actual_return_pct") is not None) / total
            if total > 0 else None
        )
        dates = [r["snapshot_date"] for r in rows if r.get("snapshot_date")]

        record = {
            "horizon_days": horizon,
            "bucket": bucket_key,
            "bucket_min": lo,
            "bucket_max": hi,
            "total_count": total,
            "win_count": wins,
            "win_rate": round(win_rate, 4),
            "avg_actual_return": round(avg_return, 4) if avg_return is not None else None,
            "sample_start_date": min(dates) if dates else None,
            "sample_end_date": max(dates) if dates else None,
            "updated_at": datetime.now().isoformat()
        }
        results[bucket_key] = record

        # 写回聚合表
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/fund_prediction_backtest?on_conflict=horizon_days,bucket",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates"},
                json=[record],
                timeout=15
            )
            if r.status_code >= 300:
                print(f"[backtest] {bucket_key} 写回失败: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"[backtest] {bucket_key} 写回异常: {e}")

    print(f"[backtest] 胜率聚合完成: {results}")
    return results


def run_daily_backtest(all_predictions: dict, horizon: int = 30):
    """
    每日回测流程（在每次批量预测完成后调用）：
    1. 存当天快照
    2. 对 30 天前的快照回填实际涨跌
    3. 重新聚合各档位胜率
    """
    try:
        snapshot_today_predictions(all_predictions, horizon=horizon)
    except Exception as e:
        print(f"[backtest] snapshot 异常: {e}")
    try:
        verify_old_snapshots(horizon=horizon)
    except Exception as e:
        print(f"[backtest] verify 异常: {e}")
    try:
        aggregate_win_rates(horizon=horizon)
    except Exception as e:
        print(f"[backtest] aggregate 异常: {e}")


if __name__ == "__main__":
    # 手动测试：从 Supabase 取最新预测，走一遍流程
    from supabase_store import load_predictions
    _, all_preds, _ = load_predictions()
    print(f"加载预测: {len(all_preds)} 只")
    run_daily_backtest(all_preds, horizon=30)
