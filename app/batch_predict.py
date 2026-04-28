"""
批量预测所有基金，导出JSON供Android端使用
"""
import json
import os
import sys
from datetime import datetime

from model_trainer import FundPredictor
from data_collector import load_nav_data, DATA_DIR

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def batch_predict_all():
    """批量预测所有有数据的基金"""
    nav_dir = os.path.join(DATA_DIR, "nav")
    fund_codes = [f.replace(".csv", "") for f in os.listdir(nav_dir) if f.endswith(".csv")]
    print(f"找到 {len(fund_codes)} 只基金")

    # 加载模型
    predictors = {}
    for horizon in [7, 30, 90]:
        p = FundPredictor(horizon=horizon)
        try:
            p.load(f"model_{horizon}d")
            predictors[horizon] = p
        except Exception as e:
            print(f"加载 {horizon}天模型失败: {e}")

    if not predictors:
        print("没有可用的模型!")
        return

    # 批量预测
    results = {}
    total = len(fund_codes)
    success = 0

    for i, code in enumerate(fund_codes):
        fund_result = {}
        for horizon, predictor in predictors.items():
            try:
                pred = predictor.predict(code)
                if "error" not in pred:
                    fund_result[f"{horizon}d"] = {
                        "probability": pred["probability"],
                        "confidence": pred["confidence"],
                        "gb_score": pred["model_scores"]["gradient_boosting"],
                        "rf_score": pred["model_scores"]["random_forest"],
                        "factors": pred["factors"][:3]  # 只保留前3个因子
                    }
            except:
                pass

        if fund_result:
            results[code] = fund_result
            success += 1

        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{total}, 成功: {success}")

    # 导出JSON
    output = {
        "generated_at": datetime.now().isoformat(),
        "model_info": {
            "7d": predictors.get(7, FundPredictor()).metrics,
            "30d": predictors.get(30, FundPredictor()).metrics,
            "90d": predictors.get(90, FundPredictor()).metrics,
        },
        "predictions": results
    }

    output_path = os.path.join(OUTPUT_DIR, "predictions.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n预测完成: {success}/{total} 只基金")
    print(f"结果已保存到: {output_path}")
    print(f"文件大小: {os.path.getsize(output_path) / 1024:.1f} KB")

    # 打印统计
    probs_30d = [r["30d"]["probability"] for r in results.values() if "30d" in r]
    if probs_30d:
        import numpy as np
        probs = np.array(probs_30d)
        print(f"\n30天预测概率分布:")
        print(f"  均值: {probs.mean():.1f}%")
        print(f"  中位数: {np.median(probs):.1f}%")
        print(f"  最高: {probs.max():.1f}% (基金 {[c for c,r in results.items() if '30d' in r and r['30d']['probability']==probs.max()][0]})")
        print(f"  最低: {probs.min():.1f}%")
        print(f"  >70%: {(probs>70).sum()}只")
        print(f"  >50%: {(probs>50).sum()}只")
        print(f"  <30%: {(probs<30).sum()}只")


if __name__ == "__main__":
    batch_predict_all()
