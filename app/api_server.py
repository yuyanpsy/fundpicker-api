"""
FundPicker AI API v3
- /trigger-update: 触发后台全量预测（异步）
- /top10: 获取预测概率最高的TOP10基金
- /predict/{code}: 实时预测单只基金
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import os, json, time, threading
from datetime import datetime

from model_trainer import FundPredictor
from data_collector import (load_nav_data, fetch_fund_nav_from_pingzhongdata,
                            save_single_nav, fetch_fund_rank, DATA_DIR)
from feature_engineering import compute_features
from supabase_store import save_predictions, load_predictions
from backtest import run_daily_backtest

app = FastAPI(title="FundPicker AI API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 加载模型
predictors = {}
for h in [7, 30, 90]:
    p = FundPredictor(horizon=h)
    try:
        p.load(f"model_{h}d")
        predictors[h] = p
        print(f"已加载 {h}天模型")
    except:
        print(f"未找到 {h}天模型")

# 全量预测缓存
prediction_cache = {
    "top10": [],
    "all_predictions": {},
    "last_update": None,
    "status": "idle",
    "progress": 0,
    "total": 0
}
cache_lock = threading.Lock()

# 启动时从Supabase加载上次的预测结果
_top10, _all_preds, _updated = load_predictions()
if _top10:
    prediction_cache["top10"] = _top10
    prediction_cache["all_predictions"] = _all_preds
    prediction_cache["last_update"] = _updated
    prediction_cache["status"] = "done"
    print(f"从Supabase恢复: {len(_top10)}只TOP10, {len(_all_preds)}只全量")


def ensure_fund_data(code: str) -> bool:
    df = load_nav_data(code)
    if df is None or len(df) < 60:
        df = fetch_fund_nav_from_pingzhongdata(code)
        if df is not None and len(df) > 60:
            save_single_nav(code, df)
            return True
        return False
    return True


def background_batch_predict():
    """后台批量预测（在线程中运行）— 预测10000只基金"""
    global prediction_cache
    with cache_lock:
        if prediction_cache["status"] == "running":
            return  # 已在运行
        prediction_cache["status"] = "running"
        prediction_cache["progress"] = 0

    try:
        # 获取多种类型基金，总计约10000只
        all_codes = []
        code_names = {}  # code -> name 映射

        for fund_type, size in [("all", 3000), ("gp", 3000), ("hh", 2000), ("zs", 2000)]:
            try:
                rank_df = fetch_fund_rank(fund_type, size)
                if rank_df is not None and len(rank_df) > 0:
                    for _, row in rank_df.iterrows():
                        code = row["code"]
                        if code not in code_names:
                            all_codes.append(code)
                            code_names[code] = row.get("name", code)
                    print(f"获取{fund_type}类型: {len(rank_df)}只, 累计去重: {len(all_codes)}只")
            except Exception as e:
                print(f"获取{fund_type}排行失败: {e}")
            time.sleep(1)

        if not all_codes:
            with cache_lock:
                prediction_cache["status"] = "idle"
            return

        # 限制最多10000只
        all_codes = all_codes[:10000]
        with cache_lock:
            prediction_cache["total"] = len(all_codes)

        predictor = predictors.get(30)
        if not predictor:
            with cache_lock:
                prediction_cache["status"] = "idle"
            return

        # 加载已有预测结果（增量更新）
        results = dict(prediction_cache.get("all_predictions", {}))
        print(f"开始批量预测: {len(all_codes)}只基金 (已有{len(results)}只)")

        for i, code in enumerate(all_codes):
            # 跳过已预测的（24小时内）
            if code in results and prediction_cache.get("last_update"):
                try:
                    last = datetime.fromisoformat(prediction_cache["last_update"])
                    if (datetime.now() - last).total_seconds() < 72000:  # 20小时内
                        with cache_lock:
                            prediction_cache["progress"] = i + 1
                        continue
                except:
                    pass

            try:
                df = load_nav_data(code)
                if df is None or len(df) < 60:
                    df = fetch_fund_nav_from_pingzhongdata(code)
                    if df is not None and len(df) > 60:
                        save_single_nav(code, df)
                    else:
                        continue

                pred = predictor.predict(code)
                if "error" not in pred:
                    # 顺手记录当时的净值，回测 snapshot 直接用，避免二次网络查询
                    latest_nav = 0.0
                    try:
                        if df is not None and len(df) > 0:
                            latest_nav = float(df.sort_values("date").iloc[-1]["nav"])
                    except Exception:
                        pass
                    results[code] = {
                        "name": code_names.get(code, code),
                        "probability": pred["probability"],
                        "confidence": pred["confidence"],
                        "factors": pred["factors"][:3],
                        "nav_at_predict": latest_nav
                    }
            except Exception as e:
                pass

            with cache_lock:
                prediction_cache["progress"] = i + 1
                # 每100只更新一次TOP10和保存
                if (i + 1) % 100 == 0 or i == len(all_codes) - 1:
                    top10 = sorted(results.items(),
                                   key=lambda x: x[1]["probability"], reverse=True)[:10]
                    prediction_cache["top10"] = [
                        {"code": c, **v} for c, v in top10
                    ]
                    prediction_cache["all_predictions"] = results

                # 每500只持久化一次到Supabase
                if (i + 1) % 500 == 0:
                    try:
                        save_predictions(prediction_cache["top10"], results)
                        # 顺便 snapshot 已预测的基金（增量对账用）
                        from backtest import snapshot_today_predictions
                        snapshot_today_predictions(results, horizon=30)
                        print(f"进度: {i+1}/{len(all_codes)}, 已预测{len(results)}只, 已保存+快照")
                    except Exception as e:
                        print(f"保存失败: {e}")

            time.sleep(0.2)  # 限流

        with cache_lock:
            prediction_cache["status"] = "done"
            prediction_cache["last_update"] = datetime.now().isoformat()
            print(f"批量预测完成: {len(results)}/{len(all_codes)} 只基金")

        # 持久化到Supabase
        save_predictions(prediction_cache["top10"], results)

        # 触发每日回测流程：存快照 + 对账 30 天前的预测 + 聚合胜率
        try:
            print("[backtest] 启动每日回测流程...")
            run_daily_backtest(results, horizon=30)
        except Exception as e:
            print(f"[backtest] 每日回测失败: {e}")

    except Exception as e:
        print(f"批量预测失败: {e}")
        with cache_lock:
            prediction_cache["status"] = "idle"


@app.get("/")
def root():
    return {
        "service": "FundPicker AI API v3 (10000基金)",
        "models": list(predictors.keys()),
        "cache_status": prediction_cache["status"],
        "cache_progress": f"{prediction_cache['progress']}/{prediction_cache['total']}",
        "predicted_count": len(prediction_cache.get("all_predictions", {})),
        "last_update": prediction_cache["last_update"]
    }


@app.on_event("startup")
def startup_event():
    """服务启动时自动开始批量预测"""
    print("服务启动，自动开始批量预测...")
    thread = threading.Thread(target=background_batch_predict, daemon=True)
    thread.start()


@app.get("/health")
def health():
    return {"status": "ok", "models": len(predictors)}


@app.get("/trigger-update")
def trigger_update(background_tasks: BackgroundTasks):
    """触发后台全量预测（10000只基金）"""
    if prediction_cache["status"] == "running":
        return {
            "status": "running",
            "progress": prediction_cache["progress"],
            "total": prediction_cache["total"],
            "predicted": len(prediction_cache.get("all_predictions", {}))
        }

    # 启动后台任务
    thread = threading.Thread(target=background_batch_predict, daemon=True)
    thread.start()
    return {"status": "started", "message": "开始批量预测10000只基金"}


@app.get("/top10")
def get_top10():
    """获取TOP10预测结果"""
    return {
        "top10": prediction_cache["top10"],
        "status": prediction_cache["status"],
        "progress": prediction_cache["progress"],
        "total": prediction_cache["total"],
        "last_update": prediction_cache["last_update"]
    }


@app.get("/predict/{fund_code}")
def predict(fund_code: str, horizon: int = 30):
    """实时预测单只基金"""
    if horizon not in predictors:
        raise HTTPException(400, f"可选周期: {list(predictors.keys())}")

    # 先查缓存
    cached = prediction_cache["all_predictions"].get(fund_code)
    if cached:
        return {"fund_code": fund_code, "horizon": horizon,
                "probability": cached["probability"],
                "confidence": cached["confidence"],
                "factors": cached.get("factors", []),
                "source": "cache"}

    # 实时预测
    if not ensure_fund_data(fund_code):
        raise HTTPException(404, f"无法获取基金 {fund_code} 的数据")

    result = predictors[horizon].predict(fund_code)
    if "error" in result:
        raise HTTPException(500, result["error"])
    result["source"] = "realtime"
    return result


@app.get("/predict/{fund_code}/all")
def predict_all(fund_code: str):
    if not ensure_fund_data(fund_code):
        raise HTTPException(404, f"无法获取基金 {fund_code} 的数据")
    results = {}
    for h, p in predictors.items():
        try:
            results[f"{h}d"] = p.predict(fund_code)
        except Exception as e:
            results[f"{h}d"] = {"error": str(e)}
    return results


@app.get("/backtest")
def get_backtest(horizon: int = 30):
    """
    获取分档位回测胜率（前端详情页展示用）
    返回：各档位的样本数 + 实际胜率 + 平均实际涨跌幅
    """
    try:
        import requests as _requests
        from supabase_store import SUPABASE_URL, SUPABASE_KEY
        resp = _requests.get(
            f"{SUPABASE_URL}/rest/v1/fund_prediction_backtest"
            f"?horizon_days=eq.{horizon}&order=bucket_min.asc",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10
        )
        if resp.status_code == 200:
            return {"horizon": horizon, "buckets": resp.json()}
        return {"horizon": horizon, "buckets": [], "error": f"supabase {resp.status_code}"}
    except Exception as e:
        return {"horizon": horizon, "buckets": [], "error": str(e)}


@app.post("/run-backtest")
def run_backtest_now():
    """手动触发一次回测流程（不等 cronjob）"""
    preds = prediction_cache.get("all_predictions", {})
    if not preds:
        return {"status": "no_predictions"}
    thread = threading.Thread(
        target=lambda: run_daily_backtest(preds, horizon=30),
        daemon=True
    )
    thread.start()
    return {"status": "started", "count": len(preds)}


@app.post("/daily-snapshot")
@app.get("/daily-snapshot")
def daily_snapshot_endpoint():
    """
    外部 cron 调用：从 Supabase 读预测 + 东方财富拉 nav，批量写快照
    独立于批量预测任务，不依赖 web 服务的缓存状态
    立即返回，后台异步执行
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from daily_snapshot import main as run_snapshot
    thread = threading.Thread(target=lambda: run_snapshot(horizon=30), daemon=True)
    thread.start()
    return {"status": "started", "task": "daily_snapshot"}


@app.post("/daily-verify")
@app.get("/daily-verify")
def daily_verify_endpoint():
    """
    外部 cron 调用：对账 30 天前快照 + 聚合胜率
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from daily_verify import main as run_verify
    thread = threading.Thread(target=run_verify, daemon=True)
    thread.start()
    return {"status": "started", "task": "daily_verify"}


if __name__ == "__main__":
    import uvicorn
    print("FundPicker AI API v3 — 10000只基金预测")
    print("文档: http://localhost:8000/docs")
    # 启动时自动开始批量预测
    thread = threading.Thread(target=background_batch_predict, daemon=True)
    thread.start()
    uvicorn.run(app, host="0.0.0.0", port=8000)
