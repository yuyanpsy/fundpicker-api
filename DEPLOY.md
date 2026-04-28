# 云端部署指南

## 方案：腾讯云/阿里云轻量服务器

### 1. 购买服务器

推荐配置：
- 腾讯云轻量应用服务器 2核4G（约50-80元/月）
- 系统：Ubuntu 22.04
- 带宽：5Mbps

### 2. 服务器初始化

```bash
# SSH登录服务器
ssh root@你的服务器IP

# 安装Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker

# 安装Docker Compose
apt install docker-compose-plugin
```

### 3. 上传代码和模型

在你的Mac上执行：
```bash
# 打包后端代码+模型+数据
cd ~/FundPicker/backend
tar czf fundpicker-backend.tar.gz app/ models/ data/ requirements.txt Dockerfile docker-compose.yml

# 上传到服务器
scp fundpicker-backend.tar.gz root@你的服务器IP:/opt/
```

### 4. 在服务器上部署

```bash
ssh root@你的服务器IP
cd /opt
tar xzf fundpicker-backend.tar.gz
cd backend

# 启动服务
docker compose up -d --build

# 查看日志
docker compose logs -f
```

### 5. 验证

```bash
curl http://你的服务器IP:8000/health
curl http://你的服务器IP:8000/predict/004320?horizon=30
```

### 6. 配置Android端

修改 FundRepository.kt 中的 API_BASE_URL：
```kotlin
private const val AI_API_URL = "http://你的服务器IP:8000"
```

### 7. 定时更新（可选）

添加crontab每天收盘后自动更新数据和预测：
```bash
crontab -e
# 每天16:30自动采集+预测
30 16 * * 1-5 cd /opt/backend && docker compose exec fundpicker-api python3 app/batch_predict.py
```
