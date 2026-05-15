"""
Supabase 持久化存储
预测结果存到Supabase，Render重启后自动恢复
"""
import requests
import json

SUPABASE_URL = "https://edzsmjegnkrbedqpotgu.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkenNtamVnbmtyYmVkcXBvdGd1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYzMDA5NDcsImV4cCI6MjA5MTg3Njk0N30.J1gHxRiRgEBSMtd3WwhmkwiO2bIpNJy2LDsphD0SPQU"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation,resolution=merge-duplicates"
}


def save_predictions(top10: list, all_predictions: dict):
    """保存预测结果到Supabase"""
    try:
        from datetime import datetime
        data = [{
            "id": "latest",
            "top10": top10,
            "all_predictions": all_predictions,
            "updated_at": datetime.utcnow().isoformat() + "+00:00"
        }]
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/fund_predictions?on_conflict=id",
            headers=HEADERS,
            json=data,
            timeout=30
        )
        print(f"Supabase保存: {resp.status_code}, top10={len(top10)}只, all={len(all_predictions)}只")
        return resp.status_code < 300
    except Exception as e:
        print(f"Supabase保存失败: {e}")
        return False


def load_predictions():
    """从Supabase加载预测结果"""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/fund_predictions?id=eq.latest&select=*",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                row = data[0]
                top10 = row.get("top10", [])
                all_preds = row.get("all_predictions", {})
                updated = row.get("updated_at", "")
                print(f"Supabase加载: top10={len(top10)}只, all={len(all_preds)}只, updated={updated}")
                return top10, all_preds, updated
    except Exception as e:
        print(f"Supabase加载失败: {e}")
    return [], {}, None
