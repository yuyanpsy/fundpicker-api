# FundPicker AI 后端

基金趋势预测模型训练和API服务。

## 快速开始

### 1. 安装依赖

```bash
pip3 install -r requirements.txt
```

### 2. 采集数据

```bash
cd backend
python3 app/data_collector.py
```

这会从东方财富获取排行前50只基金的3年净值数据，保存到 `data/nav/` 目录。

### 3. 训练模型

```bash
python3 app/model_trainer.py
```

训练3个预测周期（7天/30天/90天）的LightGBM + XGBoost模型。
使用Walk-Forward 5折交叉验证，输出AUC和准确率。
模型保存到 `models/` 目录。

### 4. 启动API服务

```bash
python3 app/api_server.py
```

服务启动在 `http://localhost:8000`，API文档在 `http://localhost:8000/docs`。

### 5. API接口

| 接口 | 说明 |
|------|------|
| `GET /predict/{fund_code}?horizon=30` | 预测单只基金 |
| `GET /predict/{fund_code}/all` | 预测所有周期 |
| `GET /batch/predict?codes=004320,005698&horizon=30` | 批量预测 |
| `GET /models/info` | 模型信息和指标 |

### 返回示例

```json
{
  "fund_code": "004320",
  "horizon": 30,
  "probability": 72.3,
  "confidence": 4,
  "model_scores": {
    "lightgbm": 74.1,
    "xgboost": 70.5,
    "ensemble": 72.3
  },
  "factors": [
    {"name": "短期动量(5日)", "value": "+2.3%", "direction": "up"},
    {"name": "RSI(14)", "value": "58", "direction": "neutral"},
    {"name": "MACD", "value": "金叉", "direction": "up"}
  ]
}
```

## 技术架构

```
特征工程 (35+个技术指标)
    ↓
LightGBM + XGBoost (Walk-Forward验证)
    ↓
集成预测 (简单平均)
    ↓
FastAPI (REST API)
    ↓
Android客户端调用
```

## 特征列表 (35+个)

- 收益率: 1/5/10/20/60日收益率
- 均线: MA5/10/20/60, 偏离度, 交叉信号
- 波动率: 5/10/20/60日波动率
- 动量: 5/10/20/60日动量, 动量加速度
- RSI: 6/14/28日RSI
- MACD: DIF, DEA, 柱状图
- 布林带: 上轨/下轨/宽度/位置
- 回撤: 20/60日最大回撤
- 趋势一致性: 短中长期方向一致度
