# 云端部署指南

## 当前方案：Render + GitHub Actions + Supabase（总费用 $0/月）

### 架构概览

```
┌──────────────────────────────────┐
│   Render Free (Web Service $0)  │
│   fundpicker-api FastAPI         │
│   /top10 /predict /health 等 API │
└──────────────┬───────────────────┘
               │ 读写
               ▼
┌──────────────────────────────────┐
│     Supabase (Free Tier $0)      │
│  fund_predictions               │
│  fund_prediction_snapshots      │
│  fund_prediction_backtests      │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│  GitHub Actions (免费，$0/月)    │
│  ┌───────────┬──────────┬─────┐ │
│  │batch-predict│snapshot │verify│ │
│  │ 每天9:00   │每天17:30 │18:00│ │
│  └───────────┴──────────┴─────┘ │
│         仅交易日运行(周一至周五)   │
└──────────────────────────────────┘
```

### 各组件说明

| 组件 | 用途 | 费用 | 备注 |
|------|------|------|------|
| Render Web Service | 提供 REST API | $0/月 | Hobby Free 计划，15分钟无流量自动休眠 |
| GitHub Actions | 定时预测/快照/验证 | $0/月 | 2000分钟/月免费额度，实际使用~1050分钟 |
| Supabase | 数据持久化存储 | $0/月 | Free Tier，500MB 存储 |

### 一、Render Web Service（已有，保留）

API 地址：`https://fundpicker-api.onrender.com`

- **实例类型**：Free
- **休眠机制**：15分钟无流量自动休眠，请求时自动唤醒（约30-60秒延迟）
- **限制**：512MB RAM，可能被随机重启，本地文件系统临时性
- **自动部署**：连接 GitHub 仓库，push 后自动构建部署

### 二、GitHub Actions（新增，替代 Render Cron Jobs）

#### 2.1 配置 GitHub Secrets

在 GitHub 仓库 → **Settings** → **Secrets and variables** → **Actions** 中添加：

| Secret 名称 | 值 |
|-------------|---|
| `SUPABASE_URL` | `https://edzsmjegnkrbedqpotgu.supabase.co` |
| `SUPABASE_KEY` | `eyJhbGciOiJIUzI1NiIs...`（完整密钥） |

#### 2.2 三个 Workflow 说明

| Workflow 文件 | 触发时间 | 功能 | 预估时长 |
|--------------|---------|------|----------|
| `.github/workflows/batch-predict.yml` | 每天 UTC 1:00（北京9:00） | 分批预测基金涨跌幅 | ~20分钟 |
| `.github/workflows/daily-snapshot.yml` | 每天 UTC 9:30（北京17:30） | 拉取最新净值做快照 | ~10分钟 |
| `.github/workflows/daily-verify.yml` | 每天 UTC 10:00（北京18:00） | 验证30天前预测准确率 | ~5分钟 |

- 仅**周一至周五**运行（跳过周末）
- 所有 workflow 均支持 `workflow_dispatch`（手动触发），可在 GitHub Actions 页面手动运行测试

#### 2.3 依赖安装

Cron 任务使用轻量依赖文件 `requirements-cron.txt`，包含：
- pandas, numpy, scikit-learn, joblib, requests, ta

### 三、Supabase（已有，保留）

- Free Tier：500MB 存储，5GB 带宽/月
- 表结构：`fund_predictions`、`fund_prediction_snapshots`、`fund_prediction_backtests`

### 四、环境变量说明

代码通过 `os.environ.get()` 读取 Supabase 配置，支持降级到默认值：

```python
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://edzsmjegnkrbedqpotgu.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIs...")
```

| 环境 | 配置方式 |
|------|---------|
| GitHub Actions | 通过 Secrets 注入 |
| Render Web Service | 通过 Render Dashboard → Environment 注入 |
| 本地开发 | 使用默认值（硬编码），或创建 `.env` 文件 |

### 五、配置验证与测试

#### 5.1 本地代码验证（已完成 ✅）

| 测试项 | 结果 | 说明 |
|--------|------|------|
| `daily_verify.py` 导入 | ✅ 通过 | 语法正确，模块依赖正常 |
| `daily_snapshot.py` 导入 | ✅ 通过 | 语法正确，模块依赖正常 |
| `batch_predict_cron.py` 导入 | ✅ 通过 | 模型加载正常，sklearn 兼容 |
| `requirements-cron.txt` 安装 | ✅ 通过 | pandas/numpy/scikit-learn/joblib/requests/ta 全部安装成功 |
| 环境变量读取 | ✅ 通过 | `os.environ.get()` 降级到默认值逻辑正确 |
| 代码推送 GitHub | ✅ 通过 | commit `4e2e8e8` 已推送至 `main` 分支 |

#### 5.2 GitHub Actions 首次运行前配置

**必须完成以下步骤，否则 workflow 会因缺少 Secrets 而失败：**

1. 打开仓库页面：`https://github.com/yuyanpsy/fundpicker-api`
2. 点击 **Settings** → 左侧 **Secrets and variables** → **Actions**
3. 点击 **New repository secret**，依次添加：

| Secret 名称 | 值（完整字符串，勿加引号） |
|-------------|---------------------------|
| `SUPABASE_URL` | `https://edzsmjegnkrbedqpotgu.supabase.co` |
| `SUPABASE_KEY` | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkenNtamVnbmtyYmVkcXBvdGd1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYzMDA5NDcsImV4cCI6MjA5MTg3Njk0N30.J1gHxRiRgEBSMtd3WwhmkwiO2bIpNJy2LDsphD0SPQU` |

#### 5.3 手动触发首次测试（已完成 ✅）

三个 workflow 已于 2026-07-05 全部手动触发测试通过：

| Workflow | Run # | 状态 | 耗时 |
|----------|-------|------|------|
| Daily Verify | #1 | ✅ Success | 37s |
| Daily Snapshot | #1 | ✅ Success | 28s |
| Batch Predict Funds | #1 | ✅ Success | 36s |

> 注：三个 workflow 均有 Node.js 20 弃用警告（非阻断），后续可升级 `actions/checkout@v4` → `v5` 消除。

定时任务（cron schedule）已生效，将按 schedule 自动运行。

### 六、监控与运维

#### 查看运行状态

1. **GitHub Actions**：仓库 → Actions 标签页，查看三个 workflow 的运行历史和日志
2. **Render Dashboard**：https://dashboard.render.com/ 查看 Web Service 状态
3. **Supabase Dashboard**：https://supabase.com/dashboard 查看数据表和存储用量

#### 手动触发

在 GitHub 仓库 → Actions → 选择对应 workflow → **Run workflow** 按钮手动触发

#### 月度使用量预估

| 资源 | 免费额度 | 预估用量 | 是否够用 |
|------|---------|---------|---------|
| GitHub Actions 分钟数 | 2000分钟/月 | ~1050分钟 | 充足 |
| Render Free Instance Hours | 750小时/月 | ~100小时 | 充足 |
| Render 出站带宽 | 5GB/月 | ~1GB | 充足 |
| Supabase 存储 | 500MB | ~100MB | 充足 |

---

## 备选方案：腾讯云/阿里云轻量服务器

如需自建服务器，可参考以下步骤（费用约50-80元/月）：

### 1. 购买服务器
- 推荐：腾讯云轻量应用服务器 2核4G
- 系统：Ubuntu 22.04，带宽 5Mbps

### 2. 服务器初始化
```bash
ssh root@你的服务器IP
curl -fsSL https://get.docker.com | sh
systemctl enable docker
apt install docker-compose-plugin
```

### 3. 上传代码和模型
```bash
cd ~/AIProjects/fundpicker-api
tar czf fundpicker.tar.gz app/ models/ data/ requirements.txt Dockerfile docker-compose.yml
scp fundpicker.tar.gz root@你的服务器IP:/opt/
```

### 4. 在服务器上部署
```bash
ssh root@你的服务器IP
cd /opt && tar xzf fundpicker.tar.gz && cd backend
docker compose up -d --build
```

### 5. 定时任务（替代 GitHub Actions）
```bash
crontab -e
# 每天9:00批量预测
0 9 * * 1-5 cd /opt/backend && docker compose exec fundpicker-api python3 app/batch_predict_cron.py
# 每天17:30快照
30 17 * * 1-5 cd /opt/backend && docker compose exec fundpicker-api python3 app/daily_snapshot.py
# 每天18:00验证
0 18 * * 1-5 cd /opt/backend && docker compose exec fundpicker-api python3 app/daily_verify.py
```
