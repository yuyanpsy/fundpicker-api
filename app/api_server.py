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
    """后台批量预测（在线程中运行）"""
    global prediction_cache
    with cache_lock:
        if prediction_cache["status"] == "running":
            return  # 已在运行
        prediction_cache["status"] = "running"
        prediction_cache["progress"] = 0

    try:
        # 获取排行榜前500只基金
        rank_df = fetch_fund_rank("all", 500)
        if rank_df is None or len(rank_df) == 0:
            with cache_lock:
                prediction_cache["status"] = "idle"
            return

        codes = rank_df["code"].tolist()
        with cache_lock:
            prediction_cache["total"] = len(codes)

        predictor = predictors.get(30)
        if not predictor:
            with cache_lock:
                prediction_cache["status"] = "idle"
            return

        results = {}
        for i, code in enumerate(codes):
            try:
                # 获取数据
                df = load_nav_data(code)
                if df is None or len(df) < 60:
                    df = fetch_fund_nav_from_pingzhongdata(code)
                    if df is not None and len(df) > 60:
                        save_single_nav(code, df)
                    else:
                        continue

                # 预测
                pred = predictor.predict(code)
                if "error" not in pred:
                    # 从排行榜获取基金名称
                    row = rank_df[rank_df["code"] == code]
                    name = row.iloc[0]["name"] if len(row) > 0 else code
                    results[code] = {
                        "name": name,
                        "probability": pred["probability"],
                        "confidence": pred["confidence"],
                        "factors": pred["factors"][:3]
                    }
            except Exception as e:
                pass

            with cache_lock:
                prediction_cache["progress"] = i + 1
                # 每50只更新一次TOP10
                if (i + 1) % 50 == 0 or i == len(codes) - 1:
                    top10 = sorted(results.items(),
                                   key=lambda x: x[1]["probability"], reverse=True)[:10]
                    prediction_cache["top10"] = [
                        {"code": c, **v} for c, v in top10
                    ]
                    prediction_cache["all_predictions"] = results

            time.sleep(0.3)  # 限流

        with cache_lock:
            prediction_cache["status"] = "done"
            prediction_cache["last_update"] = datetime.now().isoformat()
            print(f"批量预测完成: {len(results)}/{len(codes)} 只基金")

        # 持久化到Supabase
        save_predictions(prediction_cache["top10"], results)

    except Exception as e:
        print(f"批量预测失败: {e}")
        with cache_lock:
            prediction_cache["status"] = "idle"


@app.get("/")
def root():
    return {
        "service": "FundPicker AI API v3",
        "models": list(predictors.keys()),
        "cache_status": prediction_cache["status"],
        "cache_progress": f"{prediction_cache['progress']}/{prediction_cache['total']}",
        "last_update": prediction_cache["last_update"]
    }


@app.get("/health")
def health():
    return {"status": "ok", "models": len(predictors)}


@app.get("/trigger-update")
def trigger_update(background_tasks: BackgroundTasks):
    """触发后台全量预测"""
    # 检查是否24小时内已更新
    last = prediction_cache.get("last_update")
    if last:
        try:
            last_time = datetime.fromisoformat(last)
            hours_ago = (datetime.now() - last_time).total_seconds() / 3600
            if hours_ago < 20 and prediction_cache["status"] == "done":
                return {
                    "status": "already_updated",
                    "last_update": last,
                    "top10_count": len(prediction_cache["top10"])
                }
        except:
            pass

    if prediction_cache["status"] == "running":
        return {
            "status": "running",
            "progress": prediction_cache["progress"],
            "total": prediction_cache["total"]
        }

    # 启动后台任务
    thread = threading.Thread(target=background_batch_predict, daemon=True)
    thread.start()
    return {"status": "started", "message": "后台开始全量预测"}


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


if __name__ == "__main__":
    import uvicorn
    print("FundPicker AI API v3")
    print("文档: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)
