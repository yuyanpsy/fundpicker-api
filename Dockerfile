FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制代码
COPY app/ ./app/
COPY models/ ./models/ 
COPY data/ ./data/

# 暴露端口
EXPOSE 8000

# 启动API服务
CMD ["python3", "app/api_server.py"]
