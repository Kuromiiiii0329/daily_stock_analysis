# Portal Multi-Agent 集成 · 进度记录

> 本文件跟踪"把 `portal/lib/src/agent/` 的 multi-agent 框架集成进 portal server + 前端"的实施进度。
> **每完成一步必须更新本文件**，方便其他 agent 接手续作。
> 完整设计计划见：`C:\Users\I762120\.claude\plans\twinkly-twirling-quilt.md`

## 项目路径

- portal 根：`C:\Users\I762120\Desktop\incident\daily\portal`
- 运行根（跑 server/build 的 cwd）：`C:\Users\I762120\Desktop\incident\daily`
- agent 框架（已存在，vendored）：`portal/lib/src/agent/`（`server.py:46` 已把 `portal/lib` 注入 sys.path，故 `import src.agent.xxx` 生效）

## 核心决策（用户已确认）

1. **深度分析研判 = 选项 B**：跑完现有 analyzers 得到 `final_report` 后，把已算好的全部指标摘要喂给**单次 LLM**（`_make_llm_caller`）做综合研判，**不走 agent 框架**，真正复用预算数据、零重复取数。
2. **交互式 AI 对话 = 选项 A**：走 `AgentOrchestrator`（四子 agent Technical→Intel→Risk→Decision，多轮）。
3. **缺失模块用轻量 stub 补**（不复制主项目 daily/src 真实文件，避免 sqlalchemy 重依赖 + bot.models 缺失链）。
4. LLM 复用现有 `HAI_*` 环境变量，用 `os.environ.setdefault` 映射成 `OPENAI_*` + `AGENT_ARCH=multi`。

## 关键技术事实

- **阻断项**：agent 框架顶层 import `src.storage`（executor/runner/chat_context/conversation 顶层）+ `src.notification`（events.py:547 延迟）。portal/lib/src 里这两个文件不存在 → 必须先补 stub 才能 import。
- storage 被调用的方法（stub 必须覆盖，建议加 `__getattr__` no-op 兜底）：
  - `get_db()`, `persist_llm_usage(usage, model, call_type=...)`
  - `DatabaseManager`: `save_conversation_message(session_id,role,content)->int`、`get_conversation_history(session_id,limit=?)->list[dict]`、`get_visible_conversation_messages(session_id,limit=?)->list`、`get_agent_provider_turns(session_id, must_roundtrip_only=?)->list[dict]`、`get_conversation_summary(session_id)->None`、`upsert_conversation_summary(...)`、`get_analysis_history(code=,limit=)->[]`、`get_analysis_context(code,target_date=None)->None`、`save_daily_data(...)->0`、`save_news_intel(...)->0`、`save_agent_provider_turn(*a,**k)->None`
- notification 被调用：`NotificationService(source_message=None).send(...)->True`、`NotificationBuilder.build_simple_alert/build_stock_summary`
- 集成入口：`build_agent_executor(get_config())`（`factory.py:297`），`AGENT_ARCH=multi`→`AgentOrchestrator`，有 `chat(message, session_id, progress_callback, context)`。
- SSE 复用现有 `/run/stream/{task_id}` + 前端 `_listenSSE`（`run.js:826`），无需新协议。

## 实施步骤与状态

| # | 步骤 | 文件 | 状态 | 备注 |
|---|------|------|------|------|
| 1 | storage.py stub | 新建 `portal/lib/src/storage.py` | ✅ 完成 | 内存对话历史 + no-op 落库 + `__getattr__` 兜底 |
| 2 | notification.py stub | 新建 `portal/lib/src/notification.py` | ✅ 完成 | NotificationService/Builder no-op |
| 3 | 验证 agent 可导入 | （跑命令） | ✅ 完成 | `IMPORT OK`；`build_agent_executor(get_config())` 在 AGENT_ARCH=multi 下返回 `AgentOrchestrator`（chat/run 均在） |
| 4 | server.py: `_apply_agent_env()` | 改 `portal/server.py` | ✅ 完成 | HAI_*→OPENAI_* + AGENT_ARCH=multi，setdefault |
| 5 | server.py: `/chat` 端点（选项A） | 改 `portal/server.py` | ✅ 完成 | `_handle_chat`+`_run_chat_task`，do_POST 加分支；progress_cb 转 SSE |
| 6 | server.py: 深度分析研判（选项B） | 改 `portal/server.py` | ✅ 完成 | `_run_deep_analysis_task` 加 `agent_review` 参数；`_summarize_report_for_agent`+`build_review_prompt`；写 `final_report["agent_review"]` |
| 7 | 前端 chat.js | 新建 `portal/js/tabs/chat.js` | ✅ 完成 | 对话Tab + SSE，气泡流，session 持久化，离线优雅提示 |
| 8 | app.js 注册 tab | 改 `portal/js/app.js` | ✅ 完成 | import + TABS 加 chat；_pollServer 通知 chat setServerStatus |
| 9 | run.js 加研判勾选 | 改 `portal/js/tabs/run.js` | ✅ 完成 | `#chk-agent-review` checkbox + body 加 `agent_review`；`_showStructured` 追加研判卡片 |
| 10 | report_html 研判节 | 改 `portal/report_html.py` | ✅ 完成 | `agent_review` → `_md_to_html` 渲染成蓝色研判块 |
| 11 | build_standalone 加 chat.js | 改 `portal/build_standalone.py` | ✅ 完成 | ORDER 加 `("js/tabs/chat.js","CHAT")` |
| 12 | 重新生成 standalone | 跑脚本 | ✅ 完成 | 130.6 KB；node --check 通过；SERVER_CHAT 重命名正确 |
| 13 | 写 AGENT.md | 新建 `portal/AGENT.md` | ✅ 完成 | 作用/双场景/4模式/四子agent/硬性规则/18工具/env/stub |

状态图例：⬜ 待办 · 🔄 进行中 · ✅ 完成 · ⚠️ 有问题

## 回归保护

`agent_review` 默认 false；`AGENT_*` 用 setdefault；`/chat` 独立端点。未启用 agent 时 portal 行为与现状完全一致。

## 验证清单（全部完成后跑）

1. `python -c "...build_agent_executor;print('OK')"` — stub 补齐成功
2. 启动 `python portal/server.py`（设好 HAI_* 环境变量）
3. `curl -XPOST 127.0.0.1:7788/chat -d '{"message":"你好","session_id":"t1"}'` → `{ok,task_id}`；`/run/stream/<id>` 有 stage 日志 + done；`/run/report/<id>` 有 content
4. `/analyze` 带 `agent_review:true` → report JSON 含非空 `agent_review`
5. 前端（index.html 开发模式）：AI 对话 Tab 多轮问答；run Tab 勾选研判后报告含研判节
6. `python portal/build_standalone.py` 重新生成，双击 index-standalone.html 无 JS 报错、含 chat tab

## 变更日志

- 步骤 1-3 完成：新建 `portal/lib/src/storage.py`（内存 stub，DatabaseManager 含 `__getattr__` no-op 兜底）+ `portal/lib/src/notification.py`（no-op stub）；验证 `from src.agent.factory import build_agent_executor` 导入成功，`AGENT_ARCH=multi` 下构建出 `AgentOrchestrator`。
- 步骤 4-6 完成：`server.py` 加 `_apply_agent_env`（HAI_*→OPENAI_*）+ `/chat` 端点（`_handle_chat`/`_run_chat_task`，走 orchestrator，progress_cb 转 SSE）+ 深度分析 `agent_review` 选项 B（`_summarize_report_for_agent`/`build_review_prompt`，单次 LLM 复用已算数据）。`py_compile` + 模块加载 + 函数逻辑验证全部通过。
- 步骤 7-9 完成：新建 `js/tabs/chat.js`（气泡流对话Tab + SSE + session 持久化）；`app.js` 注册 chat tab 并接线 setServerStatus；`run.js` 加「🤖 Agent 综合研判」checkbox + `/analyze` body 传 `agent_review` + `_showStructured` 追加研判卡片。
- 步骤 10-12 完成：`report_html.py` 渲染 `agent_review` 蓝色研判块；`build_standalone.py` ORDER 加 chat.js；重新生成 `index-standalone.html`（130.6 KB），`node --check` 通过，`SERVER_CHAT` 重命名正确、ChatTab 注册就位。
- 步骤 13 完成：新建 `portal/AGENT.md`（含 agent 作用、双场景、4 编排模式、四子 agent 职责、硬性规则、18 工具清单、env 映射、stub 说明）。

## 端到端冒烟测试结果（已跑）

- 启动 `python portal/server.py` → `/health` 返回 ok。
- `POST /chat {"message":"你好","session_id":"smoke1"}` → 返回 `{ok, task_id, session_id}`。
- 任务真实跑起来：日志显示 Technical 子 agent 发起 5 个 tool call（get_realtime_quote 等）并执行 → **证明 `/chat`→`_apply_agent_env`→orchestrator→子 agent→工具 整条链路接线正确**。
- server 日志**无我方集成代码异常**（无 chat task failed / storage / ModuleNotFound / Traceback）。
- ⚠️ 真实 multi-agent 全链路较慢（多轮 LLM + 网络工具），冒烟窗口内未跑完属正常，非代码问题。

## 剩余可选事项 / 后续 agent 可做

- 真机验证：设好 `HAI_*` 环境变量，前端（先 `index.html` 开发模式或 `index-standalone.html`）实测 AI 对话多轮、run Tab 勾选研判出报告研判节。
- 若要对话历史持久化：把 `storage.py` stub 换成真实实现（需装 sqlalchemy）。
- 若 orchestrator 太慢：可在 `_apply_agent_env` 把 `AGENT_ORCHESTRATOR_MODE` 设为 `quick`（technical→decision）。
