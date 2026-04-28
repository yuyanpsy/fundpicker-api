#!/bin/bash
# FundPicker 模型训练一键脚本

set -e

echo "=== FundPicker AI 模型训练 ==="
echo ""

# 检查Python
python3 --version || { echo "需要Python 3.9+"; exit 1; }

# 安装依赖
echo "1. 安装依赖..."
pip3 install -r requirements.txt

# 采集数据
echo ""
echo "2. 采集基金数据..."
cd app
python3 data_collector.py

# 训练模型
echo ""
echo "3. 训练预测模型..."
python3 model_trainer.py

echo ""
echo "=== 训练完成! ==="
echo "启动API服务: python3 app/api_server.py"
