"""
独立快照脚本：从 Supabase 读已有的 all_predictions + 实时拉 nav
直接写入 fund_prediction_snapshots 表

作为独立 cron 运行，不依赖 Render web 服务的状态
Render 即使 OOM 重启多次也不影响本脚本
"""
import os
import requests
import time
from datetime import date, datetime
from typing import Optional

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://edzsmjegnkrbedqpotgu.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkenNtamVnbmtyYmVkcXBvdGd1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYzMDA5NDcsImV4cCI6MjA5MTg3Njk0N30.J1gHxRiRgEBSMtd3WwhmkwiO2bIpNJy2LDsphD0SPQU")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}


def fetch_latest_nav(fund_code: str) -> Optional[float]:
    """
    直接从东方财富拉最新净值
    接口：https://fundgz.1234567.com.cn/js/{code}.js
    返回 dwjz (单位净值)
    """
    try:
        url = f"https://fundgz.1234567.com.cn/js/{fund_code}.js?v={int(time.time()*1000)}"
        resp = requests.get(
            url,
            headers={"Referer": "https://fund.eastmoney.com/"},
            timeout=6
        )
        if resp.status_code != 200:
            return None
        body = resp.text
        # 格式: jsonpgz({"fundcode":"xxx","dwjz":"1.2345",...});
        start = body.find("(")
        end = body.rfind(")")
        if start < 0 or end < 0:
            return None
        import json as _json
        data = _json.loads(body[start+1:end])
        nav = data.get("dwjz")
        if nav is None:
            return None
        return float(nav)
    except Exception:
        return None


def load_all_predictions():
    """从 Supabase 读取最新的 all_predictions"""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/fund_predictions?id=eq.latest&select=all_predictions",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=30
        )
        if resp.status_code != 200:
            print(f"加载失败: {resp.status_code}")
            return {}
        data = resp.json()
        if not data:
            return {}
        return data[0].get("all_predictions") or {}
    except Exception as e:
        print(f"加载异常: {e}")
        return {}


def get_existing_snapshot_codes(snapshot_date: str, horizon: int = 30) -> set:
    """获取今天已经写过快照的基金代码，避免重复网络请求"""
    existing = set()
    try:
        # 分页拉
        offset = 0
        while True:
            resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/fund_prediction_snapshots"
                f"?snapshot_date=eq.{snapshot_date}&horizon_days=eq.{horizon}"
                f"&select=fund_code&offset={offset}&limit=1000",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                timeout=15
            )
            if resp.status_code != 200:
                break
            rows = resp.json()
            if not rows:
                break
            for r in rows:
                existing.add(r["fund_code"])
            if len(rows) < 1000:
                break
            offset += 1000
    except Exception as e:
        print(f"读已有快照异常: {e}")
    return existing


def snapshot_batch(rows: list) -> int:
    """批量写入快照，使用 upsert 去重"""
    if not rows:
        return 0
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/fund_prediction_snapshots"
            f"?on_conflict=snapshot_date,fund_code,horizon_days",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates"},
            json=rows,
            timeout=60
        )
        if resp.status_code < 300:
            return len(rows)
        print(f"写入失败: {resp.status_code} {resp.text[:300]}")
        return 0
    except Exception as e:
        print(f"写入异常: {e}")
        return 0


def main(horizon: int = 30):
    print(f"=== 每日快照任务 开始 ===")
    all_preds = load_all_predictions()
    if not all_preds:
        print("无预测数据，退出")
        return

    today = date.today().isoformat()
    print(f"日期: {today}, 预测基金数: {len(all_preds)}")

    # 跳过今天已快照的
    existing = get_existing_snapshot_codes(today, horizon)
    print(f"今日已快照 {len(existing)} 只，跳过")

    pending_codes = [c for c in all_preds.keys() if c not in existing]
    print(f"待处理: {len(pending_codes)} 只")

    if not pending_codes:
        print("全部已快照，结束")
        return

    batch_rows = []
    total_written = 0
    batch_size = 200

    for idx, code in enumerate(pending_codes):
        pred = all_preds[code]
        prob = pred.get("probability")
        if not prob or prob == 0:
            continue

        nav = fetch_latest_nav(code)
        if nav is None or nav <= 0:
            continue

        batch_rows.append({
            "snapshot_date": today,
            "fund_code": code,
            "horizon_days": horizon,
            "probability": float(prob),
            "confidence": int(pred.get("confidence", 3)),
            "nav_at_predict": float(nav),
            "fund_name": pred.get("name", code),
        })

        # 每 200 只写一次
        if len(batch_rows) >= batch_size:
            written = snapshot_batch(batch_rows)
            total_written += written
            print(f"进度 {idx+1}/{len(pending_codes)}, 累计写入 {total_written}")
            batch_rows = []

        # 限流
        time.sleep(0.05)

    # 最后一批
    if batch_rows:
        total_written += snapshot_batch(batch_rows)

    print(f"=== 完成，总写入 {total_written} 条 ===")


if __name__ == "__main__":
    main(horizon=30)
