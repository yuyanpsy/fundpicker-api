# FundPicker AI 后端

基金趋势预测模型训练和API服务。

## 系统架构

```
东方财富排行榜 → 基金代码/涨跌幅/名称
东方财富净值接口 → 历史净值序列
    ↓
特征工程 (35+个技术指标)
    ↓
LightGBM + XGBoost (Walk-Forward验证)
    ↓
集成预测 + 风险指标计算（夏普/回撤/正收益率）
    ↓
Supabase 持久化存储（fund_predictions 表）
    ↓
Android 客户端读取展示
```

## 部署架构（Render + Supabase）

| 组件 | 平台 | 说明 |
|------|------|------|
| Web API | Render Web Service (Free) | FastAPI，提供实时预测和数据查询 |
| 批量预测 Cron | Render Cron Job (Free) | 每5分钟跑500只，持续积累 |
| 每日快照 Cron | Render Cron Job (Free) | 每天17:30(UTC+8)存预测快照 |
| 每日对账 Cron | Render Cron Job (Free) | 每天18:00(UTC+8)验证30天前预测 |
| 数据存储 | Supabase (Free) | fund_predictions / fund_prediction_snapshots / fund_prediction_backtest |

**所有服务自动运行，不依赖本地开发环境。**

## 数据状态（2026-05-13）

| 指标 | 数量 | 覆盖率 |
|------|------|--------|
| 总基金数 | 17,754 | - |
| AI预测概率 | 17,754 | 100% |
| 涨跌幅数据 | 17,227 | 97% |
| 板块归类 | 16,457 | 92% |
| 夏普/回撤/正收益率 | 6,038 | 34%（cron持续补充中，约2小时跑完） |

## 金色基金标准

同时满足以下条件的基金标记为"金色"：
- AI预测上涨概率 ≥ 70%
- 置信度 ≥ 4星
- 夏普比率 > 2（基于成立以来全量历史数据）
- 最大回撤 < 15%
- 持有正收益概率 > 80%

当前符合条件：**177 只**

## 快速开始

### 1. 安装依赖

```bash
pip3 install -r requirements.txt
```

### 2. 采集数据

```bash
python3 app/data_collector.py
```

从东方财富获取基金净值数据，保存到 `data/nav/` 目录。

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

### 5. 补数据脚本（一次性）

```bash
python3 app/backfill_changes.py
```

从东方财富拉全量涨跌幅 + 板块归类，补充到 Supabase 已有预测中。
不需要跑模型，几十秒完成。

## API接口

| 接口 | 说明 |
|------|------|
| `GET /` | 服务状态（预测进度/模型信息） |
| `GET /health` | 健康检查 |
| `GET /top10` | 获取TOP10预测结果 |
| `GET /predict/{fund_code}?horizon=30` | 实时预测单只基金 |
| `GET /predict/{fund_code}/all` | 预测所有周期(7/30/90天) |
| `GET /trigger-update` | 触发后台全量预测 |
| `GET /backtest?horizon=30` | 获取分档位回测胜率 |
| `POST /run-backtest` | 手动触发回测 |
| `GET /daily-snapshot` | 手动触发每日快照 |
| `GET /daily-verify` | 手动触发每日对账 |

## Cron Job 说明

### batch_predict_cron.py（每5分钟）
- 从 Supabase 读取已完成进度（sharpe + year_change + sector 都有才算完成）
- 从东方财富拉全量基金代码（约20000只）
- 每次预测500只新基金：拉净值 → 计算特征 → 模型预测 → 计算风险指标 → 板块归类
- 保存到 Supabase，下次从断点继续

### daily_snapshot.py（每天 UTC 9:30 = 北京 17:30）
- 从 Supabase 读取当天的预测结果
- 记录每只基金的预测概率 + 当时净值
- 写入 fund_prediction_snapshots 表

### daily_verify.py（每天 UTC 10:00 = 北京 18:00）
- 查找30天前的快照记录
- 对比当时净值和现在净值，判断预测是否正确
- 按概率档位聚合胜率，写入 fund_prediction_backtest 表

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

## 变更记录

### 2026-05-13
- 清理无效基金：删除1885只无涨跌幅数据的清盘/非公开基金（19111→17226）
- 补数据脚本 `backfill_changes.py`：一次性从东方财富补充涨跌幅+板块归类到Supabase
- 去掉 `factors` 和 `nav_at_predict` 字段精简存储（前端已不使用，回测快照独立存储）
- Android APK 优化：开启 R8 minify + ABI 过滤(arm64-only)，157MB→54MB
- 风险收益分析卡片：删除底部重复提示文字
- AI预测区域：合并说明文字（数据来源+金色条件+历史回测）到统一区域
