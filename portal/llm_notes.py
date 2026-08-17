"""
portal/llm_notes.py — 逐指标 LLM 点评（偏多/偏空/影响）+ 交易日缓存

双模式：
  - batch:         所有指标打包一次调 LLM（快，默认）
  - per_indicator: 每个指标单独调 LLM（详细，慢）

缓存：portal/data/stocks/{code}/llm_notes/{trade_date}.json
  同股票+同指标+同交易日不重复调 LLM，天然复用。

输出结构（每个 section）：{"stance": bullish|bearish|neutral, "reason": "...", "impact": "..."}
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 只点评"数值型技术指标"，跳过本身就是 LLM 文本的段（避免重复调用）
NOTE_KEYS = {
    "ma_system", "macd", "rsi", "kdj", "bollinger", "overbought",
    "divergence", "volume", "chip", "turnover", "margin", "ma250",
}


def _notes_dir(code: str) -> Path:
    d = Path(__file__).parent / "data" / "stocks" / code / "llm_notes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _notes_path(code: str, trade_date: str) -> Path:
    safe_date = str(trade_date).replace("-", "")
    return _notes_dir(code) / f"{safe_date}.json"


def _load_notes(code: str, trade_date: str) -> dict:
    p = _notes_path(code, trade_date)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("notes", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_notes(code: str, trade_date: str, notes: dict):
    try:
        _notes_path(code, trade_date).write_text(
            json.dumps({"trade_date": trade_date, "notes": notes}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception as e:
        logger.warning("[llm_notes] 保存失败 %s: %s", code, e)


def _parse_json(text: str) -> Optional[dict]:
    """从 LLM 回复里抠出首个 JSON 对象，容错 markdown 围栏/多余文字。"""
    if not text:
        return None
    # 去 ```json 围栏
    text = re.sub(r"```(?:json)?", "", text)
    # 抠第一个 {...}（贪婪到最后一个 }）
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end+1])
        except Exception:
            pass
    return None


def _norm_note(obj) -> dict:
    """规范化单条点评为 {stance, reason, impact}，非法降级 neutral。"""
    if not isinstance(obj, dict):
        return {"stance": "neutral", "reason": str(obj)[:40], "impact": ""}
    stance = str(obj.get("stance", "neutral")).lower()
    if stance not in ("bullish", "bearish", "neutral"):
        # 兼容中文
        m = {"偏多": "bullish", "看多": "bullish", "偏空": "bearish", "看空": "bearish", "中性": "neutral"}
        stance = m.get(obj.get("stance", ""), "neutral")
    return {
        "stance": stance,
        "reason": str(obj.get("reason", ""))[:60],
        "impact": str(obj.get("impact", ""))[:60],
    }


def _section_brief(s: dict) -> str:
    """指标数据摘要（喂给 LLM）。"""
    content = (s.get("content") or "").replace("\n", " ")[:200]
    data = s.get("data") or {}
    # data 可能含 numpy bool/int/float，default=str 兜底
    data_str = json.dumps(data, ensure_ascii=False, default=str)[:200] if data else ""
    return f"【{s.get('title')}】(评分{s.get('score',50)}) {content} 数据:{data_str}"


def _build_single_prompt(stock_name: str, s: dict) -> str:
    return f"""你是A股技术分析师。请点评 {stock_name} 的以下单项技术指标，判断它当前对股价是偏多、偏空还是中性。

{_section_brief(s)}

只返回严格 JSON（不要任何解释文字、不要markdown围栏）：
{{"stance":"bullish或bearish或neutral","reason":"判断依据，引用具体数值，≤40字","impact":"对后市的影响，≤40字"}}"""


def _build_batch_prompt(stock_name: str, sections: list) -> str:
    items = "\n".join(f"[{s.get('key')}] {_section_brief(s)}" for s in sections)
    keys = ", ".join(f'"{s.get("key")}"' for s in sections)
    return f"""你是A股技术分析师。请逐个点评 {stock_name} 的以下技术指标，每个判断偏多/偏空/中性。

{items}

只返回严格 JSON 对象（不要解释、不要markdown围栏），键为指标标识，值为点评：
{{{keys and (keys.split(",")[0].strip() + ':{"stance":"bullish或bearish或neutral","reason":"依据,引用数值,≤40字","impact":"影响,≤40字"}, ...其余指标同理')}}}
必须包含全部这些键：{keys}"""


def generate_notes(code: str, stock_name: str, trade_date: str,
                   sections: list, llm_call, mode: str = "batch", log=None) -> dict:
    """
    对 sections 做逐指标点评，返回 {section_key: {stance, reason, impact}}。
    带交易日缓存，只对未命中的指标调 LLM。
    """
    def _log(m):
        if log: log(m)

    # 只点评数值指标
    target = [s for s in sections if s.get("key") in NOTE_KEYS and not s.get("error")]
    if not target:
        return {}

    cached = _load_notes(code, trade_date)
    todo = [s for s in target if s.get("key") not in cached]

    if not todo:
        _log(f"📝 逐指标点评全部命中缓存（{len(cached)} 项）")
        return {s.get("key"): cached[s.get("key")] for s in target if s.get("key") in cached}

    if not llm_call:
        _log("⚠️ 未配置 LLM，跳过逐指标点评")
        return {s.get("key"): cached.get(s.get("key"), {"stance": "neutral", "reason": "", "impact": ""})
                for s in target}

    result = dict(cached)

    if mode == "per_indicator":
        _log(f"📝 逐指标详细点评：{len(todo)} 项待分析（缓存命中 {len(cached)}）")
        for i, s in enumerate(todo, 1):
            key = s.get("key")
            _log(f"  🤖 点评 {i}/{len(todo)}：{s.get('title')}")
            try:
                resp = llm_call(_build_single_prompt(stock_name, s))
                obj = _parse_json(resp)
                result[key] = _norm_note(obj) if obj else {"stance": "neutral", "reason": (resp or "")[:40], "impact": ""}
            except Exception as e:
                logger.warning("[llm_notes] 单项点评失败 %s/%s: %s", code, key, e)
                result[key] = {"stance": "neutral", "reason": "分析失败", "impact": ""}
    else:  # batch
        _log(f"📝 批量点评：{len(todo)} 项打包分析（缓存命中 {len(cached)}）")
        try:
            resp = llm_call(_build_batch_prompt(stock_name, todo))
            obj = _parse_json(resp) or {}
            for s in todo:
                key = s.get("key")
                result[key] = _norm_note(obj.get(key)) if obj.get(key) else {"stance": "neutral", "reason": "", "impact": ""}
        except Exception as e:
            logger.warning("[llm_notes] 批量点评失败 %s: %s", code, e)
            for s in todo:
                result[s.get("key")] = {"stance": "neutral", "reason": "分析失败", "impact": ""}

    _save_notes(code, trade_date, result)
    # 只返回本次 target 涉及的
    return {s.get("key"): result[s.get("key")] for s in target if s.get("key") in result}
