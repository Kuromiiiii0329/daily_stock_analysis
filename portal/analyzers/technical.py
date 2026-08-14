"""
portal/analyzers/technical.py
技术面分析器

复用 src/stock_analyzer.py（StockTrendAnalyzer）+ pandas 计算 KDJ/布林带。
子模块：ma_system / macd / rsi / volume / kdj / bollinger / pattern / wave / chan
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# path setup handled by server.py startup

from .base import BaseAnalyzer, DimensionResult, Section

logger = logging.getLogger(__name__)


class TechnicalAnalyzer(BaseAnalyzer):
    name = "技术面"
    dimension = "technical"
    description = "均线/MACD/RSI/KDJ/布林带/量价/形态/波浪/缠论"

    MODULES = {
        "ma_system":  "均线系统（MA5/10/20/60）",
        "macd":       "MACD 指标",
        "rsi":        "RSI 超买超卖",
        "kdj":        "KDJ 随机指标",
        "bollinger":  "布林带",
        "volume":     "量价关系",
        "pattern":    "K线形态（LLM）",
        "wave":       "波浪理论（LLM）",
        "chan":        "缠论（LLM）",
    }
    DEFAULT_MODULES = ["ma_system", "macd", "rsi", "kdj", "bollinger", "volume"]

    def analyze(self, stock_code, stock_name, df, modules, llm_call, search):
        result = DimensionResult(dimension=self.dimension, name=self.name)

        if df is None or df.empty or len(df) < 20:
            result.error = "K线数据不足（少于20日），无法进行技术分析"
            result.score = 50
            result.signal = "hold"
            return result

        try:
            df = df.copy().sort_values("date").reset_index(drop=True)
            df = self._compute_indicators(df)
            sections = []

            if "ma_system" in modules:
                sections.append(self._analyze_ma(df, stock_code))
            if "macd" in modules:
                sections.append(self._analyze_macd(df))
            if "rsi" in modules:
                sections.append(self._analyze_rsi(df))
            if "kdj" in modules:
                sections.append(self._analyze_kdj(df))
            if "bollinger" in modules:
                sections.append(self._analyze_bollinger(df))
            if "volume" in modules:
                sections.append(self._analyze_volume(df))
            if "pattern" in modules and llm_call:
                sections.append(self._analyze_pattern_llm(df, stock_name, llm_call))
            if "wave" in modules and llm_call:
                sections.append(self._analyze_wave_llm(df, stock_name, llm_call))
            if "chan" in modules and llm_call:
                sections.append(self._analyze_chan_llm(df, stock_name, llm_call))

            result.sections = sections

            # 综合评分：各子模块加权平均
            scored = [s for s in sections if s.score != 50]
            result.score = int(sum(s.score for s in scored) / len(scored)) if scored else 50
            result.signal = self._score_to_signal(result.score)

            # 一句话摘要
            ma_sec = next((s for s in sections if s.key == "ma_system"), None)
            result.summary = ma_sec.content.split("\n")[0] if ma_sec else f"技术评分 {result.score}/100"

        except Exception as e:
            logger.exception("TechnicalAnalyzer error for %s: %s", stock_code, e)
            result.error = str(e)

        return result

    # ── 指标计算 ─────────────────────────────────────────────
    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        volume = df.get("volume", pd.Series(dtype=float))

        # MA
        for n in [5, 10, 20, 60]:
            df[f"ma{n}"] = close.rolling(n).mean()

        # MACD (12/26/9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["dif"] = ema12 - ema26
        df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
        df["macd_bar"] = (df["dif"] - df["dea"]) * 2

        # RSI
        for n in [6, 12, 24]:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(n).mean()
            loss = (-delta).clip(lower=0).rolling(n).mean()
            rs = gain / loss.replace(0, 1e-9)
            df[f"rsi{n}"] = 100 - 100 / (1 + rs)

        # KDJ (9日)
        low_9  = df["low"].rolling(9).min()
        high_9 = df["high"].rolling(9).max()
        rsv = (close - low_9) / (high_9 - low_9 + 1e-9) * 100
        df["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
        df["kdj_d"] = df["kdj_k"].ewm(com=2, adjust=False).mean()
        df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

        # Bollinger Bands (20日, 2σ)
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        df["boll_mid"]   = mid
        df["boll_upper"] = mid + 2 * std
        df["boll_lower"] = mid - 2 * std
        df["boll_width"] = (df["boll_upper"] - df["boll_lower"]) / mid * 100

        # 成交量均线
        if not volume.empty:
            df["vol_ma5"]  = volume.rolling(5).mean()
            df["vol_ratio"] = volume / df["vol_ma5"].replace(0, 1e-9)

        return df

    # ── 子模块 ───────────────────────────────────────────────
    def _analyze_ma(self, df, stock_code) -> Section:
        last = df.iloc[-1]
        close = last["close"]
        ma5, ma10, ma20, ma60 = last.get("ma5"), last.get("ma10"), last.get("ma20"), last.get("ma60")

        bias5  = (close - ma5)  / ma5  * 100 if ma5  else 0
        bias20 = (close - ma20) / ma20 * 100 if ma20 else 0

        alignment = ""
        score = 50
        if all(v is not None and not pd.isna(v) for v in [ma5, ma10, ma20]):
            if ma5 > ma10 > ma20:
                alignment = "多头排列（MA5>MA10>MA20）"
                score = 75
            elif ma5 < ma10 < ma20:
                alignment = "空头排列（MA5<MA10<MA20）"
                score = 30
            else:
                alignment = "均线缠绕（震荡）"
                score = 50

        if abs(bias5) > 8:
            score = max(score - 10, 20)
            bias_warn = f"  ⚠️ 乖离率偏大（{bias5:+.1f}%），追高风险高\n"
        else:
            bias_warn = ""

        content = (
            f"**{alignment}**\n"
            f"- 当前价: {close:.2f}\n"
            f"- MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}"
            + (f"  MA60={ma60:.2f}" if ma60 and not pd.isna(ma60) else "") + "\n"
            f"- 乖离率MA5: {bias5:+.1f}%  乖离率MA20: {bias20:+.1f}%\n"
            + bias_warn
        )
        signal = self._score_to_signal(score)
        return Section(key="ma_system", title="均线系统", content=content,
                       data={"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
                             "close": close, "bias5": bias5},
                       score=score, signal=signal)

    def _analyze_macd(self, df) -> Section:
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        dif, dea, bar = last.get("dif", 0), last.get("dea", 0), last.get("macd_bar", 0)
        prev_bar = prev.get("macd_bar", 0)

        # 金叉/死叉检测
        golden = prev.get("dif", 0) < prev.get("dea", 0) and dif > dea
        death  = prev.get("dif", 0) > prev.get("dea", 0) and dif < dea

        score = 55 if dif > dea else 45
        if golden: score = 72
        if death:  score = 30
        if dif > 0 and dea > 0: score = min(score + 5, 85)
        if dif < 0 and dea < 0: score = max(score - 5, 20)

        cross = "🔴 死叉" if death else ("🟢 金叉" if golden else "")
        zero_pos = "零轴上方（多头区域）" if dif > 0 else "零轴下方（空头区域）"
        bar_trend = "↑ 柱线扩大" if bar > prev_bar else "↓ 柱线收缩"

        content = (
            f"**DIF={dif:.4f}  DEA={dea:.4f}  MACD柱={bar:.4f}**\n"
            f"- 位置：{zero_pos} {cross}\n"
            f"- 柱线趋势：{bar_trend}\n"
        )
        return Section(key="macd", title="MACD指标", content=content,
                       data={"dif": dif, "dea": dea, "bar": bar, "golden": golden, "death": death},
                       score=score, signal=self._score_to_signal(score))

    def _analyze_rsi(self, df) -> Section:
        last = df.iloc[-1]
        r6, r12, r24 = (last.get(f"rsi{n}", 50) for n in [6, 12, 24])

        score = 50
        status = "中性"
        if r6 > 80:
            status, score = "严重超买，短期回调风险高", 25
        elif r6 > 70:
            status, score = "超买区域，注意风险", 35
        elif r6 < 20:
            status, score = "严重超卖，可关注反弹", 75
        elif r6 < 30:
            status, score = "超卖区域，具备反弹条件", 65
        else:
            score = int(50 + (r6 - 50) * 0.3)

        content = (
            f"**RSI(6)={r6:.1f}  RSI(12)={r12:.1f}  RSI(24)={r24:.1f}**\n"
            f"- 状态：{status}\n"
            f"- 参考区间：超买>70，超卖<30\n"
        )
        return Section(key="rsi", title="RSI超买超卖", content=content,
                       data={"rsi6": r6, "rsi12": r12, "rsi24": r24},
                       score=score, signal=self._score_to_signal(score))

    def _analyze_kdj(self, df) -> Section:
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        k, d, j = last.get("kdj_k", 50), last.get("kdj_d", 50), last.get("kdj_j", 50)
        pk, pd_ = prev.get("kdj_k", 50), prev.get("kdj_d", 50)

        golden = pk < pd_ and k > d
        death  = pk > pd_ and k < d
        score = 55 if k > d else 45
        if golden: score = 70
        if death:  score = 32
        if j > 90: score = max(score - 15, 20)
        if j < 10: score = min(score + 15, 80)

        cross = "🟢 金叉" if golden else ("🔴 死叉" if death else "")
        overbought = " ⚠️ J值超买" if j > 90 else (" ✅ J值超卖反弹信号" if j < 10 else "")

        content = (
            f"**K={k:.1f}  D={d:.1f}  J={j:.1f}** {cross}{overbought}\n"
            f"- K>D（多头信号）{' ✓' if k > d else ' ✗'}\n"
        )
        return Section(key="kdj", title="KDJ随机指标", content=content,
                       data={"k": k, "d": d, "j": j, "golden": golden, "death": death},
                       score=score, signal=self._score_to_signal(score))

    def _analyze_bollinger(self, df) -> Section:
        last = df.iloc[-1]
        close = last["close"]
        upper = last.get("boll_upper")
        mid   = last.get("boll_mid")
        lower = last.get("boll_lower")
        width = last.get("boll_width", 0)

        if upper is None or pd.isna(upper):
            return Section(key="bollinger", title="布林带", content="数据不足，无法计算布林带",
                           score=50, signal="hold")

        pos_pct = (close - lower) / (upper - lower + 1e-9) * 100

        score = 50
        status = ""
        if close > upper:
            status, score = "价格突破上轨（超买/强势突破）", 35
        elif close < lower:
            status, score = "价格跌破下轨（超卖/弱势）", 65
        elif pos_pct > 75:
            status, score = "价格在布林带上半区（偏强）", 60
        elif pos_pct < 25:
            status, score = "价格在布林带下半区（偏弱）", 42
        else:
            status, score = "价格在布林带中部（震荡）", 50

        content = (
            f"**上轨={upper:.2f}  中轨={mid:.2f}  下轨={lower:.2f}**\n"
            f"- 带宽：{width:.1f}%  价格位置：{pos_pct:.0f}%\n"
            f"- 状态：{status}\n"
        )
        return Section(key="bollinger", title="布林带", content=content,
                       data={"upper": upper, "mid": mid, "lower": lower,
                             "width": width, "pos_pct": pos_pct},
                       score=score, signal=self._score_to_signal(score))

    def _analyze_volume(self, df) -> Section:
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        vol_ratio = last.get("vol_ratio", 1.0)
        close_change = (last["close"] - prev["close"]) / prev["close"] * 100

        score = 50
        if vol_ratio > 2 and close_change > 0:
            status, score = "放量上涨（强势信号）", 72
        elif vol_ratio > 2 and close_change < 0:
            status, score = "放量下跌（做空信号）", 28
        elif vol_ratio < 0.5 and close_change > 0:
            status, score = "缩量上涨（量能不足，谨慎）", 52
        elif vol_ratio < 0.5 and close_change < 0:
            status, score = "缩量回调（洗盘信号，可关注）", 60
        else:
            status = "量能正常"

        content = (
            f"**量比={vol_ratio:.2f}x  当日涨跌={close_change:+.2f}%**\n"
            f"- 状态：{status}\n"
            f"- 量比>2为放量，<0.5为缩量\n"
        )
        return Section(key="volume", title="量价关系", content=content,
                       data={"vol_ratio": vol_ratio, "close_change": close_change},
                       score=score, signal=self._score_to_signal(score))

    def _analyze_pattern_llm(self, df, stock_name, llm_call) -> Section:
        recent = df.tail(10)[["date", "open", "high", "low", "close", "volume"]].to_string(index=False)
        prompt = f"""你是技术分析专家。请分析以下 {stock_name} 最近10日K线数据，识别形态：
{recent}

请输出：
1. 识别到的K线形态（如：双底、头肩顶、三角收敛、旗形整理等）
2. 形态的可靠性（高/中/低）
3. 形态暗示的后续走势方向
4. 简要技术结论（1-2句话）

请简洁回答，不超过150字。"""
        try:
            content = llm_call(prompt).strip()
        except Exception as e:
            content = f"K线形态分析失败：{e}"
        return Section(key="pattern", title="K线形态", content=content, score=50, signal="hold")

    def _analyze_wave_llm(self, df, stock_name, llm_call) -> Section:
        closes = df.tail(60)["close"].round(2).tolist()
        prompt = f"""你是波浪理论专家。请基于以下 {stock_name} 近60日收盘价，进行波浪分析：
{closes}

请分析：
1. 当前所处的波浪位置（第几浪、推进浪还是调整浪）
2. 预计下一步走势
3. 关键的支撑/压力位
4. 投资建议（买入/持有/观望/卖出）

请简洁回答，不超过200字。"""
        try:
            content = llm_call(prompt).strip()
        except Exception as e:
            content = f"波浪分析失败：{e}"
        return Section(key="wave", title="波浪理论", content=content, score=50, signal="hold")

    def _analyze_chan_llm(self, df, stock_name, llm_call) -> Section:
        closes = df.tail(60)["close"].round(2).tolist()
        highs  = df.tail(60)["high"].round(2).tolist()
        lows   = df.tail(60)["low"].round(2).tolist()
        prompt = f"""你是缠论专家。请基于以下 {stock_name} 近60日价格数据，进行缠论分析：
收盘价：{closes}
最高价：{highs}
最低价：{lows}

请分析：
1. 当前所处的缠论结构（笔/线段/中枢状态）
2. 是否出现顶背驰或底背驰信号
3. 当前的买卖点等级（一买/二买/三买 或 一卖/二卖/三卖）
4. 操作建议

请简洁回答，不超过200字。"""
        try:
            content = llm_call(prompt).strip()
        except Exception as e:
            content = f"缠论分析失败：{e}"
        return Section(key="chan", title="缠论分析", content=content, score=50, signal="hold")

    @staticmethod
    def _score_to_signal(score: int) -> str:
        if score >= 70: return "buy"
        if score >= 55: return "watch"
        if score >= 40: return "hold"
        return "sell"


# ── 独立测试入口 ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys as _sys
    code = _sys.argv[1] if len(_sys.argv) > 1 else "600519"
    print(f"测试技术面分析：{code}")
    from data_provider import DataFetcherManager
    from src.config import get_config
    config = get_config()
    mgr = DataFetcherManager(config)
    df = mgr.get_stock_data(code, days=90)
    if df is not None and not df.empty:
        analyzer = TechnicalAnalyzer()
        result = analyzer.analyze(code, code, df,
                                  modules=list(TechnicalAnalyzer.DEFAULT_MODULES),
                                  llm_call=None, search=None)
        print(f"评分: {result.score}  信号: {result.signal}")
        for s in result.sections:
            print(f"\n[{s.title}]\n{s.content}")
    else:
        print("获取数据失败")
