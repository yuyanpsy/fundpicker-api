#!/bin/bash
# Render 入口脚本：根据环境变量决定跑 web 还是 batch predict
if [ "$RUN_MODE" = "batch" ]; then
    echo "=== 启动批量预测 ==="
    python3 app/batch_predict_cron.py
elif [ "$RUN_MODE" = "snapshot" ]; then
    echo "=== 启动每日快照 ==="
    python3 app/daily_snapshot.py
elif [ "$RUN_MODE" = "verify" ]; then
    echo "=== 启动每日对账 ==="
    python3 app/daily_verify.py
else
    echo "=== 启动 API 服务 ==="
    cd app && uvicorn api_server:app --host 0.0.0.0 --port ${PORT:-8000}
fi
