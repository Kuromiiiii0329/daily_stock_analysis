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
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

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
        except (BrokenPipeError, ConnectionResetError):
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
            args=(task_id, stock_code, stock_name, dimensions, modules_map),
            daemon=True,
        )
        thread.start()
        logger.info("深度分析任务 %s 已启动: %s (%s)", task_id, stock_name, stock_code)
        self._send_json(200, {"ok": True, "task_id": task_id})

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
        """POST /env — 写入白名单内的 key 到 .env 文件，保留注释，立即生效。"""
        updates = payload.get("env", {})
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

        # ── 优先：akshare 全市场实时行情 ─────────────────────
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

        self._send_json(200, {"ok": True, "code": code, "backtest": result})


# ── 深度分析后台任务 ──────────────────────────────────────────
def _run_deep_analysis_task(
    task_id: str,
    stock_code: str,
    stock_name: str,
    dimensions: list,
    modules_map: dict,
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

        # ── 合并报告 ──────────────────────────────────────────
        log("📝 合并各维度结果...")
        from portal.analyzers.merger import merge_results
        final_report = merge_results(stock_code, stock_name, results, llm_call)
        log(f"🎯 综合评分={final_report['overall_score']}，信号={final_report['overall_signal_label']}")

        # ── 注入 K线数据（最近60条 date/close/ma5/ma20）────────
        if df is not None and not df.empty:
            try:
                kline_df = df.copy()
                # 统一列名
                col_map = {}
                for c in kline_df.columns:
                    lc = c.lower()
                    if lc in ('trade_date', 'tradedate', 'date'):
                        col_map[c] = 'date'
                    elif lc == 'close':
                        col_map[c] = 'close'
                    elif lc == 'ma5':
                        col_map[c] = 'ma5'
                    elif lc == 'ma20':
                        col_map[c] = 'ma20'
                kline_df = kline_df.rename(columns=col_map)

                # 如果缺少 ma5/ma20，现场计算
                if 'close' in kline_df.columns:
                    if 'ma5' not in kline_df.columns:
                        kline_df['ma5'] = kline_df['close'].rolling(5, min_periods=1).mean().round(2)
                    if 'ma20' not in kline_df.columns:
                        kline_df['ma20'] = kline_df['close'].rolling(20, min_periods=1).mean().round(2)

                keep_cols = [c for c in ('date', 'close', 'ma5', 'ma20') if c in kline_df.columns]
                kline_df = kline_df[keep_cols].tail(60)

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

        with _tasks_lock:
            _tasks[task_id]["status"] = "done"
            _tasks[task_id]["report"] = json.dumps(final_report, ensure_ascii=False)
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()

        log("✅ 深度分析完成")

    except Exception as e:
        logger.exception("Deep analysis task %s failed: %s", task_id, e)
        logs.append(f"[{datetime.now(TZ_CN).strftime('%H:%M:%S')}] ❌ 分析失败：{e}")
        with _tasks_lock:
            _tasks[task_id]["status"] = "error"
            _tasks[task_id]["finished_at"] = datetime.now(TZ_CN).isoformat()


def _load_dotenv():
    """读取配置文件目录 .env，注入环境变量（不覆盖已有值）。"""
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
    """构建 LLM 调用函数，复用 litellm。"""
    try:
        import litellm

        # 从环境变量获取模型名，按优先级尝试
        model = (
            os.environ.get("LITELLM_MODEL")
            or os.environ.get("GEMINI_MODEL")
            or ("gemini/gemini-2.0-flash" if os.environ.get("GEMINI_API_KEY") else None)
            or ("deepseek/deepseek-chat" if os.environ.get("DEEPSEEK_API_KEY") else None)
            or ("gpt-4o-mini" if os.environ.get("OPENAI_API_KEY") else None)
        )

        if not model:
            log("⚠️  未配置 LLM API Key，LLM 相关子模块将跳过")
            return None

        log(f"🤖 LLM 模型：{model}")

        def call(prompt: str) -> str:
            resp = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
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


def _make_search_fn(log):
    """构建搜索函数，复用 src/search_service.py。"""
    try:
        from src.search_service import SearchService
        from src.config import get_config
        config = get_config()
        svc = SearchService(config)

        def search(query: str) -> list:
            try:
                results = svc.search(query, max_results=5)
                return results or []
            except Exception as e:
                log(f"⚠️  搜索失败（{query[:20]}）：{e}")
                return []

        return search
    except Exception as e:
        log(f"⚠️  搜索服务初始化失败：{e}，产业链分析将无搜索数据")
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

        start, end, mode = cache.calc_fetch_range(stock_code, days=120)

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
        from src.config import get_config
        config = get_config()
        mgr = DataFetcherManager(config)

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
                log("ℹ️  增量无新数据（可能是非交易日）")

            df = cache.get_kline(stock_code)
            if df is not None and not df.empty:
                return df
            # 缓存合并失败，返回增量数据
            return new_df

        else:  # full
            log(f"🌐 首次全量拉取（最近 120 日）")
            result = mgr.get_daily_data(stock_code, days=120)
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
