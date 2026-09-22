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

import threading
import webbrowser
from http.server import HTTPServer
from pathlib import Path


class _QuietHTTPServer(HTTPServer):
    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return  # 浏览器主动断开，静默忽略
        super().handle_error(request, client_address)


def main():
    server = _QuietHTTPServer(("127.0.0.1", PORT), Handler)
    logger.info("=" * 50)
    logger.info("Portal 本地服务已启动")
    logger.info("地址: http://127.0.0.1:%d", PORT)
    logger.info("配置文件: %s", CONFIG_PATH)
    logger.info("按 Ctrl+C 停止")
    logger.info("=" * 50)
    html = Path(__file__).parent / "index-standalone.html"
    threading.Timer(0.5, webbrowser.open, args=(html.as_uri(),)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("服务已停止")


if __name__ == "__main__":
    main()
