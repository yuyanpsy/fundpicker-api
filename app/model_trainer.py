"""
模型训练模块
使用Walk-Forward验证训练LightGBM/XGBoost预测模型
"""
import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, roc_auc_score, classification_report
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, ExtraTreesClassifier
import joblib
import os
import json
from datetime import datetime

from feature_engineering import compute_features, prepare_training_data
from data_collector import load_nav_data

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
os.makedirs(MODEL_DIR, exist_ok=True)


class FundPredictor:
    """基金趋势预测器"""

    def __init__(self, horizon: int = 30):
        self.horizon = horizon
        self.gb_model = None   # GradientBoosting (scikit-learn版的XGBoost替代)
        self.rf_model = None   # RandomForest
        self.scaler = StandardScaler()
        self.feature_names = []
        self.metrics = {}

    def train(self, fund_codes: list, min_samples: int = 200):
        """
        用多只基金的数据联合训练
        Walk-Forward交叉验证
        """
        print(f"=== 开始训练 (horizon={self.horizon}天, 基金数={len(fund_codes)}) ===")

        # 1. 合并所有基金的特征数据
        all_X, all_y = [], []
        for code in fund_codes:
            df = load_nav_data(code)
            if df is None:
                continue
            features = compute_features(df)
            X, y, names = prepare_training_data(features, self.horizon)
            if X is not None and len(X) >= 100:
                all_X.append(X)
                all_y.append(y)
                if not self.feature_names:
                    self.feature_names = names

        if not all_X:
            print("没有足够的训练数据!")
            return False

        X = np.vstack(all_X)
        y = np.concatenate(all_y)
        print(f"总样本: {X.shape[0]}, 特征: {X.shape[1]}, 正样本比例: {y.mean():.2%}")

        # 2. 处理NaN和Inf
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        # 3. 标准化
        X_scaled = self.scaler.fit_transform(X)

        # 4. Walk-Forward交叉验证
        tscv = TimeSeriesSplit(n_splits=5)
        gb_scores, rf_scores = [], []

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X_scaled)):
            X_train, X_val = X_scaled[train_idx], X_scaled[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]

            # GradientBoosting (scikit-learn自带，不需要libomp)
            gb_model = GradientBoostingClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.05,
                subsample=0.8, min_samples_leaf=20, random_state=42
            )
            gb_model.fit(X_train, y_train)
            gb_pred = gb_model.predict_proba(X_val)[:, 1]
            gb_auc = roc_auc_score(y_val, gb_pred)
            gb_acc = accuracy_score(y_val, (gb_pred > 0.5).astype(int))
            gb_scores.append({"auc": gb_auc, "acc": gb_acc})

            # RandomForest
            rf_model = RandomForestClassifier(
                n_estimators=300, max_depth=8, min_samples_leaf=20,
                max_features="sqrt", random_state=42, n_jobs=-1
            )
            rf_model.fit(X_train, y_train)
            rf_pred = rf_model.predict_proba(X_val)[:, 1]
            rf_auc = roc_auc_score(y_val, rf_pred)
            rf_acc = accuracy_score(y_val, (rf_pred > 0.5).astype(int))
            rf_scores.append({"auc": rf_auc, "acc": rf_acc})

            print(f"  Fold {fold+1}: GB AUC={gb_auc:.4f} ACC={gb_acc:.2%} | RF AUC={rf_auc:.4f} ACC={rf_acc:.2%}")

        # 5. 用全量数据训练最终模型
        print("\n训练最终模型...")
        self.gb_model = GradientBoostingClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            subsample=0.8, min_samples_leaf=20, random_state=42
        )
        self.gb_model.fit(X_scaled, y)

        self.rf_model = RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=20,
            max_features="sqrt", random_state=42, n_jobs=-1
        )
        self.rf_model.fit(X_scaled, y)

        # 6. 汇总指标
        self.metrics = {
            "horizon": self.horizon,
            "n_samples": int(X.shape[0]),
            "n_features": int(X.shape[1]),
            "n_funds": len(fund_codes),
            "positive_ratio": float(y.mean()),
            "gb_avg_auc": float(np.mean([s["auc"] for s in gb_scores])),
            "gb_avg_acc": float(np.mean([s["acc"] for s in gb_scores])),
            "rf_avg_auc": float(np.mean([s["auc"] for s in rf_scores])),
            "rf_avg_acc": float(np.mean([s["acc"] for s in rf_scores])),
            "trained_at": datetime.now().isoformat(),
            "feature_importance_top10": self._get_feature_importance()
        }

        print(f"\n=== 训练完成 ===")
        print(f"GradientBoosting: AUC={self.metrics['gb_avg_auc']:.4f}, ACC={self.metrics['gb_avg_acc']:.2%}")
        print(f"RandomForest:     AUC={self.metrics['rf_avg_auc']:.4f}, ACC={self.metrics['rf_avg_acc']:.2%}")
        print(f"Top10特征: {[f[0] for f in self.metrics['feature_importance_top10']]}")

        return True

    def _get_feature_importance(self):
        if self.gb_model is None:
            return []
        importance = self.gb_model.feature_importances_
        indices = np.argsort(importance)[::-1][:10]
        return [(self.feature_names[i], float(importance[i])) for i in indices if i < len(self.feature_names)]

    def predict(self, fund_code: str) -> dict:
        """
        预测单只基金未来走势
        返回: {probability, confidence, model_scores, factors}
        """
        if self.gb_model is None:
            return {"error": "模型未训练"}

        df = load_nav_data(fund_code)
        if df is None:
            return {"error": f"无法加载 {fund_code} 的数据"}

        features = compute_features(df)
        exclude_cols = [c for c in features.columns if c.startswith("target_") or c in ["date", "nav", "daily_return", "equityReturn", "unitMoney"]]
        feature_cols = [c for c in features.columns if c not in exclude_cols]

        latest = features.iloc[-1:][feature_cols]
        X = np.nan_to_num(latest.values, nan=0.0, posinf=0.0, neginf=0.0)
        X_scaled = self.scaler.transform(X)

        gb_prob = float(self.gb_model.predict_proba(X_scaled)[0][1])
        rf_prob = float(self.rf_model.predict_proba(X_scaled)[0][1])
        ensemble_prob = gb_prob * 0.6 + rf_prob * 0.4

        agreement = 1 - abs(gb_prob - rf_prob)
        confidence = int(min(5, max(1, agreement * 5)))
        factors = self._extract_factors(features.iloc[-1])

        return {
            "fund_code": fund_code,
            "horizon": self.horizon,
            "probability": round(ensemble_prob * 100, 1),
            "confidence": confidence,
            "model_scores": {
                "gradient_boosting": round(gb_prob * 100, 1),
                "random_forest": round(rf_prob * 100, 1),
                "ensemble": round(ensemble_prob * 100, 1)
            },
            "factors": factors,
            "metrics": self.metrics
        }

    def _extract_factors(self, row) -> list:
        """从特征值提取可解释的因子"""
        factors = []
        # 动量
        mom_5 = row.get("momentum_5d", 0)
        factors.append({
            "name": "短期动量(5日)",
            "value": f"{mom_5*100:.1f}%",
            "direction": "up" if mom_5 > 0.01 else ("down" if mom_5 < -0.01 else "neutral")
        })
        # RSI
        rsi = row.get("rsi_14", 50)
        factors.append({
            "name": "RSI(14)",
            "value": f"{rsi:.0f}",
            "direction": "up" if rsi < 30 else ("down" if rsi > 70 else "neutral")
        })
        # MACD
        macd_hist = row.get("macd_hist", 0)
        factors.append({
            "name": "MACD",
            "value": "金叉" if macd_hist > 0 else "死叉",
            "direction": "up" if macd_hist > 0 else "down"
        })
        # 趋势一致性
        tc = row.get("trend_consistency", 0.5)
        factors.append({
            "name": "趋势一致性",
            "value": f"{tc:.0%}",
            "direction": "up" if tc > 0.6 else ("down" if tc < 0.4 else "neutral")
        })
        # 波动率
        vol = row.get("volatility_20d", 0)
        factors.append({
            "name": "波动率(20日)",
            "value": f"{vol*100:.2f}%",
            "direction": "neutral" if vol < 0.02 else "down"
        })
        # 布林带位置
        boll = row.get("boll_position", 0.5)
        factors.append({
            "name": "布林带位置",
            "value": f"{boll:.0%}",
            "direction": "down" if boll > 0.9 else ("up" if boll < 0.1 else "neutral")
        })
        return factors

    def save(self, name: str = "default"):
        path = os.path.join(MODEL_DIR, name)
        os.makedirs(path, exist_ok=True)
        if self.gb_model:
            joblib.dump(self.gb_model, os.path.join(path, "gb_model.pkl"))
        if self.rf_model:
            joblib.dump(self.rf_model, os.path.join(path, "rf_model.pkl"))
        joblib.dump(self.scaler, os.path.join(path, "scaler.pkl"))
        with open(os.path.join(path, "meta.json"), "w") as f:
            json.dump({"horizon": self.horizon, "feature_names": self.feature_names,
                       "metrics": self.metrics}, f, indent=2, ensure_ascii=False)
        print(f"模型已保存到 {path}")

    def load(self, name: str = "default"):
        path = os.path.join(MODEL_DIR, name)
        gb_path = os.path.join(path, "gb_model.pkl")
        rf_path = os.path.join(path, "rf_model.pkl")
        if os.path.exists(gb_path):
            self.gb_model = joblib.load(gb_path)
        if os.path.exists(rf_path):
            self.rf_model = joblib.load(rf_path)
        scaler_path = os.path.join(path, "scaler.pkl")
        if os.path.exists(scaler_path):
            self.scaler = joblib.load(scaler_path)
        meta_path = os.path.join(path, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
                self.horizon = meta.get("horizon", 30)
                self.feature_names = meta.get("feature_names", [])
                self.metrics = meta.get("metrics", {})
        print(f"模型已加载: horizon={self.horizon}, features={len(self.feature_names)}")


if __name__ == "__main__":
    import sys
    from data_collector import load_nav_data, DATA_DIR

    # 检查是否有数据
    nav_dir = os.path.join(DATA_DIR, "nav")
    if not os.path.exists(nav_dir):
        print("请先运行 data_collector.py 采集数据!")
        print("  python3 app/data_collector.py")
        sys.exit(1)

    fund_codes = [f.replace(".csv", "") for f in os.listdir(nav_dir) if f.endswith(".csv")]
    if len(fund_codes) < 5:
        print(f"数据不足，只有 {len(fund_codes)} 只基金，至少需要5只")
        sys.exit(1)

    print(f"找到 {len(fund_codes)} 只基金的数据")

    # 训练3个周期的模型
    for horizon in [7, 30, 90]:
        print(f"\n{'='*60}")
        predictor = FundPredictor(horizon=horizon)
        success = predictor.train(fund_codes)
        if success:
            predictor.save(f"model_{horizon}d")

            # 测试预测
            test_code = fund_codes[0]
            result = predictor.predict(test_code)
            print(f"\n预测 {test_code} (未来{horizon}天):")
            print(f"  上涨概率: {result['probability']}%")
            print(f"  置信度: {result['confidence']}/5")
            print(f"  GradientBoosting: {result['model_scores']['gradient_boosting']}%")
            print(f"  RandomForest: {result['model_scores']['random_forest']}%")
