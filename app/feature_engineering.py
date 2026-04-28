"""
特征工程模块
从基金净值数据中提取技术指标和统计特征
"""
import pandas as pd
import numpy as np
try:
    import ta
    HAS_TA = True
except ImportError:
    HAS_TA = False
    print("Warning: ta library not installed, using basic indicators only")


def compute_features(df: pd.DataFrame, window_sizes=[5, 10, 20, 60]) -> pd.DataFrame:
    """
    从净值序列计算全部特征
    输入: DataFrame with columns [date, nav]
    输出: DataFrame with all features + target
    """
    df = df.copy()
    df = df.sort_values("date").reset_index(drop=True)
    nav = df["nav"].astype(float)

    # ==================== 收益率特征 ====================
    for w in window_sizes:
        df[f"return_{w}d"] = nav.pct_change(w)

    # 使用已有的daily_return或自己计算
    if "daily_return" in df.columns:
        df["return_1d"] = df["daily_return"]
    else:
        df["return_1d"] = nav.pct_change(1)
    df["log_return_1d"] = np.log(nav / nav.shift(1))

    # ==================== 均线特征 ====================
    for w in window_sizes:
        ma = nav.rolling(w).mean()
        df[f"ma_{w}"] = ma
        df[f"nav_over_ma_{w}"] = nav / ma - 1  # 偏离度

    # 均线交叉
    df["ma_5_over_20"] = df.get("ma_5", nav.rolling(5).mean()) / df.get("ma_20", nav.rolling(20).mean()) - 1
    df["ma_10_over_60"] = df.get("ma_10", nav.rolling(10).mean()) / df.get("ma_60", nav.rolling(60).mean()) - 1

    # ==================== 波动率特征 ====================
    for w in [5, 10, 20, 60]:
        df[f"volatility_{w}d"] = df["return_1d"].rolling(w).std()

    # ==================== 动量特征 ====================
    for w in [5, 10, 20, 60]:
        df[f"momentum_{w}d"] = nav / nav.shift(w) - 1

    # 动量加速度
    df["momentum_accel"] = df.get("momentum_5d", pd.Series(0)) - df.get("momentum_5d", pd.Series(0)).shift(5)

    # ==================== RSI ====================
    for period in [6, 14, 28]:
        delta = nav.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        df[f"rsi_{period}"] = 100 - (100 / (1 + rs))

    # ==================== MACD ====================
    ema12 = nav.ewm(span=12).mean()
    ema26 = nav.ewm(span=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ==================== 布林带 ====================
    ma20 = nav.rolling(20).mean()
    std20 = nav.rolling(20).std()
    df["boll_upper"] = ma20 + 2 * std20
    df["boll_lower"] = ma20 - 2 * std20
    df["boll_width"] = (df["boll_upper"] - df["boll_lower"]) / ma20
    df["boll_position"] = (nav - df["boll_lower"]) / (df["boll_upper"] - df["boll_lower"])

    # ==================== 最大回撤 ====================
    for w in [20, 60]:
        rolling_max = nav.rolling(w).max()
        df[f"drawdown_{w}d"] = (nav - rolling_max) / rolling_max

    # ==================== 趋势一致性 ====================
    df["trend_consistency"] = (
        (df.get("return_5d", pd.Series(0)) > 0).astype(int) +
        (df.get("return_20d", pd.Series(0)) > 0).astype(int) +
        (df.get("return_60d", pd.Series(0)) > 0).astype(int)
    ) / 3.0

    # ==================== 目标变量 ====================
    # 未来N天收益率（用于训练）
    for horizon in [7, 30, 90]:
        future_return = nav.shift(-horizon) / nav - 1
        df[f"target_{horizon}d_return"] = future_return
        df[f"target_{horizon}d_up"] = (future_return > 0).astype(int)  # 二分类标签

    return df


def prepare_training_data(df: pd.DataFrame, horizon: int = 30):
    """
    准备训练数据
    horizon: 预测未来多少天
    返回: X (特征矩阵), y (标签), feature_names
    """
    target_col = f"target_{horizon}d_up"
    if target_col not in df.columns:
        raise ValueError(f"Target column {target_col} not found")

    # 排除目标列和日期列
    exclude_cols = [c for c in df.columns if c.startswith("target_") or c in ["date", "nav", "daily_return", "equityReturn", "unitMoney"]]
    feature_cols = [c for c in df.columns if c not in exclude_cols]

    # 去掉NaN行
    valid = df.dropna(subset=feature_cols + [target_col])
    if len(valid) < 100:
        return None, None, None

    X = valid[feature_cols].values
    y = valid[target_col].values
    return X, y, feature_cols


if __name__ == "__main__":
    # 测试
    dates = pd.date_range("2021-01-01", periods=1000, freq="B")
    np.random.seed(42)
    nav = 1.0 + np.cumsum(np.random.randn(1000) * 0.01)
    nav = np.maximum(nav, 0.1)
    df = pd.DataFrame({"date": dates[:len(nav)], "nav": nav})

    features = compute_features(df)
    print(f"特征数量: {len([c for c in features.columns if not c.startswith('target_') and c not in ['date','nav']])}")
    print(f"样本数量: {len(features.dropna())}")
    print(f"\n特征列表:")
    for col in sorted(features.columns):
        if not col.startswith("target_") and col not in ["date", "nav", "equityReturn", "unitMoney"]:
            print(f"  {col}")

    X, y, names = prepare_training_data(features, horizon=30)
    if X is not None:
        print(f"\n训练数据: X={X.shape}, y={y.shape}, 正样本比例={y.mean():.2%}")
