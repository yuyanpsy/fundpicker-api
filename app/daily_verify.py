"""
独立对账脚本：
1. 对 30 天前未对账的快照用最新 nav 回填 actual_return / actual_up
2. 按档位聚合胜率写入 fund_prediction_backtest

Render cron 每天调用一次，独立运行
"""
import os
import requests
import time
from datetime import date, datetime, timedelta
from typing import Optional

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://edzsmjegnkrbedqpotgu.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkenNtamVnbmtyYmVkcXBvdGd1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYzMDA5NDcsImV4cCI6MjA5MTg3Njk0N30.J1gHxRiRgEBSMtd3WwhmkwiO2bIpNJy2LDsphD0SPQU")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}


def fetch_latest_nav(fund_code: str) -> Optional[float]:
    try:
        url = f"https://fundgz.1234567.com.cn/js/{fund_code}.js?v={int(time.time()*1000)}"
        resp = requests.get(url, headers={"Referer": "https://fund.eastmoney.com/"}, timeout=6)
        if resp.status_code != 200:
            return None
        body = resp.text
        start = body.find("(")
        end = body.rfind(")")
        if start < 0 or end < 0:
            return None
        import json as _json
        data = _json.loads(body[start+1:end])
        nav = data.get("dwjz")
        return float(nav) if nav else None
    except Exception:
        return None


def verify_old_snapshots(horizon: int = 30, max_batch: int = 2000) -> int:
    cutoff = (date.today() - timedelta(days=horizon)).isoformat()
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/fund_prediction_snapshots"
            f"?snapshot_date=lte.{cutoff}"
            f"&verified_at=is.null"
            f"&horizon_days=eq.{horizon}"
            f"&select=snapshot_date,fund_code,horizon_days,nav_at_predict"
            f"&limit={max_batch}",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=60
        )
        if resp.status_code != 200:
            print(f"[verify] 拉取失败: {resp.status_code}")
            return 0
        pending = resp.json()
    except Exception as e:
        print(f"[verify] 异常: {e}")
        return 0

    if not pending:
        print("[verify] 无待对账快照（30 天数据还没积累够）")
        return 0

    print(f"[verify] 待对账 {len(pending)} 条")
    updated = 0

    for row in pending:
        code = row["fund_code"]
        nav_before = float(row["nav_at_predict"])
        if nav_before <= 0:
            continue
        nav_after = fetch_latest_nav(code)
        if nav_after is None or nav_after <= 0:
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
        time.sleep(0.05)

    print(f"[verify] 对账完成: {updated}/{len(pending)}")
    return updated


def aggregate_win_rates(horizon: int = 30):
    buckets = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]
    for lo, hi in buckets:
        bucket_key = f"{lo}-{hi}"
        try:
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
            print(f"[agg] {bucket_key} 拉取异常: {e}")
            continue

        total = len(rows)
        wins = sum(1 for r in rows if r.get("actual_up"))
        win_rate = wins / total if total > 0 else 0.0
        rets = [r["actual_return_pct"] for r in rows if r.get("actual_return_pct") is not None]
        avg_return = sum(rets) / len(rets) if rets else None
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
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/fund_prediction_backtest?on_conflict=horizon_days,bucket",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates"},
                json=[record],
                timeout=15
            )
            if r.status_code < 300:
                print(f"[agg] {bucket_key}: total={total} wins={wins} rate={win_rate:.2%}")
        except Exception as e:
            print(f"[agg] {bucket_key} 写入异常: {e}")


def main():
    print(f"=== 每日对账 + 胜率聚合 开始 ===")
    verify_old_snapshots(horizon=30)
    aggregate_win_rates(horizon=30)
    print(f"=== 完成 ===")


if __name__ == "__main__":
    main()
