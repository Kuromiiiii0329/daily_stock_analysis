#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_kline.py -- 批量拉取 3 年 K 线并重建回测数据

用法：
    cd /path/to/daily
    python refresh_kline.py [stock_code ...]

    不传参数时默认处理 watchlist.json 内所有股票；也可指定单只股票：
    python refresh_kline.py 002466
"""
from __future__ import annotations

import sys
import json
import time
from pathlib import Path

# ── 路径注入（与 portal/server.py 保持一致）──────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
PORTAL_DIR   = PROJECT_ROOT / "portal"
LIB_DIR      = PORTAL_DIR / "lib"
sys.path.insert(0, str(PORTAL_DIR))
sys.path.insert(0, str(LIB_DIR))

WATCHLIST = PROJECT_ROOT / "config" / "watchlist.json"
DAYS = 750  # ≈3 年交易日

# ── 确定待处理股票列表 ────────────────────────────────────────────────────────
if len(sys.argv) > 1:
    STOCKS = list(sys.argv[1:])
else:
    try:
        data = json.loads(WATCHLIST.read_text(encoding="utf-8"))
        raw = data.get("stock_list", [])
        # 支持两种格式：字符串列表 ["002466"] 或对象列表 [{"code": "002466"}]
        STOCKS = [s if isinstance(s, str) else s["code"] for s in raw]
    except Exception as e:
        print(f"读取 watchlist.json 失败：{e}")
        sys.exit(1)

if not STOCKS:
    print("没有找到待处理的股票，退出")
    sys.exit(1)

print(f"待处理股票：{STOCKS}，拉取天数={DAYS}")


def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def fetch_and_backtest(code: str):
    log(f"═══ 开始处理 {code} ════════════════")

    from portal.data_cache import StockDataCache
    from data_provider import DataFetcherManager
    from data_provider.akshare_fetcher import AkshareFetcher
    from data_provider.baostock_fetcher import BaostockFetcher
    from portal.backtester import run_backtest

    cache = StockDataCache()

    # 只用内网可用的两个数据源（volume 单位均为股，无需额外换算）
    mgr = DataFetcherManager(fetchers=[AkshareFetcher(), BaostockFetcher()])
    log(f"🌐 全量拉取最近 {DAYS} 日 K 线（约 3 年）…")
    t0 = time.perf_counter()
    result = mgr.get_daily_data(code, days=DAYS)
    elapsed = time.perf_counter() - t0

    if isinstance(result, tuple):
        df, source_name = result
    else:
        df, source_name = result, "unknown"

    if df is None or df.empty:
        log(f"⚠️  {code} 数据为空，跳过")
        return

    log(f"✅ 获取 {len(df)} 条 K 线（来源: {source_name}，耗时 {elapsed*1000:.0f}ms）")

    # 写入缓存（覆盖旧数据）
    cache.save_kline(code, df, source_name)
    log(f"💾 缓存已更新")

    # 重算回测
    log(f"🔄 开始重算回测…")
    t1 = time.perf_counter()
    try:
        bt_result = run_backtest(df)
        cache.save_backtest(code, bt_result)
        bt_elapsed = time.perf_counter() - t1
        # 打印关键统计
        period_range = bt_result.get("stock_days", "")
        signals_count = len(bt_result.get("signals", {}))
        log(f"📊 回测完成（{bt_elapsed*1000:.0f}ms）信号类型={signals_count} 数据范围={period_range}")
    except Exception as e:
        log(f"⚠️  回测失败：{e}")

    log(f"═══ {code} 处理完毕 ═══")
    print()


if __name__ == "__main__":
    total_t0 = time.perf_counter()
    for code in STOCKS:
        try:
            fetch_and_backtest(code)
        except Exception as e:
            log(f"❌ {code} 处理异常：{e}")
            import traceback
            traceback.print_exc()
        print()

    total_elapsed = time.perf_counter() - total_t0
    log(f"全部完成，总耗时 {total_elapsed:.1f}s")
