FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制代码和入口脚本
COPY app/ ./app/
COPY models/ ./models/ 
COPY data/ ./data/
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# 暴露端口
EXPOSE 8000

# 通过环境变量 RUN_MODE 控制运行模式
# web服务: 不设置或 RUN_MODE=web
# 批量预测: RUN_MODE=batch
# 每日快照: RUN_MODE=snapshot
# 每日对账: RUN_MODE=verify
ENTRYPOINT ["./entrypoint.sh"]
