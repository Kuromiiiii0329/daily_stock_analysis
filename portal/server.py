#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portal/server.py — 本地配置写入 + 分析触发服务

接口：
  GET  /health          健康检查
  POST /save            写入 config/watchlist.json
  POST /run             启动分析任务，返回 task_id
  GET  /run/status/<id> 查询任务状态 + 日志
  GET  /run/stream/<id> SSE 实时推送日志
  GET  /run/report/<id> 获取分析完成的报告内容

启动：
    cd C:\\Users\\I762120\\Desktop\\incident\\daily
    python portal/server.py
"""
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORTAL_SERVER_PORT", 7788))

ALLOWED_ENV_KEYS = {
    "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LITELLM_MODEL",
    "HAI_BASE_URL", "HAI_API_KEY", "HAI_MODEL",
    "TUSHARE_TOKEN", "BOCHA_API_KEYS", "TAVILY_API_KEYS", "SERPAPI_API_KEYS",
    "EMAIL_SENDER", "EMAIL_PASSWORD", "EMAIL_RECEIVERS", "EMAIL_SENDER_NAME",
}
PORTAL_DIR   = Path(__file__).parent          # portal/
PROJECT_ROOT = PORTAL_DIR.parent              # daily/  (may not exist if running standalone)
LIB_DIR      = PORTAL_DIR / "lib"             # portal/lib/ — bundled dependencies
CONFIG_PATH  = PORTAL_DIR.parent / "config" / "watchlist.json"
TZ_CN = timezone(timedelta(hours=8))

# ── 路径注入：portal/lib/ 优先，其次项目根（向后兼容）─────────
for _p in [str(LIB_DIR), str(PROJECT_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── 压制第三方库噪音日志 ───────────────────────────────────
# litellm 在解析 provider/model 时会打印 "Provider List: https://..." 等对用户无用的行，
# 统一把 litellm 及其底层 httpx/openai 日志级别调到 ERROR，只保留真正的错误。
for _noisy in ("LiteLLM", "litellm", "httpx", "httpcore", "openai"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)
try:
    import os as _os
    _os.environ.setdefault("LITELLM_LOG", "ERROR")  # litellm 新版用此环境变量控制日志
except Exception:
    pass

# ── 任务管理 ────────────────────────────────────────────────
# task_id -> {"status": "running"|"done"|"error", "logs": [...], "report": str, "started_at": ...}
_tasks: dict = {}
_tasks_lock = threading.Lock()


def _run_analysis_task(task_id: str, cmd: list, env: dict):
    """在后台线程中运行分析命令，收集日志。"""
    with _tasks_lock:
        _tasks[task_id]["status"] = "running"
        _tasks[task_id]["started_at"] = datetime.now(TZ_CN).isoformat()

    logs = _tasks[task_id]["logs"]
    logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] 🚀 启动分析...")
    logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] 命令: {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(PROJECT_ROOT),
        )
        with _tasks_lock:
            _tasks[task_id]["pid"] = proc.pid

        for line in proc.stdout:
            line = line.rstrip()
            if line:
                logs.append(line)

        proc.wait()
        exit_code = proc.returncode

        # 分析完成后读取报告文件
        date_str = datetime.now(TZ_CN).strftime("%Y%m%d")
        report_parts = []
        for fname in [f"market_review_{date_str}.md", f"report_{date_str}.md"]:
            fpath = PROJECT_ROOT / "reports" / fname
            if fpath.exists():
                report_parts.append(fpath.read_text(encoding="utf-8"))

        report = "\n\n---\n\n".join(report_parts) if report_parts else ""

        with _tasks_lock:
            _tasks[task_id]["status"] = "done" if exit_code == 0 else "error"
            _tasks[task_id]["exit_code"] = exit_code
            _tasks[task_id]["report"] = report
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()

        emoji = "✅" if exit_code == 0 else "❌"
        logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] {emoji} 分析完成，退出码: {exit_code}")
        if report:
            logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] 📄 报告已生成")
        else:
            logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] ⚠️  未找到报告文件（可能是非交易日）")

    except Exception as e:
        logger.exception("任务 %s 执行异常: %s", task_id, e)
        logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] ❌ 异常: {e}")
        with _tasks_lock:
            _tasks[task_id]["status"] = "error"
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()


# ── HTTP Handler ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        logger.info("%s %s", self.address_string(), format % args)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, code: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass  # 客户端已断开（Windows 常见，属正常现象）

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/health", "/"):
            self._send_json(200, {"ok": True, "config_path": str(CONFIG_PATH)})

        elif path == "/env":
            self._handle_get_env()

        elif path == "/quote":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            self._handle_quote(qs)

        elif path == "/open_report":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            code = (qs.get("code", [""])[0]).strip()
            self._handle_open_report(code)

        elif path == "/backtest":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            code = (qs.get("code", [""])[0]).strip().upper()
            self._handle_backtest(code)

        elif path.startswith("/run/stream/"):
            task_id = path.split("/run/stream/")[-1]
            self._handle_sse(task_id)

        elif path.startswith("/run/status/"):
            task_id = path.split("/run/status/")[-1]
            with _tasks_lock:
                task = _tasks.get(task_id)
            if not task:
                self._send_json(404, {"ok": False, "error": "task not found"})
                return
            self._send_json(200, {
                "ok": True,
                "task_id": task_id,
                "status": task["status"],
                "log_count": len(task["logs"]),
                "has_report": bool(task.get("report")),
            })

        elif path.startswith("/run/report/"):
            task_id = path.split("/run/report/")[-1]
            with _tasks_lock:
                task = _tasks.get(task_id)
            if not task:
                self._send_json(404, {"ok": False, "error": "task not found"})
                return
            self._send_json(200, {
                "ok": True,
                "status": task["status"],
                "report": task.get("report", ""),
                "logs": task.get("logs", []),
            })

        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except Exception as e:
            self._send_json(400, {"ok": False, "error": f"invalid JSON: {e}"})
            return

        if path == "/save":
            self._handle_save(payload)
        elif path == "/run":
            self._handle_run(payload)
        elif path == "/analyze":
            self._handle_analyze(payload)
        elif path == "/chat":
            self._handle_chat(payload)
        elif path == "/market_review":
            self._handle_market_review(payload)
        elif path == "/env":
            self._handle_set_env(payload)
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def _handle_save(self, payload: dict):
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("✅ 写入 %s  股票: %s", CONFIG_PATH, payload.get("stock_list", []))
            self._send_json(200, {"ok": True, "path": str(CONFIG_PATH)})
        except Exception as e:
            logger.error("写入失败: %s", e)
            self._send_json(500, {"ok": False, "error": str(e)})

    def _handle_run(self, payload: dict):
        """
        payload 字段：
          mode: "stock" | "market" | "full"
          stocks: ["600519", "300750"]  (mode=stock 时使用)
          dry_run: bool
          force_run: bool
          report_type: "simple" | "full" | "brief"
          no_notify: bool  (默认 true)
        """
        mode = payload.get("mode", "stock")
        stocks = payload.get("stocks", [])
        dry_run = payload.get("dry_run", False)
        force_run = payload.get("force_run", False)
        report_type = payload.get("report_type", "simple")
        no_notify = payload.get("no_notify", True)

        # 构建命令
        cmd = [sys.executable, "main.py"]

        if mode == "market":
            cmd.append("--market-review")
        elif mode == "stock" and stocks:
            cmd += ["--stocks", ",".join(stocks), "--no-market-review"]
        # mode == "full": 不加任何模式参数，使用 config/watchlist.json 或 STOCK_LIST

        if dry_run:
            cmd.append("--dry-run")
        if force_run:
            cmd.append("--force-run")
        if no_notify:
            cmd.append("--no-notify")

        # 构建环境变量（继承当前进程 + 读取 .env 文件）
        env = os.environ.copy()
        env["REPORT_TYPE"] = report_type
        env["PYTHONUNBUFFERED"] = "1"  # 确保实时输出日志
        # Windows 子进程默认 cp1252，写中文日志会 UnicodeEncodeError；强制 UTF-8
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        # 如果 mode=stock 且有 stocks，也通过环境变量注入
        if mode == "stock" and stocks:
            env["STOCK_LIST"] = ",".join(stocks)

        # 读取项目 .env 文件（如果存在）
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in env:  # 不覆盖已有环境变量
                            env[k] = v
            except Exception:
                pass

        task_id = uuid.uuid4().hex[:12]
        with _tasks_lock:
            _tasks[task_id] = {
                "status": "pending",
                "logs": [],
                "report": "",
                "pid": None,
                "created_at": datetime.now(TZ_CN).isoformat(),
                "mode": mode,
                "stocks": stocks,
            }

        thread = threading.Thread(
            target=_run_analysis_task,
            args=(task_id, cmd, env),
            daemon=True,
        )
        thread.start()
        logger.info("任务 %s 已启动: mode=%s stocks=%s", task_id, mode, stocks)
        self._send_json(200, {"ok": True, "task_id": task_id})

    def _handle_sse(self, task_id: str):
        """Server-Sent Events：实时推送日志行。"""
        with _tasks_lock:
            if task_id not in _tasks:
                self._send_json(404, {"ok": False, "error": "task not found"})
                return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        sent = 0
        try:
            while True:
                with _tasks_lock:
                    task = _tasks.get(task_id, {})
                    logs = task.get("logs", [])
                    status = task.get("status", "pending")

                # 推送新日志行
                while sent < len(logs):
                    line = logs[sent].replace("\n", " ")
                    data = f"data: {json.dumps({'log': line, 'status': status}, ensure_ascii=False)}\n\n"
                    self.wfile.write(data.encode("utf-8"))
                    self.wfile.flush()
                    sent += 1

                # 任务结束则推送 done 事件并断开
                if status in ("done", "error"):
                    with _tasks_lock:
                        report = _tasks[task_id].get("report", "")
                        report_type = _tasks[task_id].get("report_type", "markdown")
                    done_data = json.dumps({
                        "status": status,
                        "has_report": bool(report),
                        "report_type": report_type,
                    }, ensure_ascii=False)
                    self.wfile.write(f"event: done\ndata: {done_data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    break

                time.sleep(0.3)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # 客户端断开，正常退出

    def _handle_analyze(self, payload: dict):
        """
        深度双维度分析接口。

        payload:
          stock_code:  str
          stock_name:  str (可选，自动查询)
          dimensions:  list[str]  默认 ["technical","fundamental","industry"]
          modules:     dict       各维度启用的子模块
        """
        stock_code = payload.get("stock_code", "").strip().upper()
        if not stock_code:
            self._send_json(400, {"ok": False, "error": "stock_code 不能为空"})
            return

        stock_name  = payload.get("stock_name", stock_code)
        dimensions  = payload.get("dimensions", ["technical", "fundamental", "industry"])
        modules_map = payload.get("modules", {})
        llm_mode    = payload.get("llm_mode", "batch")       # batch | per_indicator
        open_report = payload.get("open_report", True)
        agent_review = bool(payload.get("agent_review", False))  # 🤖 Agent 综合研判（选项B）

        task_id = uuid.uuid4().hex[:12]
        with _tasks_lock:
            _tasks[task_id] = {
                "status":     "pending",
                "logs":       [],
                "report":     "",
                "report_type": "structured",   # 区别于 /run 的 markdown 报告
                "pid":        None,
                "created_at": datetime.now(TZ_CN).isoformat(),
                "stock_code": stock_code,
                "stock_name": stock_name,
                "task_kind":  "analyze",
            }

        thread = threading.Thread(
            target=_run_deep_analysis_task,
            args=(task_id, stock_code, stock_name, dimensions, modules_map, llm_mode, open_report, agent_review),
            daemon=True,
        )
        thread.start()
        logger.info("深度分析任务 %s 已启动: %s (%s)", task_id, stock_name, stock_code)
        self._send_json(200, {"ok": True, "task_id": task_id})

    def _handle_chat(self, payload: dict):
        """POST /chat — 交互式 AI 对话（选项A：走 AgentOrchestrator 四子 agent 多轮）。

        payload:
          message:     str   用户提问（必填）
          session_id:  str   前端生成并持久化（localStorage），用于多轮上下文
          stock_code:  str   可选，带上则注入股票上下文
          stock_name:  str   可选
        """
        message = (payload.get("message") or "").strip()
        if not message:
            self._send_json(400, {"ok": False, "error": "message 不能为空"})
            return

        session_id = (payload.get("session_id") or "").strip() or ("sess_" + uuid.uuid4().hex[:12])
        stock_code = (payload.get("stock_code") or "").strip().upper()
        stock_name = payload.get("stock_name", stock_code)

        task_id = uuid.uuid4().hex[:12]
        with _tasks_lock:
            _tasks[task_id] = {
                "status":      "pending",
                "logs":        [],
                "report":      "",
                "report_type": "chat",
                "pid":         None,
                "created_at":  datetime.now(TZ_CN).isoformat(),
                "session_id":  session_id,
                "task_kind":   "chat",
            }

        thread = threading.Thread(
            target=_run_chat_task,
            args=(task_id, message, session_id, stock_code, stock_name),
            daemon=True,
        )
        thread.start()
        logger.info("AI 对话任务 %s 已启动: session=%s", task_id, session_id)
        self._send_json(200, {"ok": True, "task_id": task_id, "session_id": session_id})

    def _handle_market_review(self, payload: dict):
        """POST /market_review — 大盘复盘（上证+创业板），生成一份 HTML 并打开。"""
        open_report = payload.get("open_report", True)
        task_id = uuid.uuid4().hex[:12]
        with _tasks_lock:
            _tasks[task_id] = {
                "status":      "pending",
                "logs":        [],
                "report":      "",
                "report_type": "market",
                "pid":         None,
                "created_at":  datetime.now(TZ_CN).isoformat(),
                "task_kind":   "market",
            }
        thread = threading.Thread(
            target=_run_market_review_task,
            args=(task_id, open_report),
            daemon=True,
        )
        thread.start()
        logger.info("大盘复盘任务 %s 已启动", task_id)
        self._send_json(200, {"ok": True, "task_id": task_id})

    def _handle_open_report(self, code: str):
        """GET /open_report?code=xxx — 按 code 找最新 HTML 报告并用浏览器打开。"""
        try:
            from portal.report_html import find_latest_stock_html, find_latest_market_html, open_in_browser
            if code == "__market__" or not code:
                path = find_latest_market_html()
            else:
                path = find_latest_stock_html(code.upper())
            if not path or not path.exists():
                self._send_json(404, {"ok": False, "error": "未找到该报告的 HTML，请重新分析生成"})
                return
            opened = open_in_browser(path)
            self._send_json(200, {"ok": True, "path": str(path), "opened": opened})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)})

    def _handle_get_env(self):
        """GET /env — 返回 .env 中已配置的白名单 key，value 用星号掩码（前3后3）。"""
        env_file = PROJECT_ROOT / ".env"
        configured = {}
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k in ALLOWED_ENV_KEYS and v:
                            if len(v) <= 6:
                                masked = "*" * len(v)
                            else:
                                masked = v[:3] + "*" * (len(v) - 6) + v[-3:]
                            configured[k] = masked
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
                return
        self._send_json(200, {"ok": True, "env": configured})

    def _handle_set_env(self, payload: dict):
        """POST /env — 写入白名单内的 key 到 .env 文件，保留注释，立即生效。

        兼容两种 payload 格式：
          - {"env": {"KEY": "value", ...}}   （旧格式，带 env 包装）
          - {"KEY": "value", ...}             （前端 settings.js 实际发送的格式）
        """
        updates = payload.get("env", payload)
        if not isinstance(updates, dict):
            self._send_json(400, {"ok": False, "error": "env 字段必须是对象"})
            return

        # 只保留白名单内的 key
        safe_updates = {k: v for k, v in updates.items() if k in ALLOWED_ENV_KEYS}
        if not safe_updates:
            self._send_json(200, {"ok": True, "updated": []})
            return

        env_file = PROJECT_ROOT / ".env"
        # 读取现有内容
        if env_file.exists():
            lines = env_file.read_text(encoding="utf-8").splitlines()
        else:
            lines = []

        # 对每个要更新的 key，在已有行中替换，记录哪些已更新
        updated_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, _ = stripped.partition("=")
                k = k.strip()
                if k in safe_updates:
                    new_lines.append(f'{k}={safe_updates[k]}')
                    updated_keys.add(k)
                    continue
            new_lines.append(line)

        # 追加尚未出现的 key
        for k, v in safe_updates.items():
            if k not in updated_keys:
                new_lines.append(f'{k}={v}')

        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        # 立即生效：更新当前进程环境变量
        _load_dotenv()
        # 强制覆盖（_load_dotenv 不覆盖已有值，新写入的要强制更新）
        for k, v in safe_updates.items():
            os.environ[k] = v

        logger.info("✅ .env 已更新，keys: %s", list(safe_updates.keys()))
        self._send_json(200, {"ok": True, "updated": list(safe_updates.keys())})

    def _handle_quote(self, params: dict):
        """GET /quote?codes=600519,300750 — 返回股票实时行情（名称、现价、涨跌幅）。"""
        codes_raw = params.get("codes", [""])[0]
        codes = [c.strip() for c in codes_raw.split(",") if c.strip()]
        if not codes:
            self._send_json(400, {"ok": False, "error": "缺少 codes 参数"})
            return

        result = []

        # ── 优先：efinance get_base_info（内网稳定，返回名称+行业）──
        # 东财 spot（akshare stock_zh_a_spot_em）在部分内网返回乱码/不通，
        # efinance 个股基本信息接口更稳，用于可靠拿到股票名称。
        try:
            import efinance as ef
            ef_ok = False
            for code in codes:
                name = None; price = None; pct = None
                try:
                    info = ef.stock.get_base_info(code)
                    # 单只返回 Series，可取"股票名称"
                    if info is not None and hasattr(info, "get"):
                        nm = info.get("股票名称")
                        if nm and str(nm) not in ("nan", "-", ""):
                            name = str(nm); ef_ok = True
                except Exception:
                    pass
                result.append({"code": code, "name": name, "price": price,
                               "pct_chg": pct, "volume": None})
            if ef_ok:
                self._send_json(200, {"ok": True, "quotes": result})
                return
            result = []  # efinance 全空，落到 akshare 分支
        except Exception as e:
            logger.info("efinance 查名不可用，降级 akshare: %s", str(e)[:60])
            result = []

        # ── 次选：akshare 全市场实时行情（东财 spot）────────────
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            # 列名示例：代码 名称 最新价 涨跌幅 成交量 ...
            col_map = {
                "代码":   "code",
                "名称":   "name",
                "最新价": "price",
                "涨跌幅": "pct_chg",
                "成交量": "volume",
            }
            df = df.rename(columns=col_map)
            df["code"] = df["code"].astype(str).str.strip()
            df_idx = df.set_index("code")

            for code in codes:
                if code in df_idx.index:
                    row = df_idx.loc[code]
                    def _safe(v):
                        try:
                            f = float(v)
                            return None if (f != f) else f  # NaN → None
                        except Exception:
                            return None
                    result.append({
                        "code":    code,
                        "name":    str(row.get("name", code)),
                        "price":   _safe(row.get("price")),
                        "pct_chg": _safe(row.get("pct_chg")),
                        "volume":  _safe(row.get("volume")),
                    })
                else:
                    result.append({"code": code, "name": None, "price": None, "pct_chg": None, "volume": None})

            self._send_json(200, {"ok": True, "quotes": result})
            return

        except Exception as e:
            logger.warning("akshare 获取行情失败，降级读取缓存: %s", e)

        # ── 降级：从 data_cache meta.json 读取 name ───────────
        try:
            from portal.data_cache import StockDataCache
            cache = StockDataCache()
        except Exception:
            cache = None

        for code in codes:
            name = None
            if cache:
                try:
                    meta = cache.get_meta(code)
                    if meta:
                        name = meta.get("name")
                except Exception:
                    pass
            result.append({"code": code, "name": name, "price": None, "pct_chg": None, "volume": None})

        self._send_json(200, {"ok": True, "quotes": result})

    def _handle_backtest(self, code: str):
        """GET /backtest?code=xxx — 对指定股票的K线缓存运行回测，返回信号胜率统计。"""
        if not code:
            self._send_json(400, {"ok": False, "error": "缺少 code 参数"})
            return
        try:
            from portal.data_cache import StockDataCache
            df = StockDataCache().get_kline(code)
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"读取缓存失败：{e}"})
            return

        if df is None or df.empty:
            self._send_json(404, {
                "ok": False,
                "error": "请先对该股票执行一次深度分析以建立K线缓存"
            })
            return

        try:
            from portal.backtester import run_backtest
            result = run_backtest(df)
        except Exception as e:
            logger.exception("backtest error for %s: %s", code, e)
            self._send_json(500, {"ok": False, "error": f"回测执行失败：{e}"})
            return

        # 保存到缓存，供走势预测等模块复用
        try:
            from portal.data_cache import StockDataCache
            StockDataCache().save_backtest(code, result)
        except Exception as e:
            logger.warning("保存回测缓存失败 %s: %s", code, e)

        self._send_json(200, {"ok": True, "code": code, "backtest": result})


# ── 深度分析后台任务 ──────────────────────────────────────────
def _json_default(o):
    """json.dumps 的兜底：把 numpy 标量（bool_/int64/float64）等转成原生类型。"""
    # numpy 标量都实现了 .item()
    if hasattr(o, "item"):
        try:
            return o.item()
        except Exception:
            pass
    # pandas/numpy 布尔、其他可布尔化对象
    if isinstance(o, (set,)):
        return list(o)
    try:
        import numpy as np
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
    except Exception:
        pass
    return str(o)


def _recompute_tech_dimension(tech_result, llm_call, log=None):
    """LLM 打分回写各 Section 后，重算技术面维度综合分与综合信号。

    - 维度综合分：各指标 LLM 分的均值（排除占位 50）。
    - 维度综合信号：交给 LLM 基于全部指标做一次总结性判断（零硬编码）。
      LLM 不可用/失败时降级为 hold（不做 score→signal 硬编码推导）。
    """
    def _log(m):
        if log: log(m)

    sections = [s for s in tech_result.sections if s is not None]
    scored = [s for s in sections if s.score != 50]
    tech_result.score = int(sum(s.score for s in scored) / len(scored)) if scored else 50

    # 维度综合信号交给 LLM
    tech_result.signal = "hold"
    if not llm_call or not sections:
        return
    try:
        import json as _json
        brief = "；".join(
            f"{s.title}(评分{s.score},{s.signal})" for s in sections
        )
        prompt = (
            f"你是A股技术分析师。下面是某股票技术面各指标的 LLM 评分与信号汇总：\n{brief}\n\n"
            f"技术面综合评分为 {tech_result.score}/100。请你综合全部指标，给出技术面维度的**综合信号**。\n"
            f"只返回严格 JSON（不要解释、不要markdown围栏）："
            f'{{"signal":"buy或watch或hold或sell"}}'
        )
        resp = (llm_call(prompt) or "").strip()
        obj = _json.loads(resp[resp.find("{"): resp.rfind("}") + 1]) if "{" in resp else {}
        sig = str(obj.get("signal", "hold")).strip().lower()
        if sig not in ("buy", "watch", "hold", "sell"):
            cn = {"买入": "buy", "关注": "watch", "观望": "watch",
                  "持有": "hold", "减仓": "hold", "卖出": "sell"}
            sig = cn.get(str(obj.get("signal", "")).strip(), "hold")
        tech_result.signal = sig
        _log(f"🧭 技术面维度综合信号（LLM）：{sig}")
    except Exception as e:
        logger.warning("技术面维度综合信号 LLM 判断失败: %s", e)


def _run_deep_analysis_task(
    task_id: str,
    stock_code: str,
    stock_name: str,
    dimensions: list,
    modules_map: dict,
    llm_mode: str = "batch",
    open_report: bool = True,
    agent_review: bool = False,
):
    """在后台线程中运行双维度深度分析。"""
    sys.path.insert(0, str(PROJECT_ROOT))

    logs = _tasks[task_id]["logs"]

    def log(msg):
        ts = datetime.now(TZ_CN).strftime("%H:%M:%S")
        logs.append(f"[{ts}] {msg}")
        logger.info("[task %s] %s", task_id, msg)

    with _tasks_lock:
        _tasks[task_id]["status"] = "running"
        _tasks[task_id]["started_at"] = datetime.now(TZ_CN).isoformat()

    try:
        log(f"🚀 开始深度分析：{stock_name}（{stock_code}）")
        log(f"📊 分析维度：{dimensions}")

        # ── 加载环境变量 (.env) ──────────────────────────────
        _load_dotenv()

        # ── 构建 LLM 调用函数 ────────────────────────────────
        llm_call = _make_llm_caller(log)

        # ── 构建搜索函数 ─────────────────────────────────────
        search_fn = _make_search_fn(log)

        # ── 获取 K线数据（各分析器共用）────────────────────
        log("📈 获取K线数据...")
        df = _fetch_kline(stock_code, log)

        # ── 执行各维度分析 ────────────────────────────────────
        from portal.analyzers import ANALYZER_REGISTRY
        results = []

        for dim in dimensions:
            cls = ANALYZER_REGISTRY.get(dim)
            if not cls:
                log(f"⚠️  未知分析维度：{dim}，跳过")
                continue

            analyzer = cls()
            active_modules = modules_map.get(dim) or cls.DEFAULT_MODULES
            log(f"🔍 [{analyzer.name}] 开始分析，子模块：{active_modules}")

            try:
                dim_result = analyzer.analyze(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    df=df,
                    modules=active_modules,
                    llm_call=llm_call,
                    search=search_fn,
                )
                if dim_result.error:
                    log(f"⚠️  [{analyzer.name}] 分析异常：{dim_result.error}")
                else:
                    log(f"✅ [{analyzer.name}] 完成，评分={dim_result.score}，信号={dim_result.signal}")
                results.append(dim_result)
            except Exception as e:
                log(f"❌ [{analyzer.name}] 执行失败：{e}")
                logger.exception("Analyzer %s failed for %s", dim, stock_code)

        # ── 技术面逐指标 LLM 打分（零硬编码：score+signal 全由 LLM 出）──
        #    必须在 merge 之前回写 Section，merge 才能用 LLM 分聚合综合分。
        tech_llm_notes = {}
        try:
            from portal.llm_notes import score_sections
            from portal.data_cache import _last_trading_date
            trade_date = _last_trading_date()
            tech_result = next((r for r in results if r.dimension == "technical"), None)
            if tech_result and tech_result.sections:
                scores = score_sections(stock_code, stock_name, trade_date,
                                        tech_result.sections, llm_call, mode=llm_mode, log=log)
                # 回写到 Section 对象（网页端/HTML 两端同源，自动显示 LLM 分）
                for s in tech_result.sections:
                    if s.key in scores:
                        s.score = scores[s.key]["score"]
                        s.signal = scores[s.key]["signal"]
                        # per_indicator 模式下 LLM 产出了详细分析，追加到客观描述之后展示
                        detail = scores[s.key].get("detail")
                        if detail:
                            s.content = (s.content or "").rstrip() + "\n\n**📊 LLM 分析**\n" + detail
                tech_llm_notes = scores
                # 重算技术面维度综合分 + 维度综合信号（信号交给 LLM 做总结判断）
                _recompute_tech_dimension(tech_result, llm_call, log)
        except Exception as e:
            logger.warning("技术面 LLM 打分失败: %s", e)
            log(f"⚠️ 技术面 LLM 打分失败：{e}")

        # ── 合并报告 ──────────────────────────────────────────
        log("📝 合并各维度结果...")
        from portal.analyzers.merger import merge_results
        final_report = merge_results(stock_code, stock_name, results, llm_call)
        final_report["llm_notes"] = tech_llm_notes
        log(f"🎯 综合评分={final_report['overall_score']}，信号={final_report['overall_signal_label']}")

        # ── 注入 K线数据（最近60条 date/open/high/low/close/ma5/ma20）──
        if df is not None and not df.empty:
            try:
                kline_df = df.copy()
                # 统一列名
                col_map = {}
                for c in kline_df.columns:
                    lc = c.lower()
                    if lc in ('trade_date', 'tradedate', 'date'):
                        col_map[c] = 'date'
                    elif lc == 'open':
                        col_map[c] = 'open'
                    elif lc == 'high':
                        col_map[c] = 'high'
                    elif lc == 'low':
                        col_map[c] = 'low'
                    elif lc == 'close':
                        col_map[c] = 'close'
                    elif lc == 'ma5':
                        col_map[c] = 'ma5'
                    elif lc == 'ma20':
                        col_map[c] = 'ma20'
                    elif lc == 'ma250':
                        col_map[c] = 'ma250'
                kline_df = kline_df.rename(columns=col_map)

                # 如果缺少 ma5/ma20/ma250，现场计算
                if 'close' in kline_df.columns:
                    if 'ma5' not in kline_df.columns:
                        kline_df['ma5'] = kline_df['close'].rolling(5, min_periods=1).mean().round(2)
                    if 'ma20' not in kline_df.columns:
                        kline_df['ma20'] = kline_df['close'].rolling(20, min_periods=1).mean().round(2)
                    if 'ma250' not in kline_df.columns:
                        kline_df['ma250'] = kline_df['close'].rolling(250, min_periods=200).mean().round(2)

                keep_cols = [c for c in ('date', 'open', 'high', 'low', 'close', 'ma5', 'ma20', 'ma250') if c in kline_df.columns]
                kline_df = kline_df[keep_cols].tail(250)

                # 序列化为干净的 list[dict]，NaN → None
                kline_records = []
                for row in kline_df.to_dict(orient='records'):
                    cleaned = {}
                    for k, v in row.items():
                        if v is None:
                            cleaned[k] = None
                        elif isinstance(v, float) and v != v:  # NaN
                            cleaned[k] = None
                        elif hasattr(v, 'item'):  # numpy scalar
                            raw = v.item()
                            cleaned[k] = str(raw) if k == 'date' else round(float(raw), 2)
                        elif isinstance(v, float):
                            cleaned[k] = round(v, 2)
                        else:
                            cleaned[k] = str(v) if k == 'date' else v
                    kline_records.append(cleaned)

                final_report['kline_data'] = kline_records
                log(f"📊 K线数据已注入报告（{len(kline_records)} 条）")
            except Exception as e:
                logger.warning("注入 kline_data 失败: %s", e)
                final_report['kline_data'] = []
        else:
            final_report['kline_data'] = []

        # ── 保存元信息到缓存 ──────────────────────────────────
        try:
            from portal.data_cache import StockDataCache
            cache = StockDataCache()
            # 从产业链维度结果里提取关键词
            industry_result = next(
                (r for r in results if r.dimension == "industry"), None
            )
            keywords = []
            if industry_result:
                for sec in industry_result.sections:
                    if sec.key == "chain_keywords":
                        keywords = sec.data.get("keywords", [])
                        break
            cache.save_meta(stock_code, name=stock_name, keywords=keywords)
            log(f"💾 元信息已保存（关键词: {len(keywords)} 个）")
        except Exception as e:
            logger.warning("保存 meta 失败: %s", e)

        # 注：技术面逐指标 LLM 打分已在 merge 之前完成并回写（见上），此处不再重复。

        # ── 🤖 Agent 综合研判（选项B：单次 LLM 复用已算数据，零重复取数）──
        if agent_review:
            summary_text = _summarize_report_for_agent(final_report)
            try:
                log("🤖 Agent 综合研判：基于已算好的全部指标做深度研判...")
                if llm_call:
                    review_text = llm_call(build_review_prompt(stock_name, stock_code, summary_text)).strip()
                    final_report["agent_review"] = review_text
                    log(f"✅ Agent 综合研判完成（{len(review_text)} 字）")
                else:
                    final_report["agent_review"] = ""
                    log("⚠️ 未配置 LLM，跳过 Agent 综合研判")
            except Exception as e:
                logger.warning("Agent 综合研判失败: %s", e)
                log(f"⚠️ Agent 综合研判失败：{e}")
                final_report["agent_review"] = ""

            # ── 📈 走势预测（7 交易日模拟 K 线 + 次日高低点）──
            try:
                log("📈 走势预测：预测未来 7 交易日走势及次日高低点...")
                kline_tail = [
                    r for r in (final_report.get("kline_data") or [])
                    if isinstance(r, dict) and r.get("close")
                ][-15:]  # 喂最近 15 条（含 OHLC）

                # 读取或现场生成回测数据（供预测 prompt 参考）
                backtest_result = None
                try:
                    from portal.data_cache import StockDataCache
                    from portal.backtester import run_backtest
                    _cache = StockDataCache()
                    backtest_result = _cache.get_backtest(stock_code)
                    if backtest_result is None and df is not None and not df.empty:
                        log("📊 回测数据缺失，现场生成...")
                        backtest_result = run_backtest(df)
                        _cache.save_backtest(stock_code, backtest_result)
                        log("✅ 回测数据已生成并缓存")
                except Exception as _be:
                    logger.warning("走势预测阶段获取回测数据失败: %s", _be)

                if llm_call and kline_tail:
                    raw = llm_call(build_forecast_prompt(
                        stock_name, stock_code, summary_text, kline_tail, backtest_result
                    )).strip()
                    # 抠 JSON
                    s, e_ = raw.find("{"), raw.rfind("}")
                    forecast_obj = json.loads(raw[s:e_+1]) if s >= 0 and e_ > s else {}
                    final_report["price_forecast"] = forecast_obj
                    log("✅ 走势预测完成")
                else:
                    final_report["price_forecast"] = {}
                    if not llm_call:
                        log("⚠️ 未配置 LLM，跳过走势预测")
                    else:
                        log("⚠️ K线数据不足，跳过走势预测")
            except Exception as e:
                logger.warning("走势预测失败: %s", e)
                log(f"⚠️ 走势预测失败：{e}")
                final_report["price_forecast"] = {}

        # ── 生成独立 HTML 报告 + 自动打开 ─────────────────────
        html_path = None
        try:
            from portal.report_html import render_stock_report, open_in_browser
            html_path = render_stock_report(final_report, final_report.get("llm_notes"))
            log(f"📄 HTML 报告已生成：{html_path}")
            if open_report:
                open_in_browser(html_path, log)
        except Exception as e:
            logger.warning("生成 HTML 报告失败: %s", e)
            log(f"⚠️ HTML 报告生成失败：{e}")

        with _tasks_lock:
            _tasks[task_id]["status"] = "done"
            if html_path:
                final_report["html_path"] = str(html_path)
                _tasks[task_id]["html_path"] = str(html_path)
            _tasks[task_id]["report"] = json.dumps(final_report, ensure_ascii=False, default=_json_default)
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()

        log("✅ 深度分析完成")

    except Exception as e:
        logger.exception("Deep analysis task %s failed: %s", task_id, e)
        logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] ❌ 分析失败：{e}")
        with _tasks_lock:
            _tasks[task_id]["status"] = "error"
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()


def _run_chat_task(task_id: str, message: str, session_id: str,
                   stock_code: str = "", stock_name: str = ""):
    """后台线程：交互式 AI 对话（选项A）→ AgentOrchestrator.chat 多轮。

    进度经 progress_callback 转成日志行走现有 SSE（/run/stream/<task_id>）。
    最终答案（result.content）存入 _tasks[task_id]["report"]，report_type="chat"。
    """
    sys.path.insert(0, str(LIB_DIR))
    sys.path.insert(0, str(PROJECT_ROOT))
    logs = _tasks[task_id]["logs"]

    def log(msg):
        ts = datetime.now(TZ_CN).strftime("%H:%M:%S")
        logs.append(f"[{ts}] {msg}")
        logger.info("[chat %s] %s", task_id, msg)

    with _tasks_lock:
        _tasks[task_id]["status"] = "running"
        _tasks[task_id]["started_at"] = datetime.now(TZ_CN).isoformat()

    try:
        log(f"💬 收到提问：{message[:60]}")
        _load_dotenv()
        _apply_agent_env()

        from src.config import get_config
        from src.agent.factory import build_agent_executor

        log("🤖 构建 multi-agent 编排器（Technical→Intel→Decision）...")
        executor = build_agent_executor(get_config())

        def progress_cb(ev):
            try:
                t = (ev or {}).get("type")
                if t == "stage_start":
                    log(f"🔹 {ev.get('stage')} 开始…")
                elif t == "stage_done":
                    dur = ev.get("duration")
                    dur_s = f"{dur:.1f}s" if isinstance(dur, (int, float)) else ""
                    log(f"✅ {ev.get('stage')} 完成（{ev.get('status', '')} {dur_s}）")
                elif t == "pipeline_timeout":
                    log(f"⏱ {ev.get('stage')} 超时（{ev.get('elapsed')}s/{ev.get('timeout')}s）")
            except Exception:
                pass

        ctx = {"stock_code": stock_code, "stock_name": stock_name} if stock_code else None
        result = executor.chat(
            message=message,
            session_id=session_id,
            progress_callback=progress_cb,
            context=ctx,
        )

        content = getattr(result, "content", "") or ""
        success = getattr(result, "success", True)
        with _tasks_lock:
            _tasks[task_id]["report"] = content
            _tasks[task_id]["report_type"] = "chat"
            _tasks[task_id]["status"] = "done" if success else "error"
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()
        if success:
            log(f"✅ 回答完成（{len(content)} 字）")
        else:
            err = getattr(result, "error", "") or "未知错误"
            log(f"⚠️ 回答异常：{err}")

    except Exception as e:
        logger.exception("Chat task %s failed: %s", task_id, e)
        logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] ❌ 对话失败：{e}")
        with _tasks_lock:
            _tasks[task_id]["status"] = "error"
            _tasks[task_id]["report"] = f"[对话失败] {e}"
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()


def _run_market_review_task(task_id: str, open_report: bool = True):
    """后台线程：大盘复盘（上证+创业板）→ 一份 HTML → 打开。"""
    sys.path.insert(0, str(PROJECT_ROOT))
    logs = _tasks[task_id]["logs"]

    def log(msg):
        ts = datetime.now(TZ_CN).strftime("%H:%M:%S")
        logs.append(f"[{ts}] {msg}")
        logger.info("[market %s] %s", task_id, msg)

    with _tasks_lock:
        _tasks[task_id]["status"] = "running"
        _tasks[task_id]["started_at"] = datetime.now(TZ_CN).isoformat()

    try:
        log("🌐 开始大盘复盘（上证指数 + 创业板指）")
        _load_dotenv()
        llm_call = _make_llm_caller(log)

        from portal.analyzers.market import MarketAnalyzer
        mkt = MarketAnalyzer()
        results = mkt.analyze_all(llm_call, log)
        if not results:
            raise RuntimeError("未获取到任何指数数据")

        log("📝 生成大盘整体研判...")
        overall = mkt.build_overall_summary(results, llm_call, log)

        from portal.report_html import render_market_report, open_in_browser
        html_path = render_market_report(results, overall)
        log(f"📄 大盘 HTML 报告已生成：{html_path}")
        if open_report:
            open_in_browser(html_path, log)

        with _tasks_lock:
            _tasks[task_id]["status"] = "done"
            _tasks[task_id]["html_path"] = str(html_path)
            _tasks[task_id]["report"] = json.dumps(
                {"kind": "market", "indices": results, "overall_summary": overall,
                 "html_path": str(html_path)},
                ensure_ascii=False, default=_json_default)
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()
        log("✅ 大盘复盘完成")

    except Exception as e:
        logger.exception("Market review task %s failed: %s", task_id, e)
        logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] ❌ 大盘复盘失败：{e}")
        with _tasks_lock:
            _tasks[task_id]["status"] = "error"
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()


def _ensure_env_file():
    """确保项目根目录存在 .env 文件；不存在时从 .env.example 复制创建。"""
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        return
    example = PROJECT_ROOT / ".env.example"
    try:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        if example.exists():
            env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("✅ 已从 .env.example 自动创建 %s", env_file)
        else:
            env_file.write_text("", encoding="utf-8")
            logger.info("✅ 已自动创建空 %s（未找到 .env.example）", env_file)
    except Exception as e:
        logger.warning("自动创建 %s 失败: %s", env_file, e)


def _load_dotenv():
    """读取配置文件目录 .env，注入环境变量（不覆盖已有值）。"""
    # 确保 .env 存在（不存在则自动创建）
    _ensure_env_file()
    # 优先 portal/lib/.env，其次项目根 .env
    for env_file in [LIB_DIR / ".env", PROJECT_ROOT / ".env"]:
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception as e:
            logger.warning("load_dotenv %s error: %s", env_file, e)
        break


def _make_llm_caller(log):
    """构建 LLM 调用函数，复用 litellm。

    优先级：
      1. Hai Proxy（SAP 内网 OpenAI 兼容网关）—— HAI_BASE_URL + HAI_API_KEY + HAI_MODEL
         用于内网直连外部 LLM（DeepSeek/OpenAI）被封锁的场景。
      2. LITELLM_MODEL / GEMINI / DEEPSEEK / OPENAI（公网直连）
    """
    try:
        import litellm
        # 关掉 litellm 的调试提示（含 "Provider List: https://..." 这类无用行）
        try:
            litellm.suppress_debug_info = True
            litellm.set_verbose = False
        except Exception:
            pass

        # ── 优先：Hai Proxy（内网 OpenAI 兼容网关）──────────────
        hai_base = os.environ.get("HAI_BASE_URL")
        hai_key  = os.environ.get("HAI_API_KEY")
        if hai_base and hai_key:
            hai_model = os.environ.get("HAI_MODEL", "gpt-4.1")
            # litellm 用 openai/ 前缀走 OpenAI 兼容协议 + 自定义 api_base
            model = f"openai/{hai_model}"
            log(f"🤖 LLM：Hai Proxy（{hai_model} @ {hai_base}）")

            # GPT-5 系列不支持自定义 temperature（只接受默认值），且有 reasoning 开销
            is_gpt5 = "gpt-5" in hai_model.lower()

            def call_hai(prompt: str) -> str:
                kwargs = dict(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    api_base=hai_base,
                    api_key=hai_key,
                    max_tokens=8192,   # litellm 自动转 max_completion_tokens；调大以容纳
                                       # K线形态/波浪/缠论等长结构化输出，并为 GPT-5 系列的
                                       # reasoning 开销预留预算，避免返回空内容（content=""）
                    timeout=90,
                )
                if not is_gpt5:
                    kwargs["temperature"] = 0.3   # GPT-5 只允许默认 temperature，故省略
                resp = litellm.completion(**kwargs)
                return resp.choices[0].message.content or ""

            return call_hai

        # ── 回退：公网直连模型 ──────────────────────────────────
        model = (
            os.environ.get("LITELLM_MODEL")
            or os.environ.get("GEMINI_MODEL")
            or ("gemini/gemini-2.0-flash" if os.environ.get("GEMINI_API_KEY") else None)
            or ("deepseek/deepseek-chat" if os.environ.get("DEEPSEEK_API_KEY") else None)
            or ("gpt-4o-mini" if os.environ.get("OPENAI_API_KEY") else None)
        )

        if not model:
            log("⚠️  未配置 LLM（Hai Proxy 或 API Key），LLM 相关子模块将跳过")
            return None

        log(f"🤖 LLM 模型：{model}")

        def call(prompt: str) -> str:
            resp = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=8192,   # 调大以容纳长结构化输出（K线形态/波浪/缠论），避免返回空内容
                timeout=60,
            )
            return resp.choices[0].message.content or ""

        return call

    except ImportError:
        log("⚠️  litellm 未安装，LLM 子模块跳过")
        return None
    except Exception as e:
        log(f"⚠️  LLM 初始化失败：{e}")
        return None


def _apply_agent_env():
    """把 portal 现有的 HAI_* 网关配置映射为 agent 框架（get_config）认识的 OPENAI_*，
    并设定 multi-agent 编排默认参数。用 setdefault，不覆盖用户显式设置。

    必须在构建 agent（build_agent_executor）之前调用。
    """
    hai_key = os.environ.get("HAI_API_KEY")
    hai_base = os.environ.get("HAI_BASE_URL")
    if hai_key and hai_base:
        os.environ.setdefault("OPENAI_API_KEY", hai_key)
        os.environ.setdefault("OPENAI_BASE_URL", hai_base)
        _m = os.environ.get("HAI_MODEL", "gpt-4.1")
        os.environ.setdefault("LITELLM_MODEL", f"openai/{_m}")
    os.environ.setdefault("AGENT_ARCH", "multi")                  # multi → AgentOrchestrator
    os.environ.setdefault("AGENT_ORCHESTRATOR_MODE", "standard")  # technical→intel→decision
    os.environ.setdefault("AGENT_MAX_STEPS", "10")


def _summarize_report_for_agent(final_report: dict) -> str:
    """把已算好的 final_report 抽取成一段结构化中文摘要，供 Agent 综合研判（选项B）。

    只喂"已算好的结论与数据"，不让 LLM 重新取数。
    """
    lines = []
    name = final_report.get("stock_name", "")
    code = final_report.get("stock_code", "")
    lines.append(f"标的：{name}（{code}）")
    lines.append(
        f"系统加权综合评分：{final_report.get('overall_score', '?')}/100，"
        f"综合信号：{final_report.get('overall_signal_label', final_report.get('overall_signal', '?'))}"
    )

    for dim in final_report.get("dimensions", []):
        if not isinstance(dim, dict):
            continue
        dname = dim.get("name") or dim.get("dimension", "")
        if dim.get("error"):
            lines.append(f"\n### {dname}（分析失败：{dim.get('error')}）")
            continue
        lines.append(
            f"\n### {dname}（维度评分 {dim.get('score', '?')}/100，信号：{dim.get('signal', '?')}）"
        )
        for sec in dim.get("sections", []):
            if not isinstance(sec, dict):
                continue
            title = sec.get("title", "")
            content = (sec.get("content") or "").strip()
            first_line = content.split("\n")[0].replace("**", "") if content else ""
            sig = sec.get("signal", "")
            score = sec.get("score", "")
            lines.append(f"- {title}（{sig}/{score}）：{first_line}")

    notes = final_report.get("llm_notes") or {}
    if isinstance(notes, dict) and notes:
        lines.append("\n### 逐指标点评（偏多/偏空）")
        for key, note in notes.items():
            if isinstance(note, dict):
                lines.append(f"- {key}：{note.get('stance', '')} — {note.get('reason', '')}")

    kline = final_report.get("kline_data") or []
    if isinstance(kline, list) and kline:
        lines.append("\n### 最近K线（date/close/ma5/ma20）")
        for r in kline[-5:]:
            if isinstance(r, dict):
                lines.append(
                    f"- {r.get('date')}: close={r.get('close')} ma5={r.get('ma5')} ma20={r.get('ma20')}"
                )

    return "\n".join(lines)


def build_review_prompt(stock_name: str, stock_code: str, summary_text: str) -> str:
    """构造 Agent 综合研判 prompt（选项B）。数据全在 prompt 里，LLM 只做综合，不取数。"""
    return f"""你是一位严谨、务实的 A 股资深投研分析师。以下是系统已完成的量化分析结论与数据，\
请**基于这些【已算好的】结果**做综合研判，**不要重复取数、不要臆造未提供的数据**。

标的：{stock_name}（{stock_code}）

系统量化分析结论与数据：
{summary_text}

请严格按以下结构输出（总计 250-400 字，客观中立、数据驱动、拒绝套话）：
1. **核心判断**：一句话定性（强势/偏多/震荡/偏空/弱势），必须引用上面 2-3 个最关键的具体指标数值作为依据。
2. **多空依据**：分别列「看多理由」「看空理由」各 1-2 条，引用具体指标，客观呈现分歧。
3. **操作建议**：明确可执行——观望还是参与、关注/买入价位区间或触发条件、止损参考位、仓位建议（轻仓/半仓等）。
4. **风险提示**：一句话点明当前最需警惕的风险。

直接输出研判，用自然段落 + 关键处加粗，不要加大标题。"""


def build_forecast_prompt(stock_name: str, stock_code: str, summary_text: str,
                          kline_tail: list, backtest_result: dict = None) -> str:
    """构造走势预测 prompt，要求 LLM 输出结构化 JSON。

    kline_tail:      最近若干条 {date, open, close, low, high} 记录，供 LLM 感知价格区间。
    backtest_result: run_backtest() 的返回值，作为历史信号胜率参考。
    """
    kline_str = "\n".join(
        f"  {r.get('date')}: O={r.get('open')} H={r.get('high')} L={r.get('low')} C={r.get('close')}"
        for r in kline_tail if isinstance(r, dict)
    )

    # 回测摘要：只取有统计数据的信号，列出 1/3/5 日胜率和均收益
    backtest_str = ""
    if backtest_result and isinstance(backtest_result, dict):
        signals = backtest_result.get("signals") or {}
        lines = []
        for sig, v in signals.items():
            if not isinstance(v, dict) or not v.get("stats"):
                continue
            stats = v["stats"]
            parts = []
            for d in ("1", "3", "5", "10", "20"):
                s = stats.get(d)
                if s and s.get("win_rate") is not None:
                    parts.append(f"{d}日胜率{s['win_rate']}%/均收益{s['avg_return']}%")
            if parts:
                lines.append(f"  {sig}（触发{v.get('count',0)}次）：{'  '.join(parts)}")
        if lines:
            period = backtest_result.get("stock_days", "")
            backtest_str = f"\n历史信号回测（{period}，共{backtest_result.get('total_days',0)}日）：\n" + "\n".join(lines)

    return f"""你是一位 A 股量化技术分析师。以下是 {stock_name}（{stock_code}）的量化分析摘要和近期 K 线数据。
请基于这些信息，给出**走势预测**（这是模型推理的情景模拟，仅供研究参考，不构成投资建议）。

量化分析摘要：
{summary_text}
{backtest_str}

最近 K 线（OHLC）：
{kline_str}

请**只返回严格 JSON**（不要解释、不要 markdown 围栏），格式如下：
{{
  "next_day": {{
    "high": 数字,
    "low": 数字,
    "trend": "上涨|震荡上行|震荡|震荡下行|下跌",
    "reason": "简短依据（1-2句）"
  }},
  "week_forecast": [
    {{"day": 1, "open": 数字, "high": 数字, "low": 数字, "close": 数字, "note": "简短说明"}},
    {{"day": 2, "open": 数字, "high": 数字, "low": 数字, "close": 数字, "note": ""}},
    {{"day": 3, "open": 数字, "high": 数字, "low": 数字, "close": 数字, "note": ""}},
    {{"day": 4, "open": 数字, "high": 数字, "low": 数字, "close": 数字, "note": ""}},
    {{"day": 5, "open": 数字, "high": 数字, "low": 数字, "close": 数字, "note": ""}},
    {{"day": 6, "open": 数字, "high": 数字, "low": 数字, "close": 数字, "note": ""}},
    {{"day": 7, "open": 数字, "high": 数字, "low": 数字, "close": 数字, "note": ""}}
  ]
}}

注意：
- 所有价格数字必须是合理的浮点数，基于最近收盘价 {kline_tail[-1].get('close') if kline_tail else '?'} 附近，幅度不超过 ±10%。
- 回测数据中胜率高的信号意味着历史上该信号后续走势更可预测，请在制定预测时优先参考当前触发的高胜率信号。
- next_day 的 high/low 要比 week_forecast[0] 的 high/low 更精确（日内区间更窄）。
- week_forecast 每日 open 应等于或接近前一日 close（连续性）。
- 只输出 JSON，不要任何其他文字。"""


def _make_search_fn(log):
    """
    构建关键词搜索函数，供产业链 LLM 子模块检索资讯。

    注：src/search_service.py 的 SearchService 只提供面向个股的
    search_stock_news(code, name) 等接口，没有通用的 search(keyword)。
    产业链子模块需要的是"关键词 → 资讯片段"，接口不匹配，故此处
    暂不接入（返回 None）。industry.py 已对 search=None 做降级：
    改用 LLM 自身知识分析，不影响板块子模块（板块用 efinance 真实数据）。

    未来若要接入关键词搜索，可在此封装 SearchService.search_stock_news
    或直接调用某个搜索 API，返回 [{"snippet": "..."}] 列表。
    """
    log("ℹ️  关键词搜索未接入，产业链 LLM 子模块将用模型知识分析（板块子模块不受影响）")
    return None


def _fetch_kline(stock_code: str, log) -> object:
    """
    获取股票 K 线数据（带本地增量缓存）。

    流程：
      1. 检查 portal/data/stocks/{code}/kline.csv 是否存在
      2. 计算需要拉取的日期范围（full / incremental / up_to_date）
      3. 拉取新数据 → 合并到缓存 → 返回完整 DataFrame
    """
    try:
        from portal.data_cache import StockDataCache
        cache = StockDataCache()

        start, end, mode = cache.calc_fetch_range(stock_code, days=250)

        if mode == "up_to_date":
            df = cache.get_kline(stock_code)
            if df is not None and not df.empty:
                log(f"📦 使用本地缓存（已是最新，{len(df)} 条）")
                return df
            # 缓存存在但读取失败，降级到网络拉取
            log("⚠️  本地缓存读取失败，尝试网络拉取")
            mode = "full"
            start = None
            end   = None

        # 网络拉取
        from data_provider import DataFetcherManager
        mgr = DataFetcherManager()   # 无参：自动按优先级加载默认数据源

        if mode == "incremental":
            log(f"📥 增量拉取 {start} ~ {end}")
            result = mgr.get_daily_data(stock_code, start_date=start, end_date=end)
            if isinstance(result, tuple):
                new_df, source_name = result
            else:
                new_df, source_name = result, "unknown"

            if new_df is not None and not new_df.empty:
                cache.merge_kline(stock_code, new_df, source_name)
                log(f"✅ 增量更新 {len(new_df)} 条，写入缓存")
            else:
                log("ℹ️  无新交易数据（非交易日），使用现有缓存进行复盘分析")

            df = cache.get_kline(stock_code)
            if df is not None and not df.empty:
                return df
            log("⚠️  本地缓存为空，尝试全量拉取")
            mode = "full"
            start = None
            end   = None

        else:  # full
            log(f"🌐 首次全量拉取（最近 250 日）")
            result = mgr.get_daily_data(stock_code, days=250)
            if isinstance(result, tuple):
                df, source_name = result
            else:
                df, source_name = result, "unknown"

            if df is not None and not df.empty:
                cache.save_kline(stock_code, df, source_name)
                log(f"✅ 获取 {len(df)} 条K线数据，已写入缓存")
                return df
            else:
                log("⚠️  K线数据为空")
                return None

    except Exception as e:
        log(f"⚠️  K线数据获取失败：{e}")
        # 降级：尝试直接从缓存读取（即使过期也比没有好）
        try:
            from portal.data_cache import StockDataCache
            df = StockDataCache().get_kline(stock_code)
            if df is not None and not df.empty:
                log(f"📦 降级使用过期缓存（{len(df)} 条）")
                return df
        except Exception:
            pass
        return None


def main():
    # 启动时确保 .env 存在（不存在则自动创建）
    _ensure_env_file()
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    logger.info("=" * 50)
    logger.info("Portal 本地服务已启动")
    logger.info("地址: http://127.0.0.1:%d", PORT)
    logger.info("配置文件: %s", CONFIG_PATH)
    logger.info("按 Ctrl+C 停止")
    logger.info("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("服务已停止")


if __name__ == "__main__":
    main()
