# 股票分析 Portal — 功能 Proposal 报告

> 生成日期：2026-08-16

---

## 1. 现状总结

当前 portal 整体完成度约 **75%**，技术指标主流水平已达（MA/MACD/RSI/KDJ/布林带全齐），基本面依托 akshare 真实数据，多维度加权评分架构合理。但存在三类系统性问题：

- **部分核心模块是 LLM 占位符**：pattern/wave/chan/business 评分恒定输出 50 分，与最终结论完全脱节
- **并发安全设计缺失**：缓存无锁、任务无上限、subprocess 无超时
- **用户体验断层**：首次使用无引导、服务离线无说明、报告缺图

离生产可用还差若干关键修复。

---

## 2. 已实现功能矩阵

| 模块 | 功能 | 完成度 |
|---|---|---|
| server.py | HTTP 路由 / SSE 推流 / /analyze 深度分析链路 | ✅ 完整 |
| server.py | LLM 多模型优先级选择 / /env 白名单读写 | ✅ 完整 |
| server.py | report_type 字段同步（/run 路径） | ⚠️ 占位（永远返回默认值） |
| server.py | 任务并发控制 / subprocess 超时 | ❌ 缺失 |
| server.py | HTTPServer 多线程 | ❌ 单线程（SSE 期间其他请求全阻塞） |
| technical.py | MA/MACD/RSI/KDJ/布林带/量能计算+评分+金叉检测 | ✅ 完整 |
| technical.py | pattern 形态 / wave 波浪 / chan 缠论 | ⚠️ LLM 占位，评分恒 50 |
| fundamental.py | 营收/净利/ROE/毛利率/增速/股息/估值 | ✅ 完整 |
| fundamental.py | PE<0 亏损股估值 | ❌ Bug（误判为低估值加分） |
| fundamental.py | 业务描述（LLM） | ⚠️ 占位，评分恒 50 |
| merger.py | 三维度加权评分 + LLM 综合结论 + 规则降级 | ✅ 完整 |
| merger.py | SIGNAL_PRIORITY 优先级逻辑 | ⚠️ 定义了但从未使用 |
| data_cache.py | K线增量缓存 / 商品搜索缓存+TTL | ✅ 完整 |
| data_cache.py | 并发写入保护 / 节假日TTL | ❌ 缺失 |
| send_report.py | 多报告合并发送 | ✅ 完整 |
| send_report.py | Markdown→HTML 转换 / 邮件重试 | ❌ 缺失 |
| GitHub Actions | 定时触发 / artifacts / 多LLM源 | ✅ 完整 |
| GitHub Actions | 失败告警通知 / retry | ❌ 缺失 |
| portal UI | 四 Tab 布局 / 三子面板 Run / API配置 | ✅ 完整 |
| portal UI | 首次使用引导 / K线图 / 雷达图 / 离线说明 | ❌ 缺失 |

---

## 3. 功能缺口 Proposal

### 3.1 P0 — 代码 Bug（必须先修）

| # | 问题 | 文件 | 影响 |
|---|---|---|---|
| B1 | **HTTPServer 单线程**：SSE 长连接期间所有请求阻塞，/run/status 轮询全失效 | server.py | 致命 |
| B2 | **PE<0 评分逻辑错误**：亏损股被判为"低估值"加分 | fundamental.py | 误导决策 |
| B3 | **data_cache 无锁**：并发分析时 CSV 读写竞争，数据损坏 | data_cache.py | 数据可靠性 |
| B4 | **subprocess 无超时**：main.py 挂死时 SSE 永不关闭，线程泄漏 | server.py | 资源耗尽 |
| B5 | **任务无上限**：连续触发 /analyze 不断开新线程 | server.py | 资源耗尽 |
| B6 | **send_report.py 无 MD→HTML 转换**：邮件正文是裸 Markdown，完全不可读 | send_report.py | 核心功能失效 |

### 3.2 P0 — 技术指标（高价值、低成本）

| 功能 | 为什么需要 | 复杂度 |
|---|---|---|
| **筹码分布** | A 股判断阻力位最直接依据，akshare 已有接口 `stock_cyq_em()` | 中 |
| **换手率趋势（近30日）** | 区分"低换手吸筹"和"高换手出货"，K线原始数据已有 | 低 |
| **融资融券余额趋势** | 杠杆资金动向，中小盘最有效多空信号之一 | 中 |
| **北向资金（陆股通）** | 外资独立指标，历史上持续流出领先大盘调整1-3天 | 中 |
| **大股东减持公告** | 最强内部人卖出信号，当前基本面完全未覆盖 | 中 |

### 3.3 P1 — 分析质量提升

| 功能 | 为什么需要 | 复杂度 |
|---|---|---|
| **PEG 指标** | PE 和增速已分别实现却割裂，PE=50 但增速80% 当前被判为"高估"是典型误判 | 低 |
| **商誉减值风险** | A 股年报季系统性风险，财报数据已有商誉科目，新增字段提取即可 | 低 |
| **ROE 近5年趋势** | 单期高 ROE 可能是一次性收益，时序稳定性才是护城河 | 中 |
| **行业平均 PE 参照** | 绝对值估值判断对高成长行业系统性误判，半导体 PE=80 不等于高估 | 中 |
| **RSI 改 Wilder 平滑** | 当前用 rolling mean，结果与同花顺/东财不一致，用户交叉验证时困惑 | 低 |
| **OBV 能量潮** | 识别"价格横盘+资金悄悄建仓"，三行代码 | 低 |
| **MACD 背驰算法化** | 当前缠论靠 LLM 判断背驰，可靠性差，算法实现更稳定 | 中 |

### 3.4 P1 — 系统可靠性

| 功能 | 为什么需要 | 复杂度 |
|---|---|---|
| **任务状态持久化** | 进程崩溃后任务状态全消失，客户端永久轮询 | 中 |
| **节假日 TTL 修正** | 当前只判断周末，春节/国庆按盘中2小时TTL频繁重复请求 | 低 |
| **邮件发送重试** | SMTP 超时是高频临时错误，当前直接 exit(1) 报告丢失 | 低 |
| **分析失败告警邮件** | reports/ 为空时静默退出，用户不知当天失败 | 低 |
| **GitHub Actions retry** | 网络抖动直接失败整个 job，每次手动重跑 | 低 |
| **数据源备用切换** | akshare 个人维护，接口随时失效，当前无备用源 | 高 |

### 3.5 P1 — 前端 UX

| 功能 | 为什么需要 | 复杂度 |
|---|---|---|
| **首次使用引导 banner** | 页面打开按钮全 disabled，用户不知道下一步（添加股票→配置Key→运行） | 低 |
| **服务离线内联说明** | toast 需要点按钮触发，但按钮 disabled，等于零提示 | 低 |
| **报告错误状态卡片** | `_loadReport` 的 catch 完全静默，失败时报告面板空白 | 低 |
| **报告内嵌价格折线图** | 技术面结论没有配套 K 线图，用户无法建立直觉（ECharts CDN） | 中 |
| **维度雷达图** | 三个独立圆环视觉割裂，雷达图是多维评分的行业标准展示方式 | 低 |
| **自选股显示名称+涨跌幅** | 列表只显示 `002466`，无法直接判断是哪只股票 | 中 |
| **历史报告 localStorage 缓存** | 服务关闭后历史全失效，失去对比价值 | 低 |

### 3.6 P2 — 长期高级功能

| 功能 | 说明 |
|---|---|
| **回测引擎** | 验证当前评分参数（RSI<30 加15分等）的历史胜率 |
| **机构持仓季度变化** | 公募重仓变化，中线强信号（akshare `fund_portfolio_hold_em()`，有3个月延迟） |
| **龙虎榜数据** | 游资/机构席位识别，短线信号 |
| **大宗商品实时价格接入** | 产业链分析从 LLM 猜价格改为传入碳酸锂/螺纹钢实时数据 |
| **watchlist 个股差异化配置** | schema 迁移，支持每只股票独立配置分析深度 |
| **移动端 Tab 布局修复** | nav `max-w-xs` 小屏截断，guide `grid-cols-2` 在 375px 碎块 |

---

## 4. 分阶段路线图

### Phase 1（第1-2周）：让现有功能正确运行

| # | 任务 | 文件 | 预估工时 |
|---|---|---|---|
| 1 | HTTPServer → ThreadingHTTPServer | server.py | 0.5h |
| 2 | PE<0 评分逻辑 bug | fundamental.py | 0.5h |
| 3 | data_cache 并发写入加锁 | data_cache.py | 2h |
| 4 | subprocess 添加 timeout | server.py | 0.5h |
| 5 | 任务并发上限（返回 429） | server.py | 1h |
| 6 | send_report.py 内置 Markdown→HTML | send_report.py | 2h |
| 7 | /run 路径 report_type 字段同步 | server.py | 0.5h |
| 8 | 前端服务离线内联说明 | portal/js | 1h |
| 9 | 前端错误状态卡片（catch 块） | portal/js | 1h |
| 10 | 首次使用引导 banner | portal/js | 1.5h |
| 11 | CORS 限制到 localhost | server.py | 0.5h |
| 12 | stock_code 白名单校验（安全） | server.py | 1h |
| 13 | 任务 ID 改 `secrets.token_hex(16)` | server.py | 0.1h |

**目标**：所有已有功能正确运行，不再因为已知 Bug 产生误导性数据。

---

### Phase 2（第2-4周）：补齐关键投资信号

| # | 任务 | 预估工时 |
|---|---|---|
| 1 | 筹码分布分析模块 | 8h |
| 2 | 换手率趋势（近30日） | 2h |
| 3 | 融资融券余额趋势 | 4h |
| 4 | 北向资金（陆股通） | 4h |
| 5 | 大股东减持公告 | 6h |
| 6 | PEG 指标（PE+增速联动） | 1h |
| 7 | 商誉减值风险提示 | 2h |
| 8 | ROE 近5年趋势 | 3h |
| 9 | RSI 改 Wilder 平滑 | 1h |
| 10 | OBV 能量潮 | 1h |
| 11 | 报告内嵌价格折线图（ECharts） | 6h |
| 12 | 维度雷达图替换圆环 | 3h |
| 13 | 自选股列表显示名称+涨跌幅 | 4h |
| 14 | 历史报告 localStorage 缓存 | 3h |
| 15 | 邮件发送重试+失败告警邮件 | 2h |
| 16 | GitHub Actions retry 机制 | 1h |
| 17 | watchlist.json schema 校验 | 2h |
| 18 | 任务状态持久化（JSON 文件） | 4h |
| 19 | 节假日 TTL 修正（chinese_calendar） | 1h |

**目标**：补齐 A 股机构视角核心信号，报告具备真实投资参考价值。

---

### Phase 3（1-3个月）：专业化

| # | 任务 | 说明 |
|---|---|---|
| 1 | 回测引擎 | 验证评分参数有效性，输出胜率统计 |
| 2 | MACD 背驰算法检测 | 替换 LLM 做数值背驰，纳入评分 |
| 3 | 机构持仓季度变化 | 公募重仓变化作为中线信号 |
| 4 | 龙虎榜数据集成 | 游资/机构席位识别 |
| 5 | 大宗商品实时价格（产业链） | 产业链分析从猜价格改为传入实时数据 |
| 6 | watchlist 个股差异化配置 | schema 迁移，支持每只股票独立配置 |
| 7 | 数据源备用切换（baostock/tushare） | 解决 akshare 单点依赖问题 |
| 8 | 报告导出（Markdown/图片） | html2canvas 或打印样式 |
| 9 | GitHub Actions Step Summary | Actions 列表页面显示分析摘要 |

---

## 5. 技术债务清单

| 优先级 | 问题 | 位置 | 重构方案 |
|---|---|---|---|
| 高 | `_tasks` 字典只增不减，内存泄漏 | server.py | 后台定时器清理超过6小时的已完成任务 |
| 高 | LLM 异常被 `except: pass` 完全吞掉，无法排查 | merger.py | 改为 `except Exception as e: logger.warning(...)` |
| 高 | pattern/wave/chan 评分恒50却在模块列表中，误导用户 | technical.py | 从 DEFAULT_MODULES 中移除，或实现真实评分 |
| 中 | SSE while True 无最大等待时长，连接永不超时 | server.py | 增加10分钟上限，超时主动发 done 事件 |
| 中 | `get_kline_last_date` 全表扫描 O(n) | data_cache.py | CSV 已按 date 排序，直接读最后一行 O(1) |
| 中 | `_make_search_fn` 导入失败静默跳过 | server.py | 启动时记录 warning，让用户知晓搜索是否可用 |
| 低 | `SIGNAL_PRIORITY` 定义但从未使用 | merger.py | 实现信号优先级逻辑或删除死代码 |
| 低 | 缺失列填 `0.0` 而非 `NaN` | data_cache.py | 改为 NaN，避免下游误用0值做均线计算 |

---

## 6. 安全加固（暴露到公网前必做）

| 风险级别 | 问题 | 修复方案 | 工时 |
|---|---|---|---|
| **高危** | `Access-Control-Allow-Origin: *`，任意网站可 CSRF 读写 /env 的 API key | 限制为 `http://localhost:7788` | 30min |
| **高危** | /env 和 /save 接口无认证，任何人可覆盖 API key 和配置 | 启动时生成随机 token，每次请求带 Authorization header | 2-3h |
| **高危** | stock_code 未校验直接拼入 subprocess 命令 | 正则 `^\d{6}$` 过滤，不合格返回 400 | 1h |
| **中危** | 任务 ID 用 `uuid4().hex[:12]`（48位），可被暴力枚举 | 改用 `secrets.token_hex(16)`（128位熵） | 5min |
| **中危** | 无速率限制，任意请求耗尽线程和内存 | 基于 IP 的频率限制，/analyze 每分钟最多3次，超出返回 429 | 2-3h |
| **低危** | GET /health 返回完整本地路径，泄露目录结构 | 只返回 `{"ok": true, "version": "..."}` | 10min |
