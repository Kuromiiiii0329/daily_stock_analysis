"""
portal/analyzers/merger.py
合并所有维度结果，生成最终结构化报告。
权重：技术面 40% + 基本面 40% + 产业链 20%
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any

from .base import DimensionResult

TZ_CN = timezone(timedelta(hours=8))

DIMENSION_WEIGHTS = {
    "technical": 0.40,
    "fundamental": 0.40,
    "industry": 0.20,
}

SIGNAL_PRIORITY = {
    "buy": 4,
    "watch": 3,
    "hold": 2,
    "sell": 1,
}

SIGNAL_LABELS = {
    "buy": "买入",
    "watch": "关注",
    "hold": "持有",
    "sell": "卖出",
}

SCORE_TO_SIGNAL = [
    (70, "buy"),
    (55, "watch"),
    (40, "hold"),
    (0,  "sell"),
]


def _score_to_signal(score: int) -> str:
    for threshold, sig in SCORE_TO_SIGNAL:
        if score >= threshold:
            return sig
    return "sell"


def merge_results(
    stock_code: str,
    stock_name: str,
    results: list[DimensionResult],
    llm_call=None,
) -> dict[str, Any]:
    """
    将各维度分析结果合并为最终报告。

    Args:
        stock_code: 股票代码
        stock_name: 股票名称
        results:    各维度 DimensionResult 列表
        llm_call:   可选，用于生成综合结论的 LLM 调用函数

    Returns:
        {
          "stock_code": str,
          "stock_name": str,
          "overall_score": int,
          "overall_signal": str,
          "overall_signal_label": str,
          "conclusion": str,
          "dimensions": [...],
          "generated_at": str,
        }
    """
    result_map = {r.dimension: r for r in results}

    # ── 加权综合评分 ────────────────────────────────────────
    total_weight = 0.0
    weighted_score = 0.0
    for dim_result in results:
        if dim_result.error:
            continue
        w = DIMENSION_WEIGHTS.get(dim_result.dimension, 0.1)
        weighted_score += dim_result.score * w
        total_weight += w

    overall_score = int(weighted_score / total_weight) if total_weight > 0 else 50
    overall_signal = _score_to_signal(overall_score)

    # ── 生成综合结论 ─────────────────────────────────────────
    conclusion = _build_conclusion(stock_code, stock_name, results, overall_score, overall_signal, llm_call)

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "overall_score": overall_score,
        "overall_signal": overall_signal,
        "overall_signal_label": SIGNAL_LABELS.get(overall_signal, overall_signal),
        "conclusion": conclusion,
        "dimensions": [r.to_dict() for r in results],
        "generated_at": datetime.now(TZ_CN).isoformat(),
    }


def _build_conclusion(
    stock_code: str,
    stock_name: str,
    results: list[DimensionResult],
    overall_score: int,
    overall_signal: str,
    llm_call,
) -> str:
    """用 LLM 生成一段话综合结论，无 LLM 时用规则拼接。"""
    if llm_call:
        summaries = "\n".join(
            f"【{r.name}】（评分{r.score}/100，信号:{r.signal}）：{r.summary}"
            for r in results if not r.error
        )
        prompt = f"""你是一位专业股票分析师。
请根据以下各维度分析摘要，为 {stock_name}（{stock_code}）生成一段 100-150 字的综合投资结论。
结论要具体、务实，直接点明核心矛盾和操作建议。

各维度摘要：
{summaries}

综合评分：{overall_score}/100，综合信号：{SIGNAL_LABELS.get(overall_signal, overall_signal)}

请直接输出结论段落，不要加标题："""
        try:
            return llm_call(prompt).strip()
        except Exception:
            pass

    # 规则降级：拼接各维度摘要
    parts = [f"{r.name}：{r.summary}" for r in results if r.summary and not r.error]
    signal_label = SIGNAL_LABELS.get(overall_signal, overall_signal)
    return f"综合评分 {overall_score}/100，信号：{signal_label}。" + "；".join(parts) + "。"
