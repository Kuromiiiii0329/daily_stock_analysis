<div align="center">

# 股票智能分析系统 (Daily Stock Analysis)

[![CI](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/zhulinsen/daily_stock_analysis)

</div>

基于 AI 大模型的 A 股/港股/美股自选股智能分析系统。每日自动抓取行情数据、执行技术面和基本面分析、调用 LLM 生成决策报告，并推送到企业微信、飞书、Telegram、Discord、Slack、邮件等多个渠道。

---

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。作者不对使用本项目产生的任何损失负责。

---

## 目录

- [项目简介](#项目简介)
- [功能目录](#功能目录)
- [项目结构](#项目结构)
- [环境要求](#环境要求)
- [安装步骤](#安装步骤)
- [配置说明](#配置说明)
- [运行方法](#运行方法)
- [API 接口一览](#api-接口一览)
- [测试运行记录](#测试运行记录)
- [修改指南](#修改指南)

---

## 项目简介

本系统是一个全栈股票分析平台，包含：

- **Python 后端**：FastAPI REST API + 定时任务调度引擎
- **React 前端** (`apps/dsa-web`)：基于 Vite + TypeScript 的 Web 界面，提供决策仪表盘、历史回放、回测、投资组合、告警等功能
- **Electron 桌面客户端** (`apps/dsa-desktop`)：跨平台桌面封装
- **聊天机器人**：支持钉钉 Stream / 飞书 Stream / Discord / 企业微信 Bot

### 分析流程概述

```
触发（定时/手动/API）
    -> DataFetcherManager    多数据源行情抓取（efinance / akshare / tushare 等）
    -> StockTrendAnalyzer    纯技术指标计算（MA/MACD/RSI/支撑阻力/买卖信号评分）
    -> SearchService         新闻/舆情搜索（Tavily / Brave / SerpAPI / Bocha）
    -> GeminiAnalyzer        LLM 分析（LiteLLM Router 多模型 failover）
    -> 后处理                决策稳定性校准 / 筹码结构填充 / 价位填充
    -> NotificationService   多渠道推送（企业微信/飞书/Telegram 等）
    -> SQLite 持久化         行情 / 分析历史 / 新闻 / 用量统计
    -> 可选：飞书文档自动创建 / 自动回测评估
```

---

## 功能目录

### 1. 多数据源行情抓取

| 数据源 | 优先级 | 支持市场 | 实现文件 |
|---|---|---|---|
| EfinanceFetcher | P0（最高） | A 股/ETF | `data_provider/efinance_fetcher.py` |
| AkshareFetcher | P1 | A 股/港股 | `data_provider/akshare_fetcher.py` |
| PytdxFetcher | P2 | A 股 | `data_provider/pytdx_fetcher.py` |
| TushareFetcher | P2/P0 | A 股/港股 | `data_provider/tushare_fetcher.py` |
| BaostockFetcher | P3 | A 股 | `data_provider/baostock_fetcher.py` |
| FinnhubFetcher | P2（美股） | 美股 | `data_provider/finnhub_fetcher.py` |
| AlphaVantageFetcher | P3（美股） | 美股 | `data_provider/alphavantage_fetcher.py` |
| YfinanceFetcher | P4 | A 股/港股/美股 | `data_provider/yfinance_fetcher.py` |
| LongbridgeFetcher | P5 | 美股/港股 | `data_provider/longbridge_fetcher.py` |

**统一入口**：`data_provider/base.py` 中的 `DataFetcherManager.get_daily_data()`，自动 failover。

**修改指引**：
- 新增数据源：继承 `BaseFetcher`，实现 `_fetch_raw_data()` 和 `_normalize_data()`，在 `DataFetcherManager._init_default_fetchers()` 中注册
- 调整优先级：修改 `_init_default_fetchers()` 中各 fetcher 的 `priority` 参数
- 超时配置：环境变量 `EFINANCE_CALL_TIMEOUT`（默认 30s）

---

### 2. 技术指标分析

**实现文件**：`src/stock_analyzer.py`，核心类 `StockTrendAnalyzer`

| 指标 | 实现方法 |
|---|---|
| 均线（MA5/10/20/60） | `_calculate_mas()` |
| MACD（12/26/9 标准参数） | `_calculate_macd()` |
| RSI（6/12/24 周期） | `_calculate_rsi()` |
| 趋势状态（7 级：STRONG_BULL 到 STRONG_BEAR） | `_analyze_trend()` |
| 量能分析 | `_analyze_volume()` |
| 支撑/阻力位 | `_analyze_support_resistance()` |
| 综合买卖信号评分（0-100 分） | `_generate_signal()` |

评分权重：趋势(30) + 乖离(20) + 量能(15) + 支撑(10) + MACD(15) + RSI(10)

**修改指引**：
- 调整 MACD 参数：修改 `_calculate_macd()` 中的 EMA 周期（当前 12/26/9）
- 调整买卖信号阈值：修改 `_generate_signal()` 中的分数区间和 `BuySignal` 枚举映射
- 新增技术指标：在 `TrendAnalysisResult` dataclass 添加字段，在 `analyze()` 方法中调用

---

### 3. 基本面数据采集

**实现文件**：`data_provider/fundamental_adapter.py`（A 股），`data_provider/yfinance_fundamental_adapter.py`（美股/港股）

覆盖数据：估值指标、营收/利润增长、股息、机构持股、资金流向、龙虎榜、板块归属

**统一入口**：`DataFetcherManager.get_fundamental_context(stock_code, budget_seconds)`

**修改指引**：
- 新增基本面指标：在 `AkshareFundamentalAdapter.get_fundamental_bundle()` 中添加 AkShare 接口调用
- 调整超时：环境变量 `FUNDAMENTAL_STAGE_TIMEOUT_SECONDS`（默认 8s）
- 关闭基本面采集：`.env` 中设 `FUNDAMENTAL_PIPELINE_ENABLED=false`

---

### 4. LLM 智能分析

**实现文件**：`src/analyzer.py`，核心类 `GeminiAnalyzer`

- 通过 `LiteLLM Router` 支持多 LLM 供应商（OpenAI、Gemini、Anthropic、Azure 等），自动 failover
- 输出结构化 JSON，解析为 `AnalysisResult` dataclass
- 包含决策稳定性校准（防止 LLM 因单日涨跌导致建议频繁翻转）：`stabilize_decision_with_structure()`
- 支持多语言报告（中文/英文）：`src/report_language.py`

**修改指引**：
- 修改分析 prompt：`GeminiAnalyzer._get_analysis_system_prompt()`
- 修改输出 JSON schema：`src/schemas/report_schema.py` 中的 `AnalysisReportSchema`
- 新增 LLM 供应商：在 `.env` 中配置 `LLM_CHANNELS`，支持任意 LiteLLM 兼容接口
- 调整决策校准逻辑：`stabilize_decision_with_structure()` 函数

---

### 5. 主分析流水线

**实现文件**：`src/core/pipeline.py`，核心类 `StockAnalysisPipeline`

`analyze_stock()` 方法 8 个步骤：
1. 获取实时行情
2. 获取筹码分布
3. 聚合基本面上下文
4. 运行技术指标分析
5. 多维度新闻搜索（或 Agent 模式）
6. 增强上下文（合并实时行情 + 筹码 + 技术面 + 基本面）
7. 调用 LLM 分析并后处理
8. 保存分析历史到 SQLite

**修改指引**：
- 新增分析步骤：在 `analyze_stock()` 方法中按步骤顺序插入
- 修改并发数：环境变量 `MAX_WORKERS`（默认 3）
- 切换 Agent 模式：环境变量 `AGENT_MODE_ENABLED=true`

---

### 6. 多渠道通知推送

**实现文件**：`src/notification.py`，各渠道 sender 位于 `src/notification_sender/`

| 渠道 | Sender 文件 | 所需配置 |
|---|---|---|
| 企业微信 Webhook | `wechat_sender.py` | `WECHAT_WEBHOOK_URL` |
| 飞书 Webhook | `feishu_sender.py` | `FEISHU_WEBHOOK_URL` |
| Telegram | `telegram_sender.py` | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` |
| 邮件 SMTP | `email_sender.py` | `EMAIL_SENDER` + `EMAIL_PASSWORD` + `EMAIL_RECIPIENTS` |
| Discord | `discord_sender.py` | `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` 或 Webhook URL |
| Slack | `slack_sender.py` | `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` 或 Webhook URL |
| Pushover | `pushover_sender.py` | `PUSHOVER_USER_KEY` + `PUSHOVER_APP_TOKEN` |
| ntfy | `ntfy_sender.py` | `NTFY_URL` |
| Gotify | `gotify_sender.py` | `GOTIFY_URL` + Token |
| PushPlus | `pushplus_sender.py` | `PUSHPLUS_TOKEN` |
| Server酱3 | `serverchan3_sender.py` | `SERVERCHAN3_SENDKEY` |
| AstrBot | `astrbot_sender.py` | `ASTRBOT_WEBHOOK_URL` |
| 自定义 Webhook | `custom_webhook_sender.py` | `CUSTOM_WEBHOOK_URLS` |

**通知降噪**：`src/notification_noise.py` — 支持去重、冷却时间、静默时段（`NOTIFICATION_QUIET_HOURS`）

**修改指引**：
- 新增渠道：在 `src/notification_sender/` 创建新 sender，在 `NotificationService` 中注册 mixin
- 修改报告格式：`src/notification.py` 中的 `generate_daily_report()` / `generate_dashboard_report()`
- 修改报告模板：`templates/report_markdown.j2`、`templates/report_brief.j2`、`templates/report_wechat.j2`

---

### 7. 定时调度

**实现文件**：`src/scheduler.py`，核心类 `Scheduler`

- 每日定时执行分析（`SCHEDULE_TIME`，默认 `18:00`）
- 支持运行时热重载配置（无需重启）
- 支持 SIGINT/SIGTERM 优雅退出

**修改指引**：
- 修改执行时间：`.env` 中设 `SCHEDULE_TIME=HH:MM`（运行时热重载，无需重启）
- 多时间点执行：修改 `main.py` 中的 `_build_schedule_time_provider()` 注入多个时间点

---

### 8. 选股策略

**目录**：`strategies/`，15 个 YAML 策略文件

内置策略：牛市趋势、均线金叉、量价突破、热点题材、事件驱动、成长质量、预期重定价、缩量回调、底部放量、龙头股、一阳三阴、箱体振荡、缠论、波浪理论、情绪周期

**修改指引**：
- 新增策略：在 `strategies/` 目录创建新 YAML 文件
- 查看策略 schema：`strategies/README.md`

---

### 9. 回测系统

**实现文件**：`src/services/backtest_service.py`，API 路由 `api/v1/endpoints/backtest.py`，核心引擎 `src/core/backtest_engine.py`

- 基于历史分析记录评估买卖建议准确率
- 支持按股票代码、时间段、决策类型过滤

---

### 10. 投资组合管理

**实现文件**：`src/services/`（portfolio 相关），API 路由 `api/v1/endpoints/portfolio.py`

- 多账户管理、交易记录、现金流水、企业行动（分红/拆股）
- 持仓快照、风险报告
- 支持导入主流券商 CSV 对账单

---

### 11. 价格告警

**实现文件**：`src/services/`（alerts 相关），API 路由 `api/v1/endpoints/alerts.py`

- 创建价格/涨跌幅告警规则
- 触发后通过通知渠道推送

---

### 12. AI Agent 聊天

**实现文件**：`src/agent/`，API 路由 `api/v1/endpoints/agent.py`

- 支持自然语言查询个股分析
- SSE 流式输出
- 会话历史持久化（SQLite）
- LLM 用量追踪

---

### 13. Web 界面

**目录**：`apps/dsa-web/`（Vite + React + TypeScript）

功能页面：仪表盘首页、历史记录、回测、投资组合、告警、股票筛选、AI 聊天、系统设置

---

### 14. 桌面客户端

**目录**：`apps/dsa-desktop/`（Electron）

跨平台桌面封装，内嵌 Web 界面，支持 macOS/Windows/Linux 打包发布。

---

## 项目结构

```
daily/
├── main.py                     # CLI 主入口（调度/单次/API/回测等模式）
├── server.py                   # 仅启动 FastAPI（uvicorn server:app）
├── webui.py                    # 等价于 --webui-only，读取 WEBUI_HOST/PORT
├── requirements.txt            # Python 依赖
├── pyproject.toml              # Black / isort 格式化配置
├── setup.cfg                   # flake8 / pytest 配置
├── .env.example                # 环境变量模板（780 行，含所有可用配置）
│
├── api/                        # FastAPI 应用层
│   ├── app.py                  # 应用工厂 create_app()，CORS/Auth/静态文件/SPA
│   ├── deps.py                 # 依赖注入（DB session, Config, SystemConfigService）
│   └── v1/
│       ├── router.py           # 聚合所有 v1 路由
│       ├── endpoints/          # 各业务端点（analysis/history/stocks/backtest/...）
│       └── schemas/            # Pydantic 请求/响应 schema
│
├── src/                        # 核心业务逻辑
│   ├── config.py               # Config 单例，读取 .env，校验配置
│   ├── analyzer.py             # GeminiAnalyzer：LiteLLM Router + 分析结果后处理
│   ├── stock_analyzer.py       # StockTrendAnalyzer：纯技术指标（MA/MACD/RSI/信号）
│   ├── storage.py              # SQLite ORM（DatabaseManager），所有表定义
│   ├── scheduler.py            # 定时调度（schedule 库，支持热重载）
│   ├── notification.py         # NotificationService 聚合类
│   ├── notification_sender/    # 各渠道 Sender mixin（email/wechat/feishu/telegram/...）
│   ├── notification_noise.py   # 通知降噪（去重/冷却/静默时段）
│   ├── formatters.py           # Markdown 转换工具（飞书/Telegram/Slack/WeChat/HTML）
│   ├── agent/                  # AI Agent 框架（orchestrator/executor/skills/tools）
│   ├── core/
│   │   ├── pipeline.py         # StockAnalysisPipeline：8 步分析主流水线
│   │   ├── market_review.py    # 市场综述（大盘 + 板块 + 热点）
│   │   ├── backtest_engine.py  # 回测引擎
│   │   └── trading_calendar.py # 交易日历（判断今日是否交易日）
│   ├── services/               # 业务服务层（32 个 service 文件）
│   ├── repositories/           # 数据访问层（analysis/stock/alert/backtest/portfolio）
│   ├── schemas/                # 内部 schema（报告 schema/市场数据 schema）
│   ├── data/
│   │   ├── stock_index_loader.py  # 读取 stocks.index.json（股票代码-名称索引）
│   │   └── stock_mapping.py       # 静态股票名称字典（~100+ 常用标的）
│   └── utils/                  # 工具函数（数据处理/脱敏/元数据）
│
├── data_provider/              # 数据源适配层
│   ├── base.py                 # BaseFetcher + DataFetcherManager（failover 协调）
│   ├── efinance_fetcher.py     # EfinanceFetcher（P0，A 股，无需 token）
│   ├── akshare_fetcher.py      # AkshareFetcher（P1，A 股/港股，无需 token）
│   ├── pytdx_fetcher.py        # PytdxFetcher（P2，通达信协议，A 股）
│   ├── tushare_fetcher.py      # TushareFetcher（需 TUSHARE_TOKEN）
│   ├── baostock_fetcher.py     # BaostockFetcher（P3，A 股，无需 token）
│   ├── yfinance_fetcher.py     # YfinanceFetcher（P4，A 股/港股/美股）
│   ├── finnhub_fetcher.py      # FinnhubFetcher（需 FINNHUB_API_KEY，美股）
│   ├── alphavantage_fetcher.py # AlphaVantageFetcher（需 ALPHAVANTAGE_API_KEY，美股）
│   ├── longbridge_fetcher.py   # LongbridgeFetcher（需长桥 OAuth/密钥，美股/港股）
│   ├── fundamental_adapter.py  # A 股基本面（AkShare 接口集合）
│   └── yfinance_fundamental_adapter.py  # 美股/港股基本面（yfinance）
│
├── strategies/                 # 选股策略 YAML 文件（15 个内置策略）
├── templates/                  # Jinja2 报告模板（markdown/brief/wechat）
├── bot/                        # 聊天机器人（钉钉 Stream/飞书 Stream/Discord）
│   ├── dispatcher.py           # 消息分发
│   ├── commands/               # 指令实现（analyze/chat/market/research/...）
│   └── platforms/              # 平台适配（dingtalk/feishu/discord）
├── apps/
│   ├── dsa-web/                # React + Vite 前端
│   └── dsa-desktop/            # Electron 桌面客户端
├── docker/
│   ├── Dockerfile              # 多阶段构建（node:20 前端 + python:3.11 运行）
│   ├── docker-compose.yml      # analyzer + server 两个 service
│   └── entrypoint.sh           # 容器入口（权限修复 + gosu 降权）
├── scripts/                    # 构建/CI 脚本（.sh / .ps1 / .py）
├── tests/                      # ~175 个单元/集成测试（pytest）
└── docs/                       # 文档（部署指南/FAQ/Bot 命令/架构图等）
```

---

## 环境要求

### Python 版本

Python 3.10 或以上（推荐 3.11/3.12）

### 核心依赖（`requirements.txt`）

| 类别 | 包 |
|---|---|
| 数据源 | `efinance`, `akshare`, `tushare`, `pytdx`, `baostock`, `yfinance`, `longbridge`, `tickflow` |
| AI 分析 | `litellm>=1.80.10,<2.0.0`, `tiktoken`, `openai`, `PyYAML` |
| 数据处理 | `pandas>=2.0.0`, `numpy`, `openpyxl`, `pypinyin`, `json-repair` |
| 新闻搜索 | `tavily-python`, `google-search-results` |
| Web 框架 | `fastapi>=0.109.0`, `uvicorn[standard]` |
| 通知 | `lark-oapi`（飞书）, `dingtalk-stream`, `discord.py` |
| 网络 | `requests`, `httpx[socks]`, `fake-useragent`, `markdown2` |
| 报告 | `jinja2`, `imgkit` |
| 其他 | `python-dotenv`, `sqlalchemy>=2.0.0`, `schedule`, `tenacity` |

### 可选系统依赖

| 工具 | 用途 | 安装 |
|---|---|---|
| `wkhtmltopdf` | Markdown 转图片（`imgkit`） | `apt install wkhtmltopdf` / Homebrew |
| Docker + Docker Compose | 容器化部署 | 官方安装包 |

---

## 股票数据 API 密钥（按需配置）

A 股（以下均可不配置，系统自动 failover 到免费源）：

| 变量名 | 数据源 | 获取方式 |
|---|---|---|
| `TUSHARE_TOKEN` | Tushare Pro | [tushare.pro](https://tushare.pro) 注册 |
| `TICKFLOW_API_KEY` | TickFlow | TickFlow 官网 |

美股/港股（如需分析美股/港股，至少配置一个）：

| 变量名 | 数据源 | 获取方式 |
|---|---|---|
| `FINNHUB_API_KEY` | Finnhub | [finnhub.io](https://finnhub.io) 免费注册 |
| `ALPHAVANTAGE_API_KEY` | AlphaVantage | [alphavantage.co](https://www.alphavantage.co) 免费注册 |
| `LONGBRIDGE_OAUTH_CLIENT_ID` | 长桥 OpenAPI | 长桥证券 APP 开通 |

新闻搜索（至少配置一个以获得新闻分析能力）：

| 变量名 | 服务 | 获取方式 |
|---|---|---|
| `TAVILY_API_KEYS` | Tavily | [tavily.com](https://tavily.com) |
| `BRAVE_API_KEYS` | Brave Search | [api.search.brave.com](https://api.search.brave.com) |
| `SERPAPI_API_KEYS` | SerpAPI | [serpapi.com](https://serpapi.com) |

LLM（必须配置至少一个）：

| 变量名 | 说明 |
|---|---|
| `GEMINI_API_KEY` | Google Gemini（推荐，免费额度较大） |
| `OPENAI_API_KEY` | OpenAI GPT 系列 |
| `ANSPIRE_API_KEYS` | SAP Anspire / Azure OpenAI |
| `LLM_CHANNELS` | 多渠道 JSON 配置（支持任意 LiteLLM 兼容接口） |

---

## 安装步骤

### 方式一：本地 Python 运行

```bash
# 1. 克隆仓库
git clone https://github.com/ZhuLinsen/daily_stock_analysis.git
cd daily_stock_analysis

# 2. 创建并激活虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\activate         # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 复制环境变量模板
cp .env.example .env

# 5. 编辑 .env，至少填写以下必填项（见配置说明）
#    - STOCK_LIST
#    - 至少一个 LLM API Key

# 6. 首次运行（测试连通性）
python main.py --dry-run
```

### 方式二：Docker Compose

```bash
# 1. 克隆仓库
git clone https://github.com/ZhuLinsen/daily_stock_analysis.git
cd daily_stock_analysis

# 2. 复制并配置 .env
cp .env.example .env
# 编辑 .env

# 3. 构建并启动（API Server 模式）
cd docker
docker compose up -d server

# 4. 查看日志
docker compose logs -f server
```

### 方式三：仅启动前端开发服务器

```bash
cd apps/dsa-web
npm install
npm run dev
# 默认运行在 http://localhost:5173
# 需要后端 API 同时运行（http://localhost:8000）
```

---

## 配置说明

所有配置通过根目录的 `.env` 文件管理。完整变量列表见 `.env.example`（780 行注释）。

### 必填配置

```ini
# 要分析的股票代码，逗号分隔（A 股 6 位，港股 5 位，美股 Ticker）
STOCK_LIST=600519,300750,002594,00700,AAPL

# LLM 配置（选填一个即可）
GEMINI_API_KEY=your_gemini_key
# 或
OPENAI_API_KEY=your_openai_key
```

### 数据库配置

```ini
DATABASE_PATH=./data/stock_analysis.db   # SQLite 数据库路径（默认）
```

### Web API 配置

```ini
WEBUI_ENABLED=true          # 是否启动 Web API（默认 false）
WEBUI_HOST=0.0.0.0          # 监听地址（默认 127.0.0.1）
WEBUI_PORT=8000             # 监听端口（默认 8000）

# 跨域（前端分离部署时配置）
CORS_ORIGINS=http://localhost:5173,https://yourdomain.com
# 或允许所有来源
CORS_ALLOW_ALL=true
```

### 定时任务配置

```ini
SCHEDULE_ENABLED=false      # 是否启用定时任务（通过 --schedule 参数也可开启）
SCHEDULE_TIME=18:00         # 每日执行时间（支持运行时热重载）
```

### 并发配置

```ini
MAX_WORKERS=3               # 分析并发线程数（根据 API 频率限制调整）
```

### 通知渠道配置示例

```ini
# 企业微信
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx

# 飞书 Webhook
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABCdef
TELEGRAM_CHAT_ID=-100123456789

# 邮件（以 QQ 邮箱为例，系统自动识别 SMTP 配置）
EMAIL_SENDER=your@qq.com
EMAIL_PASSWORD=your_smtp_password
EMAIL_RECIPIENTS=to@example.com
```

### LLM 多渠道配置（高级）

```ini
# JSON 格式，支持多个渠道 failover
LLM_CHANNELS=[{"name":"gemini","provider":"google","api_key":"key","models":["gemini-1.5-pro"]}]
```

### 飞书 App 配置（机器人 + 自动建文档）

```ini
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_CHAT_ID=oc_xxx      # 机器人消息 chat ID（飞书 Stream Bot）
```

### 钉钉 App 配置（Stream 机器人）

```ini
DINGTALK_APP_KEY=xxxx
DINGTALK_APP_SECRET=xxxx
DINGTALK_CHAT_ID=xxxx
```

---

## 运行方法

### 单次分析

```bash
python main.py
```

### 指定股票代码

```bash
python main.py --stocks 600519 300750 AAPL
```

### 定时模式（每日自动执行）

```bash
python main.py --schedule
```

### 仅启动 Web API Server

```bash
# 方式一
python main.py --serve-only

# 方式二（等价）
python webui.py

# 方式三（uvicorn 直接启动，支持热重载）
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

### Web API + 立即执行一次分析

```bash
python main.py --serve
```

### Web API + 定时执行分析

```bash
python main.py --serve --schedule
```

### 仅执行市场综述

```bash
python main.py --market-review
```

### 回测模式

```bash
python main.py --backtest
```

### 调试模式（详细日志）

```bash
python main.py --debug
```

### 干跑模式（仅抓数据，跳过 LLM 分析）

```bash
python main.py --dry-run
```

### 强制执行（忽略非交易日检查）

```bash
python main.py --force-run
```

### 不发送通知

```bash
python main.py --no-notify
```

---

## API 接口一览

启动 Web API 后，接口前缀均为 `/api/v1/`。完整规范见 `docs/architecture/api_spec.json`。

| 模块 | 主要接口 |
|---|---|
| 分析 | `POST /analysis/analyze` 触发分析，`GET /analysis/tasks` 查询任务，`GET /analysis/tasks/stream` SSE 进度流 |
| 历史 | `GET /history` 分析历史列表，`GET /history/{id}/markdown` 完整报告 |
| 股票 | `GET /stocks/watchlist` 自选股，`GET /stocks/{code}/quote` 实时行情 |
| 回测 | `POST /backtest/run`，`GET /backtest/performance` |
| 投资组合 | `POST /portfolio/trades` 录入交易，`GET /portfolio/snapshot` 持仓快照 |
| 告警 | `POST /alerts/rules` 创建规则，`GET /alerts/triggers` 触发历史 |
| AI 聊天 | `POST /agent/chat`，`POST /agent/chat/stream` SSE 流式 |
| 系统设置 | `GET /system/config` 读取配置，`PUT /system/config` 更新配置，`POST /system/config/notification/test-channel` 测试通知渠道 |
| 健康检查 | `GET /health`，`GET /api/health` |

---

## 测试运行记录

此节留白，供实际部署后填写测试结果。

### 基础连通性测试

```bash
# 干跑（仅数据抓取，不调用 LLM）
python main.py --dry-run --stocks 600519
```

运行结果：
```
（待填写）
```

### LLM 分析测试

```bash
python main.py --stocks 600519 --no-notify --debug
```

运行结果：
```
（待填写）
```

### 通知推送测试

```bash
curl -X POST http://localhost:8000/api/v1/system/config/notification/test-channel \
  -H "Content-Type: application/json" \
  -d '{"channel": "wechat"}'
```

运行结果：
```
（待填写）
```

---

## 修改指南

### 修改自选股列表

方式一（推荐）：修改 `.env` 中的 `STOCK_LIST`，格式为逗号分隔代码：

```ini
STOCK_LIST=600519,300750,002594,00700,AAPL,TSLA
```

方式二：通过 Web API 动态添加/删除（运行时生效，写回 `.env`）：

```bash
curl -X POST http://localhost:8000/api/v1/stocks/watchlist/add \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "600036"}'
```

注意：港股用 5 位数字，美股用 Ticker 符号，A 股用 6 位数字（不含交易所前缀）。

---

### 修改分析 Prompt

文件：`src/analyzer.py`，方法：`GeminiAnalyzer._get_analysis_system_prompt()`

该方法构建完整的 System Prompt，包含：
- 市场角色设定
- 交易技能策略（`src/agent/skills/defaults.py` 中的 `CORE_TRADING_SKILL_POLICY_ZH`）
- 输出 JSON schema（`src/schemas/report_schema.py`）
- 市场阶段上下文（`src/market_phase_prompt.py`）

---

### 修改报告格式

| 报告类型 | 修改位置 |
|---|---|
| Markdown 完整报告 | `templates/report_markdown.j2` |
| 简报（Brief）格式 | `templates/report_brief.j2` |
| 企业微信格式 | `templates/report_wechat.j2` |
| 仪表盘报告逻辑 | `src/notification.py` -> `generate_dashboard_report()` |
| 单股即时推送 | `src/notification.py` -> `generate_single_stock_report()` |
| 各渠道 Markdown 转换 | `src/formatters.py` -> `format_feishu_markdown()` 等 |

---

### 修改技术指标参数

文件：`src/stock_analyzer.py`

```python
# MACD 参数（当前 12/26/9）
# 修改 _calculate_macd() 中的 EMA 周期：
df['ema12'] = df['close'].ewm(span=12, adjust=False).mean()  # 改 span 值
df['ema26'] = df['close'].ewm(span=26, adjust=False).mean()  # 改 span 值
signal_period = 9  # 改信号线周期

# RSI 周期（当前 6/12/24）
# 修改 _calculate_rsi() 中的 periods 列表：
for period in [6, 12, 24]:  # 可改为 [7, 14, 21]

# 买卖信号评分权重
# 修改 _generate_signal() 中各维度得分上限：
trend_score += 30   # 趋势权重
bias_score += 20    # 乖离权重
volume_score += 15  # 量能权重
```

---

### 新增技术指标

1. 在 `TrendAnalysisResult` dataclass（`src/stock_analyzer.py`）添加新字段
2. 在 `StockTrendAnalyzer` 新增计算方法（如 `_calculate_bollinger()`）
3. 在 `analyze()` 方法中调用新方法
4. 在 `_generate_signal()` 中引用新指标调整评分
5. 在 `src/analyzer.py` 的 prompt 构建中加入新指标描述

---

### 修改 LLM 模型

方式一（推荐）：通过 Web API 在系统设置页面配置 `LLM_CHANNELS`

方式二：直接编辑 `.env`：

```ini
# 单一模型
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-1.5-pro

# 多渠道 failover
LLM_CHANNELS=[
  {"name":"primary","provider":"google","api_key":"key1","models":["gemini-1.5-pro"]},
  {"name":"fallback","provider":"openai","api_key":"key2","models":["gpt-4o-mini"]}
]
```

LiteLLM 兼容任意 OpenAI 格式接口，可接入 Ollama 等本地模型。

---

### 新增数据源

1. 在 `data_provider/` 创建新 fetcher 文件（如 `my_fetcher.py`）
2. 继承 `BaseFetcher`（`data_provider/base.py`）
3. 实现抽象方法：
   ```python
   def _fetch_raw_data(self, stock_code, start_date, end_date) -> pd.DataFrame: ...
   def _normalize_data(self, df, stock_code) -> pd.DataFrame: ...
   ```
4. 在 `DataFetcherManager._init_default_fetchers()` 中注册，设置 `priority`

---

### 修改通知降噪规则

文件：`src/notification_noise.py`，通过 `.env` 配置：

```ini
# 最低通知级别（info/warning/error）
NOTIFICATION_MIN_SEVERITY=info

# 静默时段（不推送通知）
NOTIFICATION_QUIET_HOURS=22:00-08:00
NOTIFICATION_TIMEZONE=Asia/Shanghai

# 去重冷却（秒）
NOTIFICATION_DEDUP_TTL_SECONDS=3600
NOTIFICATION_COOLDOWN_SECONDS=300
```

---

### 运行测试

```bash
# 运行全部测试
pytest

# 仅运行单元测试
pytest -m unit

# 仅运行集成测试（需要数据源网络连接）
pytest -m integration

# 运行特定文件
pytest tests/test_stock_analyzer.py -v

# 查看覆盖率
pytest --cov=src --cov-report=html
```

---

## 相关文档

- `docs/DEPLOY.md` — 生产环境部署指南
- `docs/FAQ.md` — 常见问题
- `docs/bot-command.md` — Bot 指令列表
- `docs/full-guide.md` — 完整使用指南
- `docs/LLM_CONFIG_GUIDE.md` — LLM 配置详解
- `docs/llm-providers.md` — LLM 供应商列表
- `docs/notifications.md` — 通知渠道配置详解
- `docs/alerts.md` — 价格告警配置
- `strategies/README.md` — 选股策略 YAML 格式说明
- `docs/architecture/api_spec.json` — OpenAPI 规范

---

## 许可证

MIT License — 详见 `LICENSE` 文件。
