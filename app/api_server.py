"""
FastAPI 服务端 — 支持任意基金实时预测
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import json

from model_trainer import FundPredictor
from data_collector import load_nav_data, fetch_fund_nav_from_pingzhongdata, save_single_nav
from feature_engineering import compute_features

app = FastAPI(title="FundPicker AI API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 加载预训练模型
predictors = {}
for horizon in [7, 30, 90]:
    p = FundPredictor(horizon=horizon)
    try:
        p.load(f"model_{horizon}d")
        predictors[horizon] = p
        print(f"已加载 {horizon}天预测模型")
    except:
        print(f"未找到 {horizon}天模型")


def ensure_fund_data(fund_code: str):
    """确保有基金数据，没有就实时获取"""
    df = load_nav_data(fund_code)
    if df is None or len(df) < 60:
        print(f"实时获取 {fund_code} 净值数据...")
        df = fetch_fund_nav_from_pingzhongdata(fund_code)
        if df is not None and len(df) > 60:
            save_single_nav(fund_code, df)
            return True
        return False
    return True


@app.get("/")
def root():
    return {
        "service": "FundPicker AI API v2",
        "models": {f"{h}d": p.metrics.get("gb_avg_auc", 0) for h, p in predictors.items()},
        "features": "支持任意基金代码实时预测",
        "endpoints": ["/predict/{code}", "/predict/{code}/all", "/batch/predict", "/health"]
    }


@app.get("/health")
def health():
    return {"status": "ok", "models": len(predictors)}


@app.get("/models/info")
def models_info():
    return {f"{h}d": p.metrics for h, p in predictors.items()}


@app.get("/predict/{fund_code}")
def predict(fund_code: str, horizon: int = 30):
    """预测任意基金（自动获取数据）"""
    if horizon not in predictors:
        raise HTTPException(400, f"可选周期: {list(predictors.keys())}")

    if not ensure_fund_data(fund_code):
        raise HTTPException(404, f"无法获取基金 {fund_code} 的数据")

    result = predictors[horizon].predict(fund_code)
    if "error" in result:
        raise HTTPException(500, result["error"])
    return result


@app.get("/predict/{fund_code}/all")
def predict_all(fund_code: str):
    """预测所有周期"""
    if not ensure_fund_data(fund_code):
        raise HTTPException(404, f"无法获取基金 {fund_code} 的数据")

    results = {}
    for h, p in predictors.items():
        try:
            results[f"{h}d"] = p.predict(fund_code)
        except Exception as e:
            results[f"{h}d"] = {"error": str(e)}
    return results


@app.get("/batch/predict")
def batch_predict(codes: str, horizon: int = 30):
    """批量预测（逗号分隔）"""
    if horizon not in predictors:
        raise HTTPException(400, f"可选周期: {list(predictors.keys())}")

    fund_codes = [c.strip() for c in codes.split(",") if c.strip()][:50]
    results = {}
    for code in fund_codes:
        try:
            if ensure_fund_data(code):
                results[code] = predictors[horizon].predict(code)
            else:
                results[code] = {"error": "数据获取失败"}
        except Exception as e:
            results[code] = {"error": str(e)}
    return results


if __name__ == "__main__":
    import uvicorn
    print("FundPicker AI API v2")
    print("支持任意基金代码实时预测")
    print("文档: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)
