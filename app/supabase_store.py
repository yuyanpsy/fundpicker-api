"""
Supabase 持久化存储
预测结果存到Supabase，Render重启后自动恢复

优化版（2026-07-05）：减少 egress 流量
- load_top10: 只读 top10（~3KB），用于 API 展示
- load_progress: 只读 all_predictions 的 key 列表（~80KB），用于 batch_predict 断点
- load_predictions: 保留全量读取，仅在必要时调用（~6MB）
- load_prediction_summary: 只读 top10 + 统计摘要（~10KB），用于启动恢复
- save_predictions: 先单独 PATCH top10，再写全量（避免读大 JSON 回传）
"""
import requests
import json

import os
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://edzsmjegnkrbedqpotgu.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkenNtamVnbmtyYmVkcXBvdGd1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYzMDA5NDcsImV4cCI6MjA5MTg3Njk0N30.J1gHxRiRgEBSMtd3WwhmkwiO2bIpNJy2LDsphD0SPQU")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation,resolution=merge-duplicates"
}


def load_top10():
    """轻量读取：只获取 top10 列表（~3KB），用于 API 展示"""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/fund_predictions?id=eq.latest&select=top10,updated_at",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                top10 = data[0].get("top10", [])
                updated = data[0].get("updated_at", "")
                print(f"[轻量] 读取 top10: {len(top10)}只")
                return top10, updated
    except Exception as e:
        print(f"[轻量] 读取 top10 失败: {e}")
    return [], None


def load_progress():
    """轻量读取：只获取已完成预测的基金代码集合（~80KB），用于 batch_predict 断点判断
    返回 {code: True} 表示该基金已完成预测
    """
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/fund_predictions?id=eq.latest&select=predicted_codes",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0 and data[0].get("predicted_codes"):
                codes = data[0]["predicted_codes"]
                print(f"[轻量] 读取进度: {len(codes)} 只已完成")
                return codes  # dict: {code: True}
    except Exception as e:
        print(f"[轻量] 读取进度失败: {e}")
    return {}


def load_predictions():
    """全量读取（~6MB）：仅在 batch_predict 完成一批预测后、daily_snapshot 等需要全量数据时调用"""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/fund_predictions?id=eq.latest&select=*",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                row = data[0]
                top10 = row.get("top10", [])
                all_preds = row.get("all_predictions", {})
                updated = row.get("updated_at", "")
                print(f"[全量] 加载: top10={len(top10)}只, all={len(all_preds)}只, updated={updated}")
                return top10, all_preds, updated
    except Exception as e:
        print(f"[全量] 加载失败: {e}")
    return [], {}, None


def save_predictions(top10: list, all_predictions: dict):
    """保存预测结果到 Supabase
    优化：同时写入 predicted_codes 用于轻量进度读取
    """
    try:
        from datetime import datetime
        # 构建轻量进度索引：只存已完成预测的代码
        predicted_codes = {code: True for code in all_predictions.keys()}
        data = [{
            "id": "latest",
            "top10": top10,
            "all_predictions": all_predictions,
            "predicted_codes": predicted_codes,
            "updated_at": datetime.utcnow().isoformat() + "+00:00"
        }]
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/fund_predictions?on_conflict=id",
            headers=HEADERS,
            json=data,
            timeout=60
        )
        print(f"Supabase保存: {resp.status_code}, top10={len(top10)}只, all={len(all_predictions)}只")
        return resp.status_code < 300
    except Exception as e:
        print(f"Supabase保存失败: {e}")
        return False


def patch_top10(top10: list):
    """轻量更新：只更新 top10 字段（~3KB），用于 batch_predict 中间过程"""
    try:
        resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/fund_predictions?id=eq.latest",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                     "Content-Type": "application/json"},
            json={"top10": top10},
            timeout=15
        )
        if resp.status_code < 300:
            print(f"[轻量] 更新 top10: {len(top10)}只")
        return resp.status_code < 300
    except Exception as e:
        print(f"[轻量] 更新 top10 失败: {e}")
        return False
