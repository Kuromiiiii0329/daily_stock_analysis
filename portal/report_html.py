"""
portal/report_html.py — 独立 HTML 报告生成器

两套模板：
  - render_stock_report(report, llm_notes)  个股深度分析报告
  - render_market_report(index_results)     大盘复盘报告（双指数并排）

特性：
  - 纯 Python f-string，零新依赖；ECharts CDN 内联（雷达图 + 蜡烛图）
  - 自包含内联 CSS（不依赖 Tailwind CDN，离线也能看文字/布局）
  - 同 code/prefix 去重：只保留当日最新 HTML（_purge_old）
  - webbrowser 自动打开（open_in_browser，失败不阻断）
  - 文件名含日期：{code}_{YYYYMMDD}.html / market_{YYYYMMDD}.html
"""
from __future__ import annotations

import html as _htmllib
import json
import logging
import re
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))
ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"

# 信号 → 颜色/中文（与 report-view.js 的 SIGNAL_CONFIG 对齐）
SIGNAL_CFG = {
    "buy":   {"label": "买入", "bg": "#dcfce7", "text": "#15803d", "dot": "#16a34a"},
    "watch": {"label": "关注", "bg": "#dbeafe", "text": "#1d4ed8", "dot": "#2563eb"},
    "hold":  {"label": "持有", "bg": "#f3f4f6", "text": "#4b5563", "dot": "#6b7280"},
    "sell":  {"label": "卖出", "bg": "#fee2e2", "text": "#b91c1c", "dot": "#dc2626"},
}
STANCE_CFG = {
    "bullish": {"label": "偏多", "color": "#16a34a", "bg": "#dcfce7"},
    "bearish": {"label": "偏空", "color": "#dc2626", "bg": "#fee2e2"},
    "neutral": {"label": "中性", "color": "#6b7280", "bg": "#f3f4f6"},
}


def _now_cn() -> datetime:
    return datetime.now(TZ_CN)


def _today_str() -> str:
    return _now_cn().strftime("%Y%m%d")


def _reports_dir() -> Path:
    d = Path(__file__).parent / "data" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _esc(s) -> str:
    return _htmllib.escape(str(s if s is not None else ""))


def _score_color(score: int) -> str:
    if score >= 70: return "#16a34a"
    if score >= 55: return "#2563eb"
    if score >= 40: return "#6b7280"
    return "#dc2626"


def _md_to_html(md: str) -> str:
    """极简 Markdown → HTML（移植 report-view.js 的 _mdToHtml）。"""
    if not md:
        return ""
    out = []
    for line in str(md).split("\n"):
        if re.match(r"^\*\*(.+)\*\*$", line):
            out.append(f'<p class="b">{_esc(line.replace("**",""))}</p>'); continue
        if re.match(r"^#{1,3} (.+)", line):
            out.append(f'<p class="b">{_esc(re.sub(r"^#+\s","",line))}</p>'); continue
        if re.match(r"^[-*] (.+)", line):
            out.append(f'<p class="li">• {_esc(line[2:])}</p>'); continue
        if line.strip() == "":
            continue
        h = _esc(line)
        h = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h)
        h = re.sub(r"`(.+?)`", r'<code>\1</code>', h)
        h = re.sub(r"(✅|🟢)", r'<span style="color:#16a34a">\1</span>', h)
        h = re.sub(r"(⚠️|🔴|❌)", r'<span style="color:#dc2626">\1</span>', h)
        out.append(f"<p>{h}</p>")
    return "\n".join(out)


def _purge_old(prefix: str):
    """删除同 prefix 的非当日 HTML（去重，只留最新）。prefix 如 '002230' 或 'market'。"""
    today = _today_str()
    try:
        for f in _reports_dir().glob(f"{prefix}_*.html"):
            # 保留今天的，删其余
            if f.name != f"{prefix}_{today}.html":
                try: f.unlink()
                except Exception: pass
    except Exception as e:
        logger.warning("[report_html] purge %s 失败: %s", prefix, e)


# ── 通用 CSS ──────────────────────────────────────────────────
_BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;background:#f8fafc;color:#1f2937;line-height:1.6;padding:20px}
.wrap{max-width:1080px;margin:0 auto}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.hd{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.title{font-size:22px;font-weight:700}.code{font-family:monospace;color:#6b7280;font-size:14px;margin-left:8px}
.date{color:#9ca3af;font-size:13px;margin-top:4px}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600}
.ring{font-size:26px;font-weight:800}
.concl{background:linear-gradient(to right,#f8fafc,#eff6ff);border:1px solid #dbeafe;border-radius:12px;padding:14px 16px;margin-bottom:16px;font-size:14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:760px){.grid2{grid-template-columns:1fr}}
.chart{height:300px;width:100%}
.dim-hd{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:#f9fafb;border-bottom:1px solid #eee;border-radius:12px 12px 0 0;font-weight:600}
.sec{border-bottom:1px solid #f1f5f9;padding:10px 14px}
.sec:last-child{border-bottom:none}
.sec-hd{display:flex;align-items:center;justify-content:space-between;font-weight:600;font-size:14px;margin-bottom:4px}
.sec-bd{font-size:13px;color:#4b5563}.sec-bd p{margin:2px 0}.sec-bd .b{font-weight:600;color:#374151;margin-top:4px}
.sec-bd .li{padding-left:8px}.sec-bd code{background:#eef2ff;color:#4338ca;padding:0 4px;border-radius:4px;font-size:12px}
.note{margin-top:8px;padding:8px 10px;border-radius:8px;font-size:13px;border-left:3px solid}
.note b{font-weight:700}
.foot{text-align:center;color:#9ca3af;font-size:12px;margin-top:20px;padding:12px}
.small{font-size:12px;color:#6b7280}
"""


def _dim_icon(dim: str) -> str:
    return {"technical": "📊", "fundamental": "📈", "industry": "🏭"}.get(dim, "📋")


def _render_sections_html(sections: list, llm_notes: dict) -> str:
    """渲染一组 section 卡片（含 LLM 点评块）。"""
    parts = []
    for s in sections:
        sig = SIGNAL_CFG.get(s.get("signal", "hold"), SIGNAL_CFG["hold"])
        note_html = ""
        note = (llm_notes or {}).get(s.get("key"))
        if note and isinstance(note, dict):
            st = STANCE_CFG.get(note.get("stance", "neutral"), STANCE_CFG["neutral"])
            reason = _esc(note.get("reason", ""))
            impact = _esc(note.get("impact", ""))
            note_html = (
                f'<div class="note" style="border-color:{st["color"]};background:{st["bg"]}">'
                f'🤖 <b style="color:{st["color"]}">{st["label"]}</b> — {reason}'
                + (f'（影响：{impact}）' if impact else '') + '</div>'
            )
        parts.append(
            f'<div class="sec"><div class="sec-hd"><span>{_esc(s.get("title"))}</span>'
            f'<span class="badge" style="background:{sig["bg"]};color:{sig["text"]}">{sig["label"]} · {s.get("score",50)}</span></div>'
            f'<div class="sec-bd">{_md_to_html(s.get("content",""))}{note_html}</div></div>'
        )
    return "\n".join(parts)


# ── 个股报告 ──────────────────────────────────────────────────
def render_stock_report(report: dict, llm_notes: Optional[dict] = None) -> Path:
    llm_notes = llm_notes or report.get("llm_notes") or {}
    code = str(report.get("stock_code", "")).strip() or "unknown"
    name = report.get("stock_name", code)
    score = report.get("overall_score", 50)
    signal = report.get("overall_signal", "hold")
    sig = SIGNAL_CFG.get(signal, SIGNAL_CFG["hold"])
    date_disp = _now_cn().strftime("%Y-%m-%d %H:%M")
    conclusion = _esc(report.get("conclusion", ""))
    agent_review_html = _md_to_html(report.get("agent_review", "")) if report.get("agent_review") else ""

    dims = report.get("dimensions", [])
    tech_dims  = [d for d in dims if d.get("dimension") == "technical"]
    other_dims = [d for d in dims if d.get("dimension") != "technical"]

    def _dim_block(d):
        dsig = SIGNAL_CFG.get(d.get("signal", "hold"), SIGNAL_CFG["hold"])
        secs = _render_sections_html(d.get("sections", []), llm_notes)
        summ = f'<div class="sec small">{_esc(d.get("summary",""))}</div>' if d.get("summary") else ""
        return (
            f'<div class="card" style="padding:0">'
            f'<div class="dim-hd"><span>{_dim_icon(d.get("dimension"))} {_esc(d.get("name"))}</span>'
            f'<span class="badge" style="background:{dsig["bg"]};color:{dsig["text"]}">{dsig["label"]} · {d.get("score",50)}</span></div>'
            f'{summ}{secs}</div>'
        )

    left_col  = "".join(_dim_block(d) for d in tech_dims)
    right_col = "".join(_dim_block(d) for d in other_dims)

    kline = report.get("kline_data", []) or []
    radar_scores = {d.get("dimension"): d.get("score", 0) for d in dims}

    init_js = f"""
    (function(){{
      if(typeof echarts==='undefined')return;
      var kl={json.dumps(kline, ensure_ascii=False)};
      var rs={json.dumps(radar_scores, ensure_ascii=False)};
      // 雷达图
      var rEl=document.getElementById('radar');
      if(rEl){{echarts.init(rEl).setOption({{
        tooltip:{{}},
        radar:{{indicator:[{{name:'技术面',max:100}},{{name:'基本面',max:100}},{{name:'产业链',max:100}}],radius:'65%',axisName:{{fontSize:12,color:'#374151'}}}},
        series:[{{type:'radar',data:[{{value:[rs.technical||0,rs.fundamental||0,rs.industry||0],areaStyle:{{color:'rgba(59,130,246,.18)'}},lineStyle:{{color:'#3b82f6',width:2}},itemStyle:{{color:'#3b82f6'}}}}]}}]
      }});}}
      // K线蜡烛图
      var kEl=document.getElementById('kline');
      if(kEl&&kl.length){{
        var d=kl.slice(-90);
        var dates=d.map(function(x){{return (x.date||'').slice(5)}});
        var cand=d.map(function(x){{return [x.open,x.close,x.low,x.high]}});
        var hasOHLC=d[0]&&d[0].open!=null;
        var ma5=d.map(function(x){{return x.ma5}}),ma20=d.map(function(x){{return x.ma20}});
        var series=[];
        if(hasOHLC){{series.push({{name:'K线',type:'candlestick',data:cand,itemStyle:{{color:'#ef4444',color0:'#10b981',borderColor:'#ef4444',borderColor0:'#10b981'}}}});}}
        else{{series.push({{name:'收盘',type:'line',data:d.map(function(x){{return x.close}}),symbol:'none',lineStyle:{{color:'#3b82f6',width:2}}}});}}
        series.push({{name:'MA5',type:'line',data:ma5,symbol:'none',lineStyle:{{color:'#f97316',width:1}}}});
        series.push({{name:'MA20',type:'line',data:ma20,symbol:'none',lineStyle:{{color:'#8b5cf6',width:1}}}});
        echarts.init(kEl).setOption({{
          tooltip:{{trigger:'axis',axisPointer:{{type:'cross'}}}},
          legend:{{data:series.map(function(s){{return s.name}}),top:2,textStyle:{{fontSize:11}}}},
          grid:{{left:8,right:8,top:30,bottom:20,containLabel:true}},
          xAxis:{{type:'category',data:dates,axisLabel:{{fontSize:9,color:'#9ca3af'}}}},
          yAxis:{{scale:true,axisLabel:{{fontSize:9,color:'#9ca3af'}},splitLine:{{lineStyle:{{color:'#f3f4f6'}}}}}},
          series:series
        }});
      }}
    }})();
    """

    body = f"""
    <div class="wrap">
      <div class="card hd">
        <div><span class="title">{_esc(name)}</span><span class="code">{_esc(code)}</span>
          <div class="date">📅 报告日期：{date_disp}（最新交易日数据）</div></div>
        <div style="text-align:right">
          <div class="ring" style="color:{_score_color(score)}">{score}<span style="font-size:14px;color:#9ca3af">/100</span></div>
          <span class="badge" style="background:{sig['bg']};color:{sig['text']}">{sig['label']}</span>
        </div>
      </div>
      {'<div class="concl">🎯 <b>综合结论</b>：' + conclusion + '</div>' if conclusion else ''}
      {'<div class="concl" style="border-left-color:#3b82f6;background:#eff6ff">🤖 <b>Agent 综合研判</b><div style="margin-top:6px">' + agent_review_html + '</div></div>' if agent_review_html else ''}
      <div class="grid2">
        <div class="card"><div class="small" style="margin-bottom:6px">📡 维度评分雷达</div><div id="radar" class="chart" style="height:260px"></div></div>
        <div class="card"><div class="small" style="margin-bottom:6px">📈 K线走势（近90日）</div><div id="kline" class="chart" style="height:260px"></div></div>
      </div>
      <div class="grid2">
        <div>{left_col}</div>
        <div>{right_col}</div>
      </div>
      <div class="foot">本报告由本地量化系统自动生成，仅供研究参考，不构成投资建议。生成时间 {date_disp}</div>
    </div>
    """

    doc = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(name)} {_esc(code)} 分析报告 {_today_str()}</title>
<script src="{ECHARTS_CDN}"></script>
<style>{_BASE_CSS}</style></head>
<body>{body}<script>{init_js}</script></body></html>"""

    _purge_old(code)
    path = _reports_dir() / f"{code}_{_today_str()}.html"
    path.write_text(doc, encoding="utf-8")
    logger.info("[report_html] 个股报告已生成: %s", path)
    return path


# ── 大盘报告（双指数并排，第二套模板）─────────────────────────
def render_market_report(index_results: list, overall_summary: str = "") -> Path:
    date_disp = _now_cn().strftime("%Y-%m-%d %H:%M")

    cols = []
    init_blocks = []
    for i, idx in enumerate(index_results):
        symbol = idx.get("symbol", "")
        name = idx.get("name", symbol)
        score = idx.get("score", 50)
        signal = idx.get("signal", "hold")
        sig = SIGNAL_CFG.get(signal, SIGNAL_CFG["hold"])
        secs = _render_sections_html(idx.get("sections", []), {})
        kline = idx.get("kline_data", []) or []
        last_close = kline[-1].get("close") if kline else None
        chart_id = f"kl{i}"

        cols.append(
            f'<div class="card" style="padding:0">'
            f'<div class="dim-hd"><span>📊 {_esc(name)} <span class="small">{_esc(symbol)}</span></span>'
            f'<span class="badge" style="background:{sig["bg"]};color:{sig["text"]}">{sig["label"]} · {score}</span></div>'
            f'<div class="sec small">最新收盘：{last_close if last_close is not None else "-"}　{_esc(idx.get("summary",""))}</div>'
            f'<div style="padding:8px"><div id="{chart_id}" class="chart" style="height:240px"></div></div>'
            f'{secs}</div>'
        )

        init_blocks.append(f"""
        (function(){{
          var kl={json.dumps(kline, ensure_ascii=False)};
          var el=document.getElementById('{chart_id}');
          if(!el||!kl.length||typeof echarts==='undefined')return;
          var d=kl.slice(-120);
          var dates=d.map(function(x){{return (x.date||'').slice(5)}});
          var cand=d.map(function(x){{return [x.open,x.close,x.low,x.high]}});
          echarts.init(el).setOption({{
            tooltip:{{trigger:'axis',axisPointer:{{type:'cross'}}}},
            legend:{{data:['K线','MA20','MA60','MA250年线'],top:2,textStyle:{{fontSize:10}}}},
            grid:{{left:8,right:8,top:28,bottom:20,containLabel:true}},
            xAxis:{{type:'category',data:dates,axisLabel:{{fontSize:9,color:'#9ca3af'}}}},
            yAxis:{{scale:true,axisLabel:{{fontSize:9,color:'#9ca3af'}},splitLine:{{lineStyle:{{color:'#f3f4f6'}}}}}},
            series:[
              {{name:'K线',type:'candlestick',data:cand,itemStyle:{{color:'#ef4444',color0:'#10b981',borderColor:'#ef4444',borderColor0:'#10b981'}}}},
              {{name:'MA20',type:'line',data:d.map(function(x){{return x.ma20}}),symbol:'none',lineStyle:{{color:'#8b5cf6',width:1}}}},
              {{name:'MA60',type:'line',data:d.map(function(x){{return x.ma60}}),symbol:'none',lineStyle:{{color:'#f97316',width:1}}}},
              {{name:'MA250年线',type:'line',data:d.map(function(x){{return x.ma250}}),symbol:'none',lineStyle:{{color:'#dc2626',width:1.5,type:'dashed'}}}}
            ]
          }});
        }})();
        """)

    body = f"""
    <div class="wrap">
      <div class="card hd">
        <div><span class="title">🌐 大盘复盘</span>
          <div class="date">📅 {date_disp}（以最新交易日数据为准，不判断交易日）</div></div>
      </div>
      {'<div class="concl">🎯 <b>大盘整体研判</b>：' + _md_to_html(overall_summary) + '</div>' if overall_summary else ''}
      <div class="grid2">{''.join(cols)}</div>
      <div class="foot">本报告由本地量化系统自动生成，仅供研究参考，不构成投资建议。生成时间 {date_disp}</div>
    </div>
    """

    doc = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>大盘复盘 {_today_str()}</title>
<script src="{ECHARTS_CDN}"></script>
<style>{_BASE_CSS}</style></head>
<body>{body}<script>{''.join(init_blocks)}</script></body></html>"""

    _purge_old("market")
    path = _reports_dir() / f"market_{_today_str()}.html"
    path.write_text(doc, encoding="utf-8")
    logger.info("[report_html] 大盘报告已生成: %s", path)
    return path


# ── 打开 / 查找 ───────────────────────────────────────────────
def open_in_browser(path: Path, log=None) -> bool:
    """用默认浏览器打开 HTML。无头/远程环境失败时返回 False，不抛异常。"""
    try:
        ok = webbrowser.open(Path(path).as_uri())
        msg = f"🌐 已在浏览器打开报告：{path}" if ok else f"⚠️ 浏览器打开失败，报告已生成于：{path}"
        if log: log(msg)
        logger.info(msg)
        return bool(ok)
    except Exception as e:
        msg = f"⚠️ 无法自动打开浏览器（{e}），报告已生成于：{path}"
        if log: log(msg)
        logger.warning(msg)
        return False


def find_latest_stock_html(code: str) -> Optional[Path]:
    files = sorted(_reports_dir().glob(f"{code}_*.html"), reverse=True)
    return files[0] if files else None


def find_latest_market_html() -> Optional[Path]:
    files = sorted(_reports_dir().glob("market_*.html"), reverse=True)
    return files[0] if files else None
