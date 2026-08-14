# Daily Stock Analysis — 功能代码导航手册

> **用途**：快速定位各功能对应的源文件，方便直接修改。不包含项目原理介绍，原始文档见 `README.md` / `docs/`。

---

## 目录

1. [运行前提条件](#1-运行前提条件)
2. [快速启动（Test Run）](#2-快速启动test-run)
3. [Test Run 记录](#3-test-run-记录)
4. [功能目录（代码地图）](#4-功能目录代码地图)
5. [常见修改场景](#5-常见修改场景)

---

## 1. 运行前提条件

### 1.1 Python 版本

```
Python 3.10+
```

### 1.2 安装依赖

```bash
cd C:\Users\I762120\Desktop\incident\daily
pip install -r requirements.txt
```

> 可选：建议先建虚拟环境 `python -m venv venv && venv\Scripts\activate`

### 1.3 必填环境变量

复制 `.env.example` → `.env`，**至少填写以下两项**才能正常运行：

| 变量名 | 说明 | 示例 |
|---|---|---|
| `STOCK_LIST` | 要分析的股票代码，逗号分隔 | `600519,300750,002594` |
| **LLM（任选一个）** | | |
| `GEMINI_API_KEY` | Google Gemini，有免费额度 | `AIza...` |
| `DEEPSEEK_API_KEY` | DeepSeek，性价比高 | `sk-...` |
| `ANSPIRE_API_KEYS` | Anspire 一站式 | `sk-...` |
| `OPENAI_API_KEY` | OpenAI | `sk-...` |
| `ANTHROPIC_API_KEY` | Anthropic Claude | `sk-ant-...` |

> **不填通知渠道也能运行**，报告会打印到控制台日志。

### 1.4 可选但常用的配置项

```bash
# .env 中设置
DATABASE_PATH=./data/stock_analysis.db   # SQLite 路径（默认值已可用）
LOG_DIR=./logs                           # 日志目录
MARKET_REVIEW_ENABLED=true               # 是否附带大盘复盘
RUN_IMMEDIATELY=true                     # 启动时立即运行一次
```

---

## 2. 快速启动（Test Run）

### 2.1 最小化单次运行（推荐首次测试）

```bash
cd C:\Users\I762120\Desktop\incident\daily

# 1. 复制配置文件
copy .env.example .env

# 2. 编辑 .env，填入 STOCK_LIST 和至少一个 LLM API Key

# 3. 运行（分析 .env 里的自选股，不推送通知，只打印结果）
python main.py --no-notify
```

### 2.2 干跑模式（只抓数据，不调用 LLM，速度最快）

```bash
python main.py --dry-run --no-notify
```

### 2.3 指定股票临时分析

```bash
python main.py --stocks 600519,000001 --no-notify
```

### 2.4 调试模式（输出详细日志）

```bash
python main.py --debug --no-notify
```

### 2.5 仅大盘复盘

```bash
python main.py --market-review --no-notify
```

### 2.6 启动 Web 界面（含 API 服务，不自动分析）

```bash
python main.py --webui-only
# 访问 http://127.0.0.1:8000
# API 文档: http://127.0.0.1:8000/docs
```

### 2.7 定时任务模式（每日 18:00 自动运行）

```bash
# .env 中设置
SCHEDULE_ENABLED=true
SCHEDULE_TIME=18:00

python main.py --schedule
```

---

## 3. Test Run 记录

### 运行环境

- 日期：2026-08-14
- Python 版本：3.13.3
- 平台：Windows 11 Enterprise
- 测试股票：600519（贵州茅台）、300750（宁德时代）

### 运行命令

```bash
cd C:\Users\I762120\Desktop\incident\daily
python main.py --dry-run --no-notify
```

### 运行结果摘要

```
11:27:00  系统启动
11:27:00  WARNING: 未配置 Tushare Token（正常，使用备用数据源）
11:27:00  WARNING: 未配置 AI 模型（dry-run 模式不需要）
11:27:16  数据源初始化: EfinanceFetcher(P0) > AkshareFetcher(P1) > PytdxFetcher(P2) > BaostockFetcher(P3) > YfinanceFetcher(P4)
11:27:16  数据库初始化: data/stock_analysis.db ✓
11:27:16  开始并发分析 2 只股票（并发数 2）

-- 300750 宁德时代 --
  [尝试 1/5] EfinanceFetcher → 失败（东方财富接口被封/连接断开）
  [切换]     AkshareFetcher → 新浪财经 → 成功 rows=43 elapsed=17s
  保存 43 条数据 ✓
  跳过 AI 分析（dry-run）

-- 600519 贵州茅台 --
  [尝试 1/5] EfinanceFetcher → 失败（东方财富接口被封/连接断开）
  [切换]     AkshareFetcher → 新浪财经 → 成功 rows=43 elapsed=14s
  保存 43 条数据 ✓
  跳过 AI 分析（dry-run）

11:27:59  分析完成: 成功=2 失败=0 耗时=42.41s
11:28:06  自动回测: processed=0（无历史数据可回测，符合预期）
11:28:06  程序执行完成 ✓
```

**结论：系统运行正常。** 数据抓取成功（通过 AkShare/新浪财经备用源），dry-run 模式跳过 AI 分析，总耗时约 42 秒。

### 遇到的问题 & 解决方案

| 问题 | 原因 | 解决方案 |
|---|---|---|
| EfinanceFetcher（东方财富）连接断开 | 东财接口在 SAP 内网环境被限制/封锁 | 系统自动切换到 AkShare 备用源，无需手动处理 |
| `DeprecationWarning: datetime.utcfromtimestamp` | lark-oapi 库使用了旧 API | 仅警告，不影响运行；等待 lark-oapi 升级修复 |
| stock-index 远程更新失败（KR 市场不支持） | 韩国股市代码未在映射表中 | 自动降级到本地索引，无需处理 |

### 正式运行前还需配置

1. **LLM API Key**（必填）：在 `.env` 填入 `GEMINI_API_KEY` 或其他 LLM 密钥
2. **通知渠道**（可选）：填入企业微信/飞书/Telegram 等 Webhook URL
3. **Tushare Token**（可选）：获取后可提升数据质量和稳定性

---

## 4. 功能目录（代码地图）

### 4.1 程序入口 & 调度

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `main.py` | **主入口**，CLI 参数解析、模式分发（单次/定时/WebUI/回测） | 新增 CLI 参数、修改启动逻辑 |
| `main.py:parse_arguments()` | 所有命令行参数定义（`--debug`、`--dry-run`、`--stocks` 等） | 新增参数 |
| `main.py:run_full_analysis()` | 完整分析流程协调（个股 + 大盘复盘 + 回测 + 飞书文档） | 调整分析顺序、加新步骤 |
| `main.py:main()` | 模式路由：回测/大盘复盘/定时/单次 | 新增运行模式 |
| `src/scheduler.py` | `schedule` 库封装，定时触发逻辑 | 修改调度策略 |
| `server.py` | 仅启动 FastAPI（不运行分析） | 纯 API 服务部署 |
| `webui.py` | Web 界面快捷启动入口 | Web 部署快捷方式 |

---

### 4.2 配置管理

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `.env.example` | 所有配置项模板（含注释说明） | **查找有哪些配置项** |
| `.env` | 实际运行配置（不入 git） | 日常配置修改 |
| `src/config.py` | `Config` 单例类，所有配置项的 Python 读取入口 | 新增配置项的 Python 映射 |
| `src/core/config_manager.py` | 运行时动态读写 `.env` 文件（供 Web 设置页使用） | Web 设置持久化逻辑 |
| `src/core/config_registry.py` | 配置项注册表（类型、默认值、描述，供 Web 设置页展示） | 新配置项的 Web 可见性 |

**新增配置项步骤**：`.env.example` 加注释 → `src/config.py` 的 `Config` 类加字段 → `src/core/config_registry.py` 注册（可选，让 Web 设置页显示）。

---

### 4.3 数据抓取

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `data_provider/__init__.py` | `DataFetcherManager`：多数据源统一调度，按优先级依次尝试 | 调整数据源优先级 |
| `data_provider/base.py` | 基类 `BaseDataFetcher`，定义接口规范 | 新增自定义数据源 |
| `data_provider/efinance_fetcher.py` | 东方财富（默认优先级 0） | |
| `data_provider/akshare_fetcher.py` | AkShare / 新浪（优先级 1） | |
| `data_provider/tushare_fetcher.py` | Tushare Pro（优先级 2，需 Token） | |
| `data_provider/pytdx_fetcher.py` | 通达信本地行情（优先级 2） | |
| `data_provider/baostock_fetcher.py` | 证券宝（优先级 3） | |
| `data_provider/yfinance_fetcher.py` | Yahoo Finance（优先级 4，美股） | |
| `data_provider/finnhub_fetcher.py` | Finnhub（美股，需 API Key） | |
| `data_provider/alphavantage_fetcher.py` | AlphaVantage（美股，需 API Key） | |
| `data_provider/longbridge_fetcher.py` | 长桥 OpenAPI（港股/美股兜底） | |
| `data_provider/fundamental_adapter.py` | 基本面数据适配器 | 修改基本面字段映射 |

**数据源优先级**由 `.env` 的 `EFINANCE_PRIORITY`、`AKSHARE_PRIORITY` 等控制，数值越小越优先。

---

### 4.4 技术分析

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `src/stock_analyzer.py` | `StockTrendAnalyzer`：MA/MACD/RSI/KDJ/布林带计算，多头排列判断，综合评分 | **修改技术指标权重、新增指标** |
| `src/market_analyzer.py` | 大盘宽度分析（涨跌比、量能、市场热度） | 修改大盘指标计算 |
| `src/market_context.py` | 市场状态上下文（牛熊判断） | |
| `src/core/market_phase_prompt.py` | 市场阶段 Prompt 生成 | |
| `src/core/market_phase_summary.py` | 市场阶段摘要渲染 | |
| `src/phase_decision_guardrail.py` | 决策护栏：根据市场阶段限制激进买入信号 | 调整风控规则 |

**修改评分权重**：打开 `src/stock_analyzer.py`，搜索 `score` 或 `weight` 关键字，找到权重定义区域直接修改数值。

---

### 4.5 基本面数据

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `src/services/fundamental_pipeline_service.py` | 基本面聚合流水线（PE/PB/ROE/营收/利润） | 修改基本面指标 |
| `data_provider/fundamental_adapter.py` | A 股基本面适配 | |
| `data_provider/yfinance_fundamental_adapter.py` | 美股基本面适配（Yahoo Finance） | |

---

### 4.6 LLM 智能分析

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `src/analyzer.py` | `GeminiAnalyzer`（名字历史遗留，实际支持所有 LLM）：调用 LiteLLM、解析结构化分析结果 | **修改分析 Prompt、调整输出格式** |
| `src/analysis_context_pack_prompt.py` | 组装发给 LLM 的完整 Prompt（技术面+基本面+新闻+市场状态） | **修改 Prompt 内容** |
| `src/analysis_context_pack_overview.py` | Prompt 概览生成 | |
| `src/llm/__init__.py` | LiteLLM Router 封装 | 切换/新增 LLM 模型 |
| `src/llm/generation_params.py` | 模型参数（temperature 等）兼容性处理 | |

**修改 Prompt**：主要改 `src/analysis_context_pack_prompt.py` 中的模板字符串，以及 `src/analyzer.py` 中的系统 Prompt。

---

### 4.7 新闻搜索

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `src/search_service.py` | `SearchService`：多搜索引擎聚合（Tavily/SerpAPI/Brave/SearXNG/Bocha） | 切换搜索源、修改搜索策略 |
| `src/services/social_sentiment_service.py` | 美股社交媒体情绪（Reddit/X，需 adanos.org API Key） | |

---

### 4.8 通知推送

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `src/notification.py` | `NotificationService`：统一通知入口，路由到各渠道 | 新增推送渠道的路由逻辑 |
| `src/notification_routing.py` | 通知路由策略（哪类消息发到哪个渠道） | 精细控制推送路由 |
| `src/notification_noise.py` | 通知降噪（去重/静默时段/频率限制） | 减少重复通知 |
| `src/notification_sender/wechat.py` | 企业微信 Bot | |
| `src/notification_sender/feishu.py` | 飞书 Webhook | |
| `src/notification_sender/telegram.py` | Telegram Bot | |
| `src/notification_sender/email.py` | 邮件（SMTP） | |
| `src/notification_sender/discord.py` | Discord Webhook / Bot | |
| `src/notification_sender/slack.py` | Slack Bot / Webhook | |
| `src/notification_sender/dingtalk.py` | 钉钉 Webhook | |
| `src/notification_sender/pushplus.py` | PushPlus（国内） | |
| `src/notification_sender/serverchan3.py` | Server酱3 | |
| `src/notification_sender/pushover.py` | Pushover | |
| `src/notification_sender/ntfy.py` | ntfy | |
| `src/notification_sender/gotify.py` | Gotify | |
| `src/notification_sender/custom_webhook.py` | 自定义 Webhook | |

---

### 4.9 报告模板

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `templates/report_markdown.j2` | 完整 Markdown 报告模板（`REPORT_TYPE=full`） | **修改报告格式** |
| `templates/report_brief.j2` | 简短摘要模板（`REPORT_TYPE=brief`） | |
| `templates/report_wechat.j2` | 企业微信专用模板 | |
| `templates/_macros.j2` | Jinja2 公共宏（颜色标签等） | 修改公共渲染组件 |
| `src/formatters.py` | 结构化数据 → Markdown 文本转换 | 修改非模板渲染的格式 |

---

### 4.10 选股策略（Agent 模式）

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `strategies/` | 15 个 YAML 策略文件（bull_trend、ma_golden_cross 等） | **新增/修改选股策略** |
| `src/agent/strategies/` | 策略执行器 | 修改策略运行逻辑 |
| `src/agent/orchestrator.py` | Multi-Agent 编排（quick/standard/full/specialist） | 修改 Agent 协作流程 |
| `src/agent/factory.py` | Agent 工厂，根据配置创建 Agent 实例 | |
| `src/agent/runner.py` | Single/Multi Agent 运行器 | |

`.env` 中 `AGENT_MODE=true` + `AGENT_SKILLS=bull_trend,ma_golden_cross` 启用。

---

### 4.11 回测

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `src/core/backtest_engine.py` | 回测引擎：对历史分析结果评估准确率 | 修改回测算法 |
| `src/services/backtest_service.py` | 回测服务封装（`BacktestService`） | |
| `api/v1/endpoints/backtest.py` | 回测 API 端点 | |

```bash
# 手动运行回测
python main.py --backtest
python main.py --backtest --backtest-code 600519 --backtest-days 20
```

---

### 4.12 投资组合管理

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `src/services/portfolio_service.py` | 投资组合增删查改 | |
| `src/repositories/portfolio_repo.py` | 投资组合数据库操作 | |
| `api/v1/endpoints/portfolio.py` | 投资组合 REST API | |
| `api/v1/schemas/portfolio.py` | 请求/响应 Schema | |

---

### 4.13 价格告警

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `src/services/alert_service.py` | 告警规则 CRUD | 修改告警类型 |
| `src/services/alert_worker.py` | 后台轮询告警触发 | 修改检查频率 |
| `src/repositories/alert_repo.py` | 告警数据库操作 | |
| `api/v1/endpoints/alerts.py` | 告警 REST API | |

---

### 4.14 Web 界面（前端）

| 目录/文件 | 作用 | 修改场景 |
|---|---|---|
| `apps/dsa-web/src/pages/` | 各页面（仪表盘/历史/回测/告警/设置/AI聊天） | 修改页面布局 |
| `apps/dsa-web/src/components/` | 公共组件（报告卡片、图表、布局等） | 修改 UI 组件 |
| `apps/dsa-web/src/api/` | 前端 API 调用层 | 对接新 API 端点 |
| `apps/dsa-web/src/stores/` | 状态管理（analysisStore 等） | 修改全局状态逻辑 |
| `apps/dsa-web/src/hooks/` | React Hooks（useTaskStream、useWatchlist 等） | |

```bash
# 前端开发模式
cd apps/dsa-web
npm install
npm run dev
```

---

### 4.15 FastAPI 后端接口

| 文件 | 作用 |
|---|---|
| `api/app.py` | FastAPI app 创建，路由注册，CORS 配置 |
| `api/v1/router.py` | v1 路由汇总 |
| `api/v1/endpoints/analysis.py` | `/api/v1/analysis/analyze` 触发分析 |
| `api/v1/endpoints/stocks.py` | 自选股 CRUD |
| `api/v1/endpoints/history.py` | 历史分析记录 |
| `api/v1/endpoints/backtest.py` | 回测 |
| `api/v1/endpoints/portfolio.py` | 投资组合 |
| `api/v1/endpoints/alerts.py` | 价格告警 |
| `api/v1/endpoints/agent.py` | AI 聊天（问股） |
| `api/v1/endpoints/system_config.py` | 系统配置读写 |
| `api/v1/endpoints/health.py` | 健康检查 |
| `api/middlewares/auth.py` | 登录认证中间件 |

---

### 4.16 数据存储

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `src/storage.py` | SQLite 数据库初始化，`get_db()` 连接管理 | 修改数据库路径/类型 |
| `src/repositories/analysis_repo.py` | 分析结果的增删查改 | |
| `src/repositories/stock_repo.py` | 自选股列表持久化 | |

---

### 4.17 聊天机器人

| 文件 | 作用 | 修改场景 |
|---|---|---|
| `bot/dispatcher.py` | 消息分发路由 | 新增命令处理 |
| `bot/handler.py` | 消息处理主逻辑 | |
| `bot/commands/analyze.py` | `/analyze` 命令 | 修改分析命令行为 |
| `bot/commands/chat.py` | AI 对话命令 | |
| `bot/commands/strategies.py` | 策略命令 | |
| `bot/platforms/dingtalk_stream.py` | 钉钉 Stream 接入 | |
| `bot/platforms/feishu_stream.py` | 飞书 Stream 接入 | |
| `bot/platforms/discord.py` | Discord Bot 接入 | |

---

## 5. 常见修改场景

### 场景 A：更换 LLM 模型

```bash
# 方法1：.env 中直接指定
LITELLM_MODEL=gemini/gemini-2.5-flash  # 或 deepseek/deepseek-v4-flash 等

# 方法2：多渠道 + fallback（详见 .env.example 中注释）
LLM_CHANNELS=deepseek,gemini
LLM_DEEPSEEK_API_KEY=sk-xxx
LLM_DEEPSEEK_MODELS=deepseek-v4-flash
```

---

### 场景 B：添加/删除自选股

```bash
# .env 中修改 STOCK_LIST
STOCK_LIST=600519,300750,002594,AAPL,TSLA
```

运行时也可通过 `--stocks` 参数临时指定，不改 `.env`。

---

### 场景 C：修改分析 Prompt

主要文件：`src/analysis_context_pack_prompt.py`（组装 Prompt）和 `src/analyzer.py`（系统角色 Prompt）。

搜索关键字：
```bash
# 找到主 Prompt 模板
grep -n "def format_analysis" src/analysis_context_pack_prompt.py
grep -n "system" src/analyzer.py
```

---

### 场景 D：修改报告输出格式

1. **Jinja2 模板**（推荐）：修改 `templates/report_markdown.j2`，控制 `REPORT_RENDERER_ENABLED=true`。
2. **代码格式化**：修改 `src/formatters.py` 中的格式化函数。
3. **报告类型**：`.env` 中设置 `REPORT_TYPE=simple|full|brief`。

---

### 场景 E：添加新通知渠道

1. 在 `src/notification_sender/` 新建 `my_channel.py`，继承基类实现 `send()` 方法。
2. 在 `src/notification.py` 的 `NotificationService` 中注册新渠道。
3. 在 `src/config.py` 的 `Config` 类中添加对应的配置字段。
4. 在 `.env.example` 添加配置注释。

---

### 场景 F：新增选股策略

1. 在 `strategies/` 目录新建 `my_strategy.yaml`（参考 `strategies/bull_trend.yaml` 的格式）。
2. `.env` 中添加：`AGENT_MODE=true` + `AGENT_SKILLS=...,my_strategy`。

---

### 场景 G：修改技术指标权重

打开 `src/stock_analyzer.py`，搜索 `score`，找到各指标的权重打分逻辑，直接修改数值。

---

### 场景 H：修改数据源优先级

`.env` 中直接设置：

```bash
EFINANCE_PRIORITY=0      # 默认最高
AKSHARE_PRIORITY=1
TUSHARE_PRIORITY=2
YFINANCE_PRIORITY=0      # 美股时可提到最高
```

---

*本文档由 Claude 自动生成，基于 2026-08-14 代码快照。如代码结构有调整，请同步更新本文件对应行。*
