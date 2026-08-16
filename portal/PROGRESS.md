# Portal 功能实现进度

> 开始日期：2026-08-16

## 待实现功能清单

| # | 功能 | 模块 | 复杂度 | 状态 |
|---|---|---|---|---|
| 1 | 筹码分布 | technical.py | 中 | ✅ 完成 |
| 2 | 换手率趋势（近30日） | technical.py | 低 | ✅ 完成 |
| 3 | 融资融券余额趋势 | technical.py | 中 | ✅ 完成 |
| 4 | 北向资金（陆股通） | fundamental.py | 中 | ✅ 完成 |
| 5 | 大股东减持公告 | fundamental.py | 中 | ✅ 完成 |
| 6 | 报告内嵌价格折线图 | report-view.js + index.html | 中 | ✅ 完成 |
| 7 | 维度雷达图 | report-view.js | 低 | ✅ 完成 |
| 8 | 自选股显示名称+涨跌幅 | watchlist.js + server.py | 中 | ✅ 完成 |
| 9 | 历史报告 localStorage 缓存 | run.js | 低 | ✅ 完成 |
| 10 | 大宗商品实时价格接入 | industry.py + data_cache.py | 高 | ✅ 完成 |
| 11 | 回测引擎 | backtester.py（新建） | 中 | ✅ 完成 |

---

## 实现记录

### #1 筹码分布
- **状态**：✅ 完成（新增 _analyze_chip 子模块，调用 akshare stock_cyq_em）
- **实现方案**：调用 akshare `stock_cyq_em()`，计算成本集中度、主力建仓区间、当前价位套牢比例，作为 technical.py 新子模块 `chip_distribution`
- **文件**：`portal/analyzers/technical.py`

### #2 换手率趋势（近30日）
- **状态**：✅ 完成（新增 _analyze_turnover 子模块）
- **实现方案**：K线 CSV 中已有换手率字段，计算近30日均值、趋势斜率，判断放量/缩量特征
- **文件**：`portal/analyzers/technical.py`

### #3 融资融券余额趋势
- **状态**：✅ 完成（新增 _analyze_margin 子模块）
- **实现方案**：`akshare.stock_margin_detail_szse/sse()`，按股票代码过滤，计算近30日融资余额趋势
- **文件**：`portal/analyzers/technical.py`

### #4 北向资金（陆股通）
- **状态**：✅ 完成（新增 _analyze_northbound 子模块）
- **实现方案**：`akshare.stock_hsgt_hist_em()`，获取个股北向资金近期净买入趋势
- **文件**：`portal/analyzers/fundamental.py`

### #5 大股东减持公告
- **状态**：✅ 完成（新增 _analyze_holder_change 子模块）
- **实现方案**：`akshare.stock_hold_num_cninfo()`，解析近3个月大股东增减持公告，给出信号
- **文件**：`portal/analyzers/fundamental.py`

### #6 报告内嵌价格折线图
- **状态**：✅ 完成（ECharts + server kline_data 字段）
- **实现方案**：在 index.html 引入 ECharts CDN，report-view.js 在技术面维度卡片顶部渲染近60日收盘价+MA5/20折线图
- **文件**：`portal/index.html`、`portal/js/components/report-view.js`

### #7 维度雷达图
- **状态**：✅ 完成（ECharts radar，替换三圆环）
- **实现方案**：ECharts radar 类型，替换报告头部三个独立圆环评分，展示技术/基本面/产业链三维评分
- **文件**：`portal/js/components/report-view.js`

### #8 自选股显示名称+涨跌幅
- **状态**：✅ 完成（GET /quote 接口 + watchlist.js 展示）
- **实现方案**：server.py 新增 `GET /quote?codes=xxx,yyy` 接口（akshare 实时行情），watchlist.js 加载时拉取并展示名称+涨跌幅+现价
- **文件**：`portal/server.py`、`portal/js/tabs/watchlist.js`

### #9 历史报告 localStorage 缓存
- **状态**：✅ 完成（localStorage dsa_report_{id}）
- **实现方案**：run.js `_saveHistory` 时同步把完整报告 JSON 写入 localStorage（key: `dsa_report_{taskId}`），点击历史时优先读本地，超 200KB 不缓存
- **文件**：`portal/js/tabs/run.js`

### #10 大宗商品实时价格接入
- **状态**：✅ 完成（COMMODITY_FUTURES_MAP + futures_main_sina）
- **实现方案**：在 industry.py 识别关键词后，通过 akshare 期货/现货接口拉取实时价格（碳酸锂→`futures_main_sina()`，原油→`oil_price_history()`），价格数字直接传入 LLM prompt
- **文件**：`portal/analyzers/industry.py`、`portal/data_cache.py`

### #11 回测引擎
- **状态**：✅ 完成（portal/backtester.py + GET /backtest 接口）
- **实现方案**：新建 `portal/backtester.py`，基于 K线历史数据统计各信号触发后5/10/20日平均收益率，输出胜率报告；server.py 新增 `POST /backtest` 接口
- **文件**：`portal/backtester.py`（新建）、`portal/server.py`

---

## 状态说明
- ⏳ 待开始
- 🔄 进行中
- ✅ 完成
- ❌ 有问题（见备注）
