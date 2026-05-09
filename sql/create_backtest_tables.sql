-- =====================================================
-- AI 预测回测闭环所需的两张表
-- 执行位置：Supabase SQL Editor
-- =====================================================

-- 表 1：每日预测快照
-- 每次批量预测完成后，把当天所有基金的预测值 + 当时净值存一份
-- 30 天后用来对账
CREATE TABLE IF NOT EXISTS fund_prediction_snapshots (
    snapshot_date DATE NOT NULL,
    fund_code TEXT NOT NULL,
    horizon_days SMALLINT NOT NULL DEFAULT 30,
    probability REAL NOT NULL,
    confidence SMALLINT DEFAULT 3,
    nav_at_predict REAL NOT NULL,          -- 预测当天的净值
    fund_name TEXT,
    -- 对账字段（30 天后由 verify 任务回填）
    actual_nav_after REAL,                  -- 30 天后的净值
    actual_return_pct REAL,                 -- 实际涨跌幅 %
    actual_up BOOLEAN,                      -- 是否真涨
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, fund_code, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_date ON fund_prediction_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_snapshot_verified ON fund_prediction_snapshots(verified_at)
    WHERE verified_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_snapshot_prob ON fund_prediction_snapshots(probability)
    WHERE actual_up IS NOT NULL;

ALTER TABLE fund_prediction_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read snapshots" ON fund_prediction_snapshots FOR SELECT USING (true);
CREATE POLICY "Public write snapshots" ON fund_prediction_snapshots FOR ALL USING (true) WITH CHECK (true);


-- 表 2：分档位胜率聚合（前端展示用）
-- 从 snapshots 聚合而来，定时更新
-- 示例：horizon=30, bucket="70-80", win_rate=0.58, total=1243
CREATE TABLE IF NOT EXISTS fund_prediction_backtest (
    horizon_days SMALLINT NOT NULL,
    bucket TEXT NOT NULL,                   -- "50-60" / "60-70" / "70-80" / "80-90" / "90-100"
    bucket_min SMALLINT NOT NULL,           -- 档位下界，用于前端查询
    bucket_max SMALLINT NOT NULL,
    total_count INTEGER NOT NULL DEFAULT 0,
    win_count INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0,       -- win_count / total_count
    avg_actual_return REAL,                 -- 该档位平均实际涨跌幅
    sample_start_date DATE,                 -- 统计区间起始
    sample_end_date DATE,                   -- 统计区间结束
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (horizon_days, bucket)
);

ALTER TABLE fund_prediction_backtest ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read backtest" ON fund_prediction_backtest FOR SELECT USING (true);
CREATE POLICY "Public write backtest" ON fund_prediction_backtest FOR ALL USING (true) WITH CHECK (true);


-- 初始占位数据（让前端即使没数据也能拿到结构，显示"样本不足"）
INSERT INTO fund_prediction_backtest
    (horizon_days, bucket, bucket_min, bucket_max, total_count, win_count, win_rate)
VALUES
    (30, '50-60', 50, 60, 0, 0, 0),
    (30, '60-70', 60, 70, 0, 0, 0),
    (30, '70-80', 70, 80, 0, 0, 0),
    (30, '80-90', 80, 90, 0, 0, 0),
    (30, '90-100', 90, 100, 0, 0, 0)
ON CONFLICT DO NOTHING;
