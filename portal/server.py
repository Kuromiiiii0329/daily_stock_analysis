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
    """获取股票 K线数据，返回 DataFrame 或 None。"""
    try:
        from data_provider import DataFetcherManager
        from src.config import get_config
        config = get_config()
        mgr = DataFetcherManager(config)
        df = mgr.get_stock_data(stock_code, days=120)
        if df is not None and not df.empty:
            log(f"✅ 获取到 {len(df)} 条K线数据")
            return df
        else:
            log("⚠️  K线数据为空")
            return None
    except Exception as e:
        log(f"⚠️  K线数据获取失败：{e}")
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
