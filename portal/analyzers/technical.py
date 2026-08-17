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
        "ma_system":   "均线系统（MA5/10/20/60）",
        "macd":        "MACD 指标",
        "rsi":         "RSI 超买超卖",
        "kdj":         "KDJ 随机指标",
        "bollinger":   "布林带",
        "overbought":  "超买超卖综合（RSI+KDJ+WR+布林）",
        "divergence":  "背离检测（顶背离/底背离）",
        "volume":      "量价关系",
        "llm_tech":    "技术指标综合精讲（LLM，基于所有量化指标）",
        "pattern":     "K线形态（LLM）",
        "wave":        "波浪理论（LLM）",
        "chan":         "缠论（LLM）",
        "chip":        "筹码分布（成本集中度）",
        "turnover":    "换手率趋势（近30日）",
        "margin":      "融资融券余额趋势",
    }
    DEFAULT_MODULES = ["ma_system", "macd", "rsi", "kdj", "bollinger",
                       "overbought", "divergence", "volume", "llm_tech"]

    def analyze(self, stock_code, stock_name, df, modules, llm_call, search):
        result = DimensionResult(dimension=self.dimension, name=self.name)

        if df is None or df.empty or len(df) < 20:
            result.error = "K线数据不足（少于20日），无法进行技术分析"
            result.score = 50
            result.signal = "hold"
            return result

        try:
            df = df.copy().sort_values("date").reset_index(drop=True)
            self._df = df          # 保留引用，供背离渲染时取日期
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
            if "overbought" in modules:
                sections.append(self._analyze_overbought(df))
            if "divergence" in modules:
                sections.append(self._analyze_divergence(df))
            if "volume" in modules:
                sections.append(self._analyze_volume(df))
            if "pattern" in modules and llm_call:
                sections.append(self._analyze_pattern_llm(df, stock_name, llm_call))
            if "wave" in modules and llm_call:
                sections.append(self._analyze_wave_llm(df, stock_name, llm_call))
            if "chan" in modules and llm_call:
                sections.append(self._analyze_chan_llm(df, stock_name, llm_call))
            if "chip" in modules:
                sections.append(self._analyze_chip(df, stock_code))
            if "turnover" in modules:
                sections.append(self._analyze_turnover(df))
            if "margin" in modules:
                sections.append(self._analyze_margin(df, stock_code))
            if "llm_tech" in modules and llm_call:
                sections.append(self._analyze_llm_tech(df, stock_name, llm_call, sections))

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
        for n in [5, 10, 20, 60, 120, 250]:
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

        # 威廉指标 WR(14)：(最高-收盘)/(最高-最低)*(-100)，[-100,0]，
        # <-80 超卖、>-20 超买（注意方向与 RSI 相反）
        hh14 = df["high"].rolling(14).max()
        ll14 = df["low"].rolling(14).min()
        df["wr14"] = (hh14 - close) / (hh14 - ll14 + 1e-9) * -100

        # 成交量均线
        if not volume.empty:
            df["vol_ma5"]  = volume.rolling(5).mean()
            df["vol_ratio"] = volume / df["vol_ma5"].replace(0, 1e-9)

        return df

    # ── 子模块 ───────────────────────────────────────────────
    def _analyze_ma(self, df, stock_code) -> Section:
        last = df.iloc[-1]
        close = last["close"]
        ma5   = last.get("ma5")
        ma10  = last.get("ma10")
        ma20  = last.get("ma20")
        ma60  = last.get("ma60")
        ma120 = last.get("ma120")
        ma250 = last.get("ma250")

        def _v(x): return x if (x is not None and not pd.isna(x)) else None

        ma5, ma10, ma20, ma60, ma120, ma250 = (
            _v(ma5), _v(ma10), _v(ma20), _v(ma60), _v(ma120), _v(ma250)
        )

        bias5  = (close - ma5)  / ma5  * 100 if ma5  else 0
        bias20 = (close - ma20) / ma20 * 100 if ma20 else 0

        # 均线多空排列（短中期）
        alignment = ""
        score = 50
        if all(v is not None for v in [ma5, ma10, ma20]):
            if ma5 > ma10 > ma20:
                alignment = "多头排列（MA5>MA10>MA20）"
                score = 75
            elif ma5 < ma10 < ma20:
                alignment = "空头排列（MA5<MA10<MA20）"
                score = 30
            else:
                alignment = "均线缠绕（震荡）"
                score = 50

        # 价格与年线/120日线的位置加分/减分
        above_ma250 = ma250 and close > ma250
        above_ma120 = ma120 and close > ma120
        if above_ma250:
            score = min(score + 8, 90)
        elif ma250 and close < ma250:
            score = max(score - 8, 15)

        if abs(bias5) > 8:
            score = max(score - 10, 20)
            bias_warn = f"  ⚠️ 乖离率偏大（{bias5:+.1f}%），追高风险高\n"
        else:
            bias_warn = ""

        # 关键均线支撑/压力描述
        pos_lines = []
        if ma60:
            rel60 = "上方（支撑）" if close > ma60 else "下方（压力）"
            pos_lines.append(f"MA60={ma60:.2f}（60日线，{rel60}）")
        if ma120:
            rel120 = "上方（支撑）" if close > ma120 else "下方（压力）"
            pos_lines.append(f"MA120={ma120:.2f}（半年线，{rel120}）")
        if ma250:
            rel250 = "上方（牛市格局）" if close > ma250 else "下方（年线压制，偏弱）"
            pos_lines.append(f"MA250={ma250:.2f}（年线，{rel250}）")

        # 组装内容
        ma_line = (
            f"- MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}"
            if ma5 and ma10 and ma20 else "- 均线数据不足"
        )
        if ma60:  ma_line += f"  MA60={ma60:.2f}"
        if ma120: ma_line += f"  MA120={ma120:.2f}"
        if ma250: ma_line += f"  MA250={ma250:.2f}"

        content = (
            f"**{alignment}**\n"
            f"- 当前价: {close:.2f}\n"
            f"{ma_line}\n"
            f"- 乖离率MA5: {bias5:+.1f}%  乖离率MA20: {bias20:+.1f}%\n"
            + bias_warn
            + ("\n".join(f"- {l}" for l in pos_lines) + "\n" if pos_lines else "")
        )
        signal = self._score_to_signal(score)
        return Section(key="ma_system", title="均线系统", content=content,
                       data={"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
                             "ma120": ma120, "ma250": ma250,
                             "close": close, "bias5": bias5,
                             "above_ma250": above_ma250, "above_ma120": above_ma120},
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
        tail20 = df.tail(20)
        recent_str = tail20[["date", "open", "high", "low", "close", "volume"]].to_string(index=False)
        last = df.iloc[-1]
        ma5  = round(float(last.get("ma5")  or 0), 2)
        ma10 = round(float(last.get("ma10") or 0), 2)
        ma20 = round(float(last.get("ma20") or 0), 2)
        ma60 = round(float(last.get("ma60") or 0), 2)
        dif  = round(float(last.get("dif")  or 0), 4)
        dea  = round(float(last.get("dea")  or 0), 4)
        rsi6 = round(float(last.get("rsi6") or 50), 1)
        vol_ratio = round(float(last.get("vol_ratio") or 1.0), 2)

        prompt = f"""你是专业技术分析师，请对 {stock_name} 最近20日K线形态进行深度分析。

【近20日K线数据】
{recent_str}

【当前指标快照】
- 均线：MA5={ma5}  MA10={ma10}  MA20={ma20}  MA60={ma60}
- MACD：DIF={dif}  DEA={dea}
- RSI(6)={rsi6}  量比={vol_ratio}x

【要求】请输出以下内容，每点都要结合实际数据（日期、价格）说明：

1. **识别的形态**
   - 形态名称（如：双底/头肩顶/三角收敛/旗形/楔形/W底等）
   - 具体引用：形态的关键价格点（注明日期和价格，如"X月X日高点XX，X月X日回落至XX形成左肩"）
   - 可靠性：高/中/低，并说明理由（成交量配合/对称性/时间周期）

2. **形态暗示走势**
   - 突破方向及目标价位（给出具体数字，如"若有效突破XX，目标看XX-XX"）
   - 突破确认条件（收盘价/成交量要求）

3. **关键支撑与阻力**
   - 近期支撑位：XX（对应X月X日低点/均线）
   - 近期阻力位：XX（对应X月X日高点/均线压力）

4. **操作建议**
   - 当前位置建议（持有/减仓/加仓/观望）
   - 入场条件：满足【具体价格或信号】时可考虑操作
   - 止损设置：XX价格以下止损（基于形态失效判断）

输出最后一行格式：【信号】买入/观望/持有/减仓/卖出"""
        try:
            content = llm_call(prompt).strip()
        except Exception as e:
            content = f"K线形态分析失败：{e}"
        # 提取信号
        score, signal = 50, "hold"
        signal_map = {"买入": ("buy", 72), "观望": ("watch", 58), "持有": ("hold", 50),
                      "减仓": ("hold", 42), "卖出": ("sell", 28)}
        for label, (sig, sc) in signal_map.items():
            if f"【信号】{label}" in content:
                signal, score = sig, sc
                break
        return Section(key="pattern", title="K线形态", content=content, score=score, signal=signal)

    def _analyze_wave_llm(self, df, stock_name, llm_call) -> Section:
        tail = df.tail(60)
        closes = tail["close"].round(2).tolist()
        highs  = tail["high"].round(2).tolist()
        lows   = tail["low"].round(2).tolist()
        dates  = tail["date"].tolist()
        # 近期高低点
        max_idx = int(tail["high"].idxmax() - tail.index[0])
        min_idx = int(tail["low"].idxmin()  - tail.index[0])
        recent_high_date  = dates[max_idx] if max_idx < len(dates) else ""
        recent_high_price = highs[max_idx]  if max_idx < len(highs) else ""
        recent_low_date   = dates[min_idx]  if min_idx < len(dates) else ""
        recent_low_price  = lows[min_idx]   if min_idx < len(lows)  else ""
        last = df.iloc[-1]
        cur_price = round(float(last["close"]), 2)
        ma20  = round(float(last.get("ma20")  or 0), 2)
        ma60  = round(float(last.get("ma60")  or 0), 2)
        ma120 = round(float(last.get("ma120") or 0), 2)
        ma250 = round(float(last.get("ma250") or 0), 2)

        prompt = f"""你是波浪理论专家，请对 {stock_name} 进行专业波浪分析。

【近60日收盘价序列】（时间从早到晚）
{closes}

【区间高低点参考】
- 近60日高点：{recent_high_price}（{recent_high_date}）
- 近60日低点：{recent_low_price}（{recent_low_date}）
- 当前价格：{cur_price}  MA20={ma20}  MA60={ma60}  MA120={ma120}（半年线）  MA250={ma250}（年线）

【要求】请进行专业波浪分析，必须包含以下内容：

1. **波浪计数**
   - 判断大级别趋势背景（上升趋势/下降趋势/整理）
   - 当前处于第几浪（1/2/3/4/5浪，或A/B/C调整浪），给出理由
   - 关键：引用具体价格点说明浪的起止（如"从X价格启动第X浪，运行至X价格"）

2. **当前浪的状态**
   - 该浪是否已完成或进行中
   - 浪内部结构（如正处于5浪中的iii子浪）

3. **下一步预判**
   - 最可能的走势及目标价位区间（给出具体数字）
   - 次要情景（如主情景失效则转为）
   - 关键变盘时间窗口（如有周期规律则指出）

4. **关键点位**
   - 主要支撑：XX（若跌破则波浪计数修正）
   - 主要阻力：XX（突破后下一目标XX）
   - 浪的失效位：XX（跌破/突破此位则当前计数作废）

5. **操作建议**
   - 当前适合的操作策略（分批买/持有/减仓等）
   - 入场价位：XX附近，止损：XX，目标：XX

输出最后一行格式：【信号】买入/观望/持有/减仓/卖出"""
        try:
            content = llm_call(prompt).strip()
        except Exception as e:
            content = f"波浪分析失败：{e}"
        score, signal = 50, "hold"
        signal_map = {"买入": ("buy", 72), "观望": ("watch", 58), "持有": ("hold", 50),
                      "减仓": ("hold", 42), "卖出": ("sell", 28)}
        for label, (sig, sc) in signal_map.items():
            if f"【信号】{label}" in content:
                signal, score = sig, sc
                break
        return Section(key="wave", title="波浪理论", content=content, score=score, signal=signal)

    def _analyze_chan_llm(self, df, stock_name, llm_call) -> Section:
        tail = df.tail(60)
        closes = tail["close"].round(2).tolist()
        highs  = tail["high"].round(2).tolist()
        lows   = tail["low"].round(2).tolist()
        dates  = tail["date"].tolist()
        last = df.iloc[-1]
        cur_price = round(float(last["close"]), 2)
        dif  = round(float(last.get("dif")  or 0), 4)
        dea  = round(float(last.get("dea")  or 0), 4)
        rsi6 = round(float(last.get("rsi6") or 50), 1)
        ma5   = round(float(last.get("ma5")   or 0), 2)
        ma20  = round(float(last.get("ma20")  or 0), 2)
        ma60  = round(float(last.get("ma60")  or 0), 2)
        ma120 = round(float(last.get("ma120") or 0), 2)
        ma250 = round(float(last.get("ma250") or 0), 2)
        vol_ratio = round(float(last.get("vol_ratio") or 1.0), 2)
        # 日期标注的序列（每10个标注一次）
        labeled = []
        for i, (d, c, h, lo) in enumerate(zip(dates, closes, highs, lows)):
            if i % 10 == 0 or i == len(dates) - 1:
                labeled.append(f"{d}: 收{c} 高{h} 低{lo}")
        labeled_str = "\n".join(labeled)

        prompt = f"""你是缠论专家，请对 {stock_name} 进行专业缠论分析。

【近60日价格序列（关键节点标注）】
{labeled_str}

【当前指标】
当前价={cur_price}  MA5={ma5}  MA20={ma20}  MA60={ma60}  MA120={ma120}（半年线）  MA250={ma250}（年线）  DIF={dif}  DEA={dea}  RSI(6)={rsi6}  量比={vol_ratio}x

【近60日完整收盘价】
{closes}

【要求】请进行严格的缠论分析，必须包含：

1. **笔和线段结构**
   - 识别近期形成的笔（给出起止价格及日期）
   - 是否已形成线段或中枢，中枢区间是多少

2. **中枢分析**
   - 当前是否在中枢内/中枢上方/中枢下方运行
   - 中枢区间：XX ~ XX（对应具体价格区间）
   - 是否有中枢突破或中枢扩张迹象

3. **背驰判断**（这是核心，必须给出具体分析）
   - 是否出现顶背驰：如是，说明哪几笔的MACD（柱面积/DIF绝对值）在减小，对应价格反而新高
   - 是否出现底背驰：如是，说明哪几笔的MACD在减小，对应价格反而新低
   - 背驰强度（弱背驰/背驰）及判断依据

4. **买卖点判断**
   - 当前最近形成的买卖点类型（一买/二买/三买/一卖/二卖/三卖）
   - 给出具体的点位价格及判断依据

5. **后市预判**
   - 主要走势预判及目标位（给出价格区间）
   - 关键确认信号（满足什么条件则确认方向）

6. **操作建议**
   - 建议操作：买入/持有/减仓/卖出（附条件）
   - 参考止损位：XX（基于缠论笔段失效判断）
   - 目标位：XX ~ XX

输出最后一行格式：【信号】买入/观望/持有/减仓/卖出"""
        try:
            content = llm_call(prompt).strip()
        except Exception as e:
            content = f"缠论分析失败：{e}"
        score, signal = 50, "hold"
        signal_map = {"买入": ("buy", 72), "观望": ("watch", 58), "持有": ("hold", 50),
                      "减仓": ("hold", 42), "卖出": ("sell", 28)}
        for label, (sig, sc) in signal_map.items():
            if f"【信号】{label}" in content:
                signal, score = sig, sc
                break
        return Section(key="chan", title="缠论分析", content=content, score=score, signal=signal)

    def _analyze_chip(self, df: pd.DataFrame, stock_code: str) -> Section:
        """筹码分布：成本集中度分析"""
        try:
            import akshare as ak
            cyq = ak.stock_cyq_em(symbol=stock_code)
            if cyq is not None and not cyq.empty and len(cyq) >= 5:
                close_col = next((c for c in cyq.columns if "收盘" in c or "close" in c.lower()), None)
                profit_col = next((c for c in cyq.columns if "获利" in c or "profit" in c.lower()), None)
                cost_col   = next((c for c in cyq.columns if "成本" in c or "cost" in c.lower()), None)

                current_price = df.iloc[-1]["close"]

                if profit_col and not cyq[profit_col].isna().all():
                    profit_ratio = float(cyq[profit_col].iloc[-1])
                    if profit_ratio > 1:
                        profit_ratio = profit_ratio / 100.0
                else:
                    profit_col_fallback = cyq.select_dtypes(include=[float, int]).columns
                    profit_ratio = None
                    if close_col:
                        try:
                            closes_in_cyq = cyq[close_col].dropna()
                            profit_ratio = float((closes_in_cyq < current_price).sum()) / max(len(closes_in_cyq), 1)
                        except Exception:
                            pass
                    if profit_ratio is None:
                        profit_ratio = 0.5

                # 主要套牢/密集区
                if cost_col and not cyq[cost_col].isna().all():
                    cost_median = float(cyq[cost_col].median())
                else:
                    cost_median = float(df.tail(60)["close"].mean()) if len(df) >= 60 else float(df["close"].mean())

                support   = round(cost_median * 0.97, 2)
                pressure  = round(cost_median * 1.05, 2)

                profit_pct = profit_ratio * 100
                if profit_pct > 70 and current_price >= cost_median:
                    score, status = 65, "获利盘丰厚，上方压力较大但仍处强势"
                elif profit_pct < 30:
                    score, status = 70, "套牢盘少，反弹空间相对充裕"
                else:
                    score, status = 50, "筹码结构中性"

                content = (
                    f"**获利盘比例={profit_pct:.1f}%**\n"
                    f"- 密集成本区中位数：{cost_median:.2f}\n"
                    f"- 估算支撑位：{support}  压力位：{pressure}\n"
                    f"- 状态：{status}\n"
                )
                return Section(key="chip", title="筹码分布",
                               content=content,
                               data={"profit_ratio": profit_ratio, "cost_median": cost_median,
                                     "support": support, "pressure": pressure},
                               score=score, signal=self._score_to_signal(score))

            # akshare 返回空，降级到估算
            raise ValueError("cyq data empty")

        except Exception:
            # 降级：用近60日均价估算
            tail = df.tail(60) if len(df) >= 60 else df
            avg_cost = float(tail["close"].mean())
            current_price = float(df.iloc[-1]["close"])
            profit_est = float((tail["close"] < current_price).sum()) / max(len(tail), 1)
            profit_pct = profit_est * 100
            score = 65 if profit_pct > 70 else (70 if profit_pct < 30 else 50)
            content = (
                f"**获利盘估算={profit_pct:.1f}%**（基于近{len(tail)}日均价估算，非精确筹码）\n"
                f"- 近期均价：{avg_cost:.2f}\n"
                f"- 当前价格：{current_price:.2f}\n"
            )
            return Section(key="chip", title="筹码分布（估算）",
                           content=content,
                           data={"profit_ratio": profit_est, "avg_cost": avg_cost},
                           score=score, signal=self._score_to_signal(score))

    def _analyze_turnover(self, df: pd.DataFrame) -> Section:
        """换手率趋势分析（近30日）"""
        try:
            # 确定换手率列
            if "turnover_rate" in df.columns and not df["turnover_rate"].isna().all():
                tr = df["turnover_rate"].copy()
            elif all(c in df.columns for c in ["amount", "close", "volume"]):
                # amount 单位通常为元，volume 为股数，估算流通市值比
                tr = df["volume"] / (df["amount"] / df["close"].replace(0, np.nan) + 1e-9) * 100
            elif "volume" in df.columns:
                vol_mean = df["volume"].mean()
                tr = df["volume"] / (vol_mean + 1e-9) * 2  # 粗估，仅供趋势
            else:
                return Section(key="turnover", title="换手率趋势",
                               content="缺少换手率及成交量数据，无法分析",
                               score=50, signal="hold")

            tr = tr.replace([np.inf, -np.inf], np.nan).fillna(method="ffill")
            n30 = min(30, len(tr))
            n5  = min(5, len(tr))
            if n30 < 5:
                return Section(key="turnover", title="换手率趋势",
                               content="数据不足（少于5日），无法分析换手率趋势",
                               score=50, signal="hold")

            tr30 = tr.iloc[-n30:]
            tr5  = tr.iloc[-n5:]
            avg30 = float(tr30.mean())
            avg5  = float(tr5.mean())
            max30 = float(tr30.max())
            min30 = float(tr30.min())

            close5_change = float(df.iloc[-1]["close"] - df.iloc[-n5]["close"]) / (float(df.iloc[-n5]["close"]) + 1e-9) * 100

            # 趋势判断
            if avg5 > avg30 * 1.5 and close5_change > 0:
                status, score = "换手率上升+价格上涨（健康上涨）", 68
            elif avg5 > avg30 * 1.5 and close5_change < 0:
                status, score = "换手率上升+价格下跌（恐慌出货）", 30
            elif avg5 < avg30 * 0.5:
                status, score = "换手率持续低迷（市场观望）", 50
            elif avg5 > avg30 * 1.2:
                status, score = "换手率略有上升（温和活跃）", 58
            else:
                status, score = "换手率平稳", 50

            content = (
                f"**近5日均换手={avg5:.2f}%  近30日均换手={avg30:.2f}%**\n"
                f"- 30日区间：[{min30:.2f}%, {max30:.2f}%]\n"
                f"- 近5日价格变动：{close5_change:+.2f}%\n"
                f"- 状态：{status}\n"
            )
            return Section(key="turnover", title="换手率趋势",
                           content=content,
                           data={"avg5": avg5, "avg30": avg30, "max30": max30, "min30": min30},
                           score=score, signal=self._score_to_signal(score))

        except Exception as e:
            return Section(key="turnover", title="换手率趋势",
                           content=f"换手率分析失败：{e}",
                           score=50, signal="hold")

    def _analyze_margin(self, df: pd.DataFrame, stock_code: str) -> Section:
        """融资融券余额趋势分析"""
        try:
            import akshare as ak

            # 判断市场
            is_sh = stock_code.startswith("6")
            margin_df = None

            try:
                if is_sh:
                    margin_df = ak.stock_margin_detail_sse(stock=stock_code)
                else:
                    margin_df = ak.stock_margin_detail_szse(stock=stock_code)
            except Exception:
                try:
                    if is_sh:
                        margin_df = ak.stock_margin_sse(stock=stock_code)
                except Exception:
                    pass

            if margin_df is None or margin_df.empty:
                raise ValueError("margin data empty")

            margin_df = margin_df.tail(10).reset_index(drop=True)

            # 找融资余额列
            rz_col = next(
                (c for c in margin_df.columns if "融资余额" in c or ("融资" in c and "余额" in c)),
                None
            )
            if rz_col is None:
                rz_col = next(
                    (c for c in margin_df.columns if "余额" in c),
                    margin_df.columns[-1]
                )

            rz = pd.to_numeric(margin_df[rz_col], errors="coerce").dropna()
            if len(rz) < 2:
                raise ValueError("insufficient margin rows")

            latest = float(rz.iloc[-1])
            earliest = float(rz.iloc[0])
            change_pct = (latest - earliest) / (abs(earliest) + 1e-9) * 100

            # 线性回归斜率（趋势方向）
            x = np.arange(len(rz))
            slope = float(np.polyfit(x, rz.values, 1)[0])
            slope_pct = slope / (abs(earliest) + 1e-9) * 100

            if change_pct > 10:
                score, status = 68, f"融资余额增加{change_pct:+.1f}%（资金加仓，看多情绪上升）"
            elif change_pct < -10:
                score, status = 35, f"融资余额减少{change_pct:+.1f}%（资金撤退，看多情绪减弱）"
            else:
                score, status = 52, f"融资余额平稳（变动{change_pct:+.1f}%）"

            content = (
                f"**最新融资余额={latest/1e8:.2f}亿  10日前={earliest/1e8:.2f}亿**\n"
                f"- 变动幅度：{change_pct:+.1f}%\n"
                f"- 趋势斜率：{'↑ 上升' if slope > 0 else '↓ 下降'}\n"
                f"- 状态：{status}\n"
            )
            return Section(key="margin", title="融资融券",
                           content=content,
                           data={"latest": latest, "earliest": earliest, "change_pct": change_pct},
                           score=score, signal=self._score_to_signal(score))

        except Exception as e:
            return Section(key="margin", title="融资融券",
                           content=f"融资券数据获取失败（可能不在两融标的）：{e}",
                           score=50, signal="hold")

    def _analyze_llm_tech(self, df: pd.DataFrame, stock_name: str, llm_call, sections: list) -> Section:
        """基于所有已计算的量化指标，让 LLM 做综合技术精讲，输出具体点位和操作建议。"""
        last  = df.iloc[-1]
        prev5 = df.tail(6).iloc[0]  # 5日前
        close  = round(float(last["close"]), 2)
        close5 = round(float(prev5["close"]), 2)
        chg5   = round((close - close5) / close5 * 100, 2)

        # 组装量化快照
        ma5   = round(float(last.get("ma5")   or 0), 2)
        ma10  = round(float(last.get("ma10")  or 0), 2)
        ma20  = round(float(last.get("ma20")  or 0), 2)
        ma60  = round(float(last.get("ma60")  or 0), 2)
        ma120 = round(float(last.get("ma120") or 0), 2)
        ma250 = round(float(last.get("ma250") or 0), 2)
        dif  = round(float(last.get("dif")  or 0), 4)
        dea  = round(float(last.get("dea")  or 0), 4)
        bar  = round(float(last.get("macd_bar") or 0), 4)
        rsi6 = round(float(last.get("rsi6")  or 50), 1)
        r12  = round(float(last.get("rsi12") or 50), 1)
        k    = round(float(last.get("kdj_k") or 50), 1)
        d_   = round(float(last.get("kdj_d") or 50), 1)
        j    = round(float(last.get("kdj_j") or 50), 1)
        boll_u = round(float(last.get("boll_upper") or 0), 2)
        boll_m = round(float(last.get("boll_mid")   or 0), 2)
        boll_l = round(float(last.get("boll_lower") or 0), 2)
        wr14   = round(float(last.get("wr14") or -50), 1)
        vol_r  = round(float(last.get("vol_ratio") or 1.0), 2)

        # 近5日涨跌幅序列
        tail5 = df.tail(5)
        daily_chg = []
        for i in range(len(tail5)):
            row = tail5.iloc[i]
            c = round(float(row["close"]), 2)
            if i > 0:
                prev_c = round(float(tail5.iloc[i-1]["close"]), 2)
                pct = round((c - prev_c) / prev_c * 100, 2)
                daily_chg.append(f"{row['date']}({pct:+.2f}%)")
            else:
                daily_chg.append(f"{row['date']}(基准)")

        # 从已计算 sections 中提取背离摘要
        div_summary = ""
        for s in sections:
            if s.key == "divergence" and s.content:
                first_line = s.content.strip().split("\n")[0]
                div_summary = f"背离检测：{first_line}"
                break

        # 近5日最高/最低
        tail5_high = round(float(df.tail(5)["high"].max()), 2)
        tail5_low  = round(float(df.tail(5)["low"].min()), 2)

        prompt = f"""你是顶尖A股技术分析师，请对 {stock_name}（当前价{close}）进行技术面精讲分析。

【指标快照】
均线：MA5={ma5}  MA10={ma10}  MA20={ma20}  MA60={ma60}  MA120={ma120}  MA250={ma250}
MACD：DIF={dif}  DEA={dea}  柱={bar}（{'零轴上方' if dif > 0 else '零轴下方'}）
RSI(6)={rsi6}  RSI(12)={r12}  KDJ K={k} D={d_} J={j}
布林带：上{boll_u}  中{boll_m}  下{boll_l}  WR(14)={wr14}
量比={vol_r}x  近5日涨跌={chg5:+.2f}%  近5日区间=[{tail5_low},{tail5_high}]
{div_summary}

请按以下6个方面分析，每项结合具体数值，总计400字以内：

## 1. 趋势判断
均线排列（多头/空头/缠绕）+ 价格与MA60/MA120/MA250的位置关系 → 大/中/短三级趋势结论

## 2. 动能解读
MACD零轴位置+柱线扩缩含义、RSI区间判断、KDJ J值状态、多指标是否共振

## 3. 关键价位
- 阻力：列2个具体价格（注明来源）
- 支撑：列2个具体价格（注明来源）
- 变盘位：突破/跌破哪个价改变趋势

## 4. 量价与风险
量比+近期价格的量价关系解读；当前最主要的1个技术风险

## 5. 操作建议
短线（1-5日）：入场区间+止损+目标（具体数字）
中线（1-4周）：建仓条件+止损+目标（具体数字）

## 6. 仓位建议
当前看多/看空强度，建议仓位比例

输出最后一行格式：【信号】买入/观望/持有/减仓/卖出"""

        try:
            content = llm_call(prompt).strip()
        except Exception as e:
            logger.warning("[llm_tech] LLM 调用失败 %s: %s", stock_name, e)
            content = f"技术综合分析失败：{e}"

        # LLM 返回空时用规则降级
        if not content:
            logger.warning("[llm_tech] LLM 返回空内容，降级为规则摘要")
            ma_line = f"MA5={ma5} MA10={ma10} MA20={ma20} MA60={ma60}"
            content = (
                f"**技术指标快照（LLM 未返回）**\n"
                f"- 均线：{ma_line}\n"
                f"- MACD：DIF={dif} DEA={dea} 柱={bar}（{'零轴上方' if dif > 0 else '零轴下方'}）\n"
                f"- RSI(6)={rsi6}  KDJ J={j}\n"
                f"- 布林带：上{boll_u} 中{boll_m} 下{boll_l}\n"
                f"- 量比={vol_r}x\n"
                f"\n（LLM 综合精讲暂不可用，请检查 LLM API 配置）"
            )

        score, signal = 50, "hold"
        signal_map = {"买入": ("buy", 72), "观望": ("watch", 58), "持有": ("hold", 50),
                      "减仓": ("hold", 42), "卖出": ("sell", 28)}
        for label, (sig, sc) in signal_map.items():
            if f"【信号】{label}" in content:
                signal, score = sig, sc
                break
        return Section(key="llm_tech", title="技术综合精讲（LLM）",
                       content=content, score=score, signal=signal)

    # ── 超买超卖综合 + 背离检测（新增）──────────────────────
    def _analyze_overbought(self, df) -> Section:
        """综合 RSI/KDJ/威廉WR/布林位置，给出统一的超买超卖结论。"""
        last = df.iloc[-1]
        r6  = float(last.get("rsi6", 50) or 50)
        j   = float(last.get("kdj_j", 50) or 50)
        wr  = float(last.get("wr14", -50) or -50)
        upper = last.get("boll_upper"); lower = last.get("boll_lower"); close = last["close"]
        boll_pos = None
        if upper is not None and not pd.isna(upper):
            boll_pos = (close - lower) / (upper - lower + 1e-9) * 100

        # 各指标投票：+1 超买、-1 超卖、0 中性
        ob_votes, os_votes, lines = 0, 0, []
        # RSI(6)：>70 超买 <30 超卖
        if r6 > 70:   ob_votes += 1; lines.append(f"RSI(6)={r6:.0f} 超买")
        elif r6 < 30: os_votes += 1; lines.append(f"RSI(6)={r6:.0f} 超卖")
        else:         lines.append(f"RSI(6)={r6:.0f} 中性")
        # KDJ J：>90 超买 <10 超卖
        if j > 90:    ob_votes += 1; lines.append(f"KDJ_J={j:.0f} 超买")
        elif j < 10:  os_votes += 1; lines.append(f"KDJ_J={j:.0f} 超卖")
        else:         lines.append(f"KDJ_J={j:.0f} 中性")
        # 威廉 WR(14)：>-20 超买 <-80 超卖
        if wr > -20:  ob_votes += 1; lines.append(f"WR(14)={wr:.0f} 超买")
        elif wr < -80: os_votes += 1; lines.append(f"WR(14)={wr:.0f} 超卖")
        else:         lines.append(f"WR(14)={wr:.0f} 中性")
        # 布林位置：>90% 超买 <10% 超卖
        if boll_pos is not None:
            if boll_pos > 90:   ob_votes += 1; lines.append(f"布林位置={boll_pos:.0f}% 触上轨")
            elif boll_pos < 10: os_votes += 1; lines.append(f"布林位置={boll_pos:.0f}% 触下轨")
            else:               lines.append(f"布林位置={boll_pos:.0f}% 中部")

        total = ob_votes + os_votes
        if ob_votes >= 3:
            status, score = f"🔴 强烈超买（{ob_votes}/4 指标），短期回调风险高", 25
        elif os_votes >= 3:
            status, score = f"🟢 强烈超卖（{os_votes}/4 指标），反弹概率大", 75
        elif ob_votes == 2:
            status, score = f"🟠 偏超买（{ob_votes}/4），注意风险", 38
        elif os_votes == 2:
            status, score = f"🔵 偏超卖（{os_votes}/4），可关注", 62
        else:
            status, score = "⚪ 中性区域，无明显超买超卖", 50

        content = "**" + status + "**\n" + "\n".join(f"- {x}" for x in lines) + \
                  "\n- 判据：多指标共振时信号更可靠（≥3 项同向为强信号）\n"
        return Section(key="overbought", title="超买超卖综合", content=content,
                       data={"rsi6": r6, "kdj_j": j, "wr14": wr, "boll_pos": boll_pos,
                             "ob_votes": ob_votes, "os_votes": os_votes},
                       score=score, signal=self._score_to_signal(score))

    def _analyze_divergence(self, df) -> Section:
        """
        多状态背离检测引擎（专业量化实现）。

        特性（区别于简单的两点对比）：
        - 摆动点：分形(Fractal, 右侧k根确认防未来函数) + ATR幅度清洗
        - 类型：常规背离(反转) + 隐藏背离(延续)，MACD-DIF 与 RSI12 双指标
        - 成熟度状态机：无迹象 → 迹象浮现(EARLY) → 进行中(FORMING) → 已确认(CONFIRMED) → 失效
          用"已确认摆动点 + 实时临时锚点"做进行时预测，不必等背离完全形成
        - 强度评分：价格位移/指标反向差/极值区/量能/时间跨度 多因子加权 × 成熟度系数
        """
        import numpy as np
        n = len(df)
        if n < 30:
            return Section(key="divergence", title="背离检测",
                           content="数据不足（少于30日），无法可靠检测背离", score=50, signal="hold")

        close = df["close"].values.astype(float)
        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        dif   = df["dif"].values.astype(float)   if "dif"   in df.columns else None
        rsi   = df["rsi12"].values.astype(float) if "rsi12" in df.columns else None
        macd_bar = df["macd_bar"].values.astype(float) if "macd_bar" in df.columns else None
        vr    = df["vol_ratio"].values.astype(float) if "vol_ratio" in df.columns else None
        ma20  = df["close"].rolling(20).mean().values

        # ATR14（波动率归一化基准）
        atr = self._atr(high, low, close, 14)
        now = n - 1
        K = 3               # 分形半宽（右侧确认根数）
        MIN_GAP, MAX_GAP = 5, 55
        RECENT_WIN = 90     # 只看近90交易日内形成的背离（更早的时效性差，且避免长历史刷屏）

        # 摆动点（价格高/低点，已右侧确认）+ ATR 幅度清洗
        pl = self._fractal_idx(low,  K, kind="low")
        ph = self._fractal_idx(high, K, kind="high")
        pl = self._clean_pivots(pl, low,  atr, MIN_GAP)
        ph = self._clean_pivots(ph, high, atr, MIN_GAP)

        signals = []

        # ── 已确认背离（遍历相邻同类摆动点，MACD + RSI 双指标）──
        for ind, ind_name, k_ind in [(dif, "MACD", "dif"), (rsi, "RSI", "rsi")]:
            if ind is None:
                continue
            # 底背离 / 隐藏底背离
            for a, b in zip(pl, pl[1:]):
                if b < now - RECENT_WIN:          # 第二个摆动点须在近期窗口内
                    continue
                if not (MIN_GAP <= b - a <= MAX_GAP):
                    continue
                sig = self._classify_pair(
                    kind="bottom", i1=a, i2=b, price=low, ind=ind, ind_name=ind_name,
                    atr=atr, rsi=rsi, ma20=ma20, vr=vr, close=close)
                if sig:
                    signals.append(sig)
            # 顶背离 / 隐藏顶背离
            for a, b in zip(ph, ph[1:]):
                if b < now - RECENT_WIN:
                    continue
                if not (MIN_GAP <= b - a <= MAX_GAP):
                    continue
                sig = self._classify_pair(
                    kind="top", i1=a, i2=b, price=high, ind=ind, ind_name=ind_name,
                    atr=atr, rsi=rsi, ma20=ma20, vr=vr, close=close)
                if sig:
                    signals.append(sig)

        # ── 进行时 / 早期背离（临时锚点 + 状态机，MACD 主判）──
        emerging = self._emerging(
            close, low, high, dif, rsi, macd_bar, atr, ma20, pl, ph, now)
        signals.extend(emerging)

        # 去重：同类型同区间保留强度最高
        signals = self._dedup(signals)

        return self._build_divergence_section(signals)

    # ── 背离引擎辅助方法 ──────────────────────────────────────
    @staticmethod
    def _atr(high, low, close, n=14):
        import numpy as np
        tr = np.zeros(len(close))
        tr[0] = high[0] - low[0]
        for i in range(1, len(close)):
            tr[i] = max(high[i] - low[i],
                        abs(high[i] - close[i-1]),
                        abs(low[i]  - close[i-1]))
        atr = np.full(len(close), np.nan)
        if len(close) >= n:
            atr[n-1] = tr[:n].mean()
            for i in range(n, len(close)):
                atr[i] = (atr[i-1] * (n-1) + tr[i]) / n
        # 前段用累计均值兜底，避免 NaN
        for i in range(len(close)):
            if np.isnan(atr[i]):
                atr[i] = tr[:i+1].mean() if i > 0 else tr[0]
        return atr

    @staticmethod
    def _fractal_idx(arr, k, kind="low"):
        """分形摆动点：严格不等号 + 右侧 k 根确认（防未来函数）。返回升序下标。"""
        out = []
        m = len(arr)
        for i in range(k, m - k):
            left  = arr[i-k:i]
            right = arr[i+1:i+k+1]
            if kind == "low"  and arr[i] < left.min() and arr[i] < right.min():
                out.append(i)
            elif kind == "high" and arr[i] > left.max() and arr[i] > right.max():
                out.append(i)
        return out

    @staticmethod
    def _clean_pivots(pivots, prices, atr, min_gap, atr_mult=1.2):
        """清洗摆动点：间隔<min_gap 合并保留更极端者；幅度<atr_mult*ATR 视为同一波剔除。"""
        kept = []
        for idx in pivots:
            if not kept:
                kept.append(idx); continue
            prev = kept[-1]
            if idx - prev < min_gap:
                if abs(prices[idx]) and abs(prices[idx] - prices[prev]) > 0:
                    # 保留更极端的（低点取更低、高点由调用侧保证方向一致，这里按价差方向）
                    kept[-1] = idx if abs(prices[idx]) else prev
                continue
            if abs(prices[idx] - prices[prev]) < atr_mult * (atr[idx] or 1e-9):
                continue
            kept.append(idx)
        return kept

    def _classify_pair(self, kind, i1, i2, price, ind, ind_name, atr, rsi, ma20, vr, close):
        """判定一对摆动点是否构成背离，返回信号 dict 或 None。"""
        p1, p2 = price[i1], price[i2]
        v1, v2 = ind[i1], ind[i2]

        dtype = None       # regular_bull/bear, hidden_bull/bear
        if kind == "bottom":
            if p2 < p1 and v2 > v1:
                dtype = "regular_bull"       # 价格新低+指标抬高 → 常规底背离(反转看涨)
            elif p2 > p1 and v2 < v1:
                dtype = "hidden_bull"        # 价格抬高+指标新低 → 隐藏底背离(延续看涨)
        else:  # top
            if p2 > p1 and v2 < v1:
                dtype = "regular_bear"       # 价格新高+指标走弱 → 常规顶背离(反转看跌)
            elif p2 < p1 and v2 > v1:
                dtype = "hidden_bear"        # 价格走低+指标抬高 → 隐藏顶背离(延续看跌)
        if dtype is None:
            return None

        # 过滤：常规背离要区域过滤（RSI），隐藏背离要趋势过滤
        if dtype.startswith("regular") and ind_name == "RSI" and rsi is not None:
            if kind == "bottom" and not (rsi[i1] < 35 or rsi[i2] < 35):
                return None
            if kind == "top" and not (rsi[i1] > 65 or rsi[i2] > 65):
                return None
        if dtype.startswith("hidden") and ma20 is not None and not pd.isna(ma20[i2]):
            if "bull" in dtype and not (close[i2] > ma20[i2]):
                return None
            if "bear" in dtype and not (close[i2] < ma20[i2]):
                return None

        # 成熟度：i2 已是确认摆动点（能进这函数就已右侧确认）→ CONFIRMED
        state = "CONFIRMED"
        score = self._strength(dtype, kind, i1, i2, price, ind, ind_name, atr, rsi, vr)
        score = int(round(score * 1.0))     # confirm_mult=1.0

        # 携带日期和具体价格/指标值，供渲染时引用
        df = getattr(self, "_df", None)
        d1 = str(df.iloc[i1]["date"]) if df is not None and i1 < len(df) else str(i1)
        d2 = str(df.iloc[i2]["date"]) if df is not None and i2 < len(df) else str(i2)

        return {"type": dtype, "indicator": ind_name, "maturity": state,
                "score": score, "i1": i1, "i2": i2,
                "date1": d1, "date2": d2,
                "price1": round(float(p1), 3), "price2": round(float(p2), 3),
                "ind1": round(float(v1), 4),   "ind2": round(float(v2), 4)}

    def _strength(self, dtype, kind, i1, i2, price, ind, ind_name, atr, rsi, vr):
        """八因子加权强度评分 0-100（简化版，保留核心因子）。"""
        a = atr[i2] or 1e-9
        # 价格位移
        price_f = min(abs(price[i2] - price[i1]) / a / 2.5, 1.0)
        # 指标反向差
        ind_diff = (ind[i2] - ind[i1]) if "bull" in dtype else (ind[i1] - ind[i2])
        k_ind = 15.0 if ind_name == "RSI" else max(a, abs(ind[i2]) + 1e-9)
        ind_f = min(max(ind_diff, 0) / k_ind, 1.0)
        align_f = min(price_f, ind_f)
        # 时间跨度梯形
        dt = i2 - i1
        if dt < 5 or dt > 80: time_f = 0.0
        elif 8 <= dt <= 40:   time_f = 1.0
        elif dt < 8:          time_f = (dt - 5) / 3.0
        else:                 time_f = (80 - dt) / 40.0
        # 极值区（仅常规背离，用 RSI）
        zone_f = 0.0
        if dtype.startswith("regular") and rsi is not None:
            zone_f = min(max((rsi[i2]-70)/20, 0), 1) if kind == "top" else min(max((30-rsi[i2])/20, 0), 1)
        # 量能（P2 缩量为正）
        vol_f = 0.0
        if vr is not None and vr[i1] > 0:
            vol_f = min(max((vr[i1] - vr[i2]) / vr[i1], 0), 1)
        raw = 100 * (0.22*zone_f + 0.20*ind_f + 0.18*align_f +
                     0.15*price_f + 0.13*time_f + 0.12*vol_f)
        if raw < 5:
            raw = 30 + 20 * align_f     # 无极值区/量能时给个保底（隐藏背离常见）
        return max(min(raw, 100), 0)

    def _emerging(self, close, low, high, dif, rsi, macd_bar, atr, ma20, pl, ph, now):
        """临时锚点 + 状态机：检测 EARLY(迹象浮现) / FORMING(进行中) 背离。"""
        import numpy as np
        out = []
        if dif is None:
            return out
        recent_cut = now - 55

        for kind in ("bottom", "top"):
            pivots = pl if kind == "bottom" else ph
            confirmed = [i for i in pivots if i <= now - 3 and i >= recent_cut]
            if not confirmed:
                continue
            p1_idx = confirmed[-1]           # 最近一个已确认摆动点
            seg = slice(p1_idx + 1, now + 1)
            if p1_idx + 1 > now:
                continue

            if kind == "bottom":
                live_idx = p1_idx + 1 + int(np.argmin(low[seg]))
                P1, P2 = low[p1_idx], low[live_idx]
            else:
                live_idx = p1_idx + 1 + int(np.argmax(high[seg]))
                P1, P2 = high[p1_idx], high[live_idx]
            I1, I2 = dif[p1_idx], dif[live_idx]
            bb = max(live_idx - p1_idx, 1)
            a = atr[now] or 1e-9

            # 价格-指标滚动相关（脱钩预警）
            W = 14
            rho = np.nan
            if now >= W:
                ret = np.diff(close[now-W:now+1]) / (close[now-W:now] + 1e-9)
                dind = np.diff(dif[now-W:now+1])
                if ret.std() > 0 and dind.std() > 0:
                    rho = float(np.corrcoef(ret, dind)[0, 1])

            state = None
            reason = ""
            if kind == "bottom":
                # FORMING：价格已破前低 + DIF 抬高
                if P2 < P1 and I2 > I1:
                    state, reason = "FORMING", "价格已创新低但 MACD 抬高，底背离进行中"
                else:
                    near = (P1 <= P2 <= P1 * 1.015) or (P2 - P1 <= 0.5 * a)
                    rsi_strong = rsi is not None and (rsi[live_idx] - rsi[p1_idx] >= 3)
                    if near and (I2 > I1 or rsi_strong):
                        state, reason = "EARLY", "价格逼近前低但指标已转强，底背离迹象浮现"
                    elif not np.isnan(rho) and rho < -0.3 and close[now] < ma20[now]:
                        state, reason = "EARLY", "价格与 MACD 走势脱钩（负相关），潜在底背离酝酿"
            else:  # top
                if P2 > P1 and I2 < I1:
                    state, reason = "FORMING", "价格已创新高但 MACD 走弱，顶背离进行中"
                else:
                    near = (P1 * 0.985 <= P2 <= P1) or (P1 - P2 <= 0.5 * a)
                    # 动能衰减：macd_bar 连续收缩
                    decay = False
                    if macd_bar is not None and now >= 3:
                        h = macd_bar[now-2:now+1]
                        decay = h[0] > h[1] > h[2] and h[0] > 0
                    if near and (I2 < I1 or decay):
                        state, reason = "EARLY", "价格逼近前高但动能衰减，顶背离迹象浮现"
                    elif decay and close[now] > ma20[now]:
                        state, reason = "EARLY", "MACD 柱连续收缩、量价动能脱钩，顶背离预警"

            if state:
                dtype = ("regular_bull" if kind == "bottom" else "regular_bear")
                base = self._strength(dtype, kind, p1_idx, live_idx, low if kind=="bottom" else high,
                                      dif, "MACD", atr, rsi, None)
                mult = 0.6 if state == "EARLY" else 0.85
                score = int(round(base * mult))
                # 早期信号给分不低于状态下限，便于展示
                score = max(score, 30 if state == "EARLY" else 42)

                df = getattr(self, "_df", None)
                d1 = str(df.iloc[p1_idx]["date"])  if df is not None and p1_idx < len(df) else str(p1_idx)
                d2 = str(df.iloc[live_idx]["date"]) if df is not None and live_idx < len(df) else str(live_idx)
                price_arr = low if kind == "bottom" else high
                out.append({"type": dtype + "_emerging", "indicator": "MACD",
                            "maturity": state, "score": score,
                            "i1": p1_idx, "i2": live_idx, "reason": reason,
                            "date1": d1, "date2": d2,
                            "price1": round(float(price_arr[p1_idx]), 3),
                            "price2": round(float(price_arr[live_idx]), 3),
                            "ind1": round(float(dif[p1_idx]), 4),
                            "ind2": round(float(dif[live_idx]), 4)})
        return out

    @staticmethod
    def _dedup(signals):
        """
        同「基础类型 + 指标 + 是否早期」只保留强度最高的 1 条，避免长历史多对摆动点刷屏。
        例：多个 RSI 常规顶背离只留最强那条。
        """
        best = {}
        for s in signals:
            base_type = s["type"].replace("_emerging", "")
            is_emerging = "_emerging" in s["type"]
            key = (base_type, s.get("indicator", ""), is_emerging)
            if key not in best or s["score"] > best[key]["score"]:
                best[key] = s
        return sorted(best.values(), key=lambda x: -x["score"])

    def _build_divergence_section(self, signals) -> Section:
        """把背离信号列表渲染成详细的 Section，包含具体 K 线引用、确认条件、操作建议。"""
        TYPE_LABEL = {
            "regular_bull": ("🟢 常规底背离", "反转看涨", "buy"),
            "regular_bear": ("🔴 常规顶背离", "反转看跌", "sell"),
            "hidden_bull":  ("🔵 隐藏底背离", "趋势延续（多）", "buy"),
            "hidden_bear":  ("🟠 隐藏顶背离", "趋势延续（空）", "sell"),
        }
        MAT_LABEL = {
            "EARLY":     ("迹象浮现",  "⚠️ 预警级"),
            "FORMING":   ("进行中",    "🔔 观察级"),
            "CONFIRMED": ("已确认",    "✅ 参考级"),
        }

        if not signals:
            return Section(key="divergence", title="背离检测",
                           content=(
                               "近期未检测到顶/底背离信号（含早期/进行中）。\n"
                               "- 检测方法：分形摆动点 + MACD(DIF)/RSI 双指标 + 成熟度状态机\n"
                               "- 背离生成需要近期存在可比较的相邻摆动高/低点，若行情单边运行则无法产生\n"
                           ),
                           data={"signals": []}, score=50, signal="hold")

        # 取当前价格辅助计算关键点位
        df = getattr(self, "_df", None)
        cur_price = float(df.iloc[-1]["close"]) if df is not None else 0.0
        ma5  = float(df.iloc[-1].get("ma5")  or 0) if df is not None else 0.0
        ma10 = float(df.iloc[-1].get("ma10") or 0) if df is not None else 0.0
        ma20 = float(df.iloc[-1].get("ma20") or 0) if df is not None else 0.0

        lines = []
        for s in signals[:5]:
            base_type = s["type"].replace("_emerging", "")
            is_emerging = "_emerging" in s["type"]
            label, meaning, _ = TYPE_LABEL.get(base_type, ("背离", "", "hold"))
            mat_short, mat_badge = MAT_LABEL.get(s["maturity"], (s["maturity"], ""))
            ind_name = s.get("indicator", "MACD")
            grade = "强" if s["score"] >= 70 else ("中" if s["score"] >= 45 else "弱")

            d1   = s.get("date1", "")
            d2   = s.get("date2", "")
            p1   = s.get("price1", 0.0)
            p2   = s.get("price2", 0.0)
            v1   = s.get("ind1",   0.0)
            v2   = s.get("ind2",   0.0)

            # 标题行
            lines.append(f"### {label}·{mat_short}  {mat_badge}（{ind_name}，强度 {s['score']}/{grade}）")

            # 具体 K 线引用
            if "bull" in base_type:
                lines.append(f"**K线区间**：{d1} 低点 **{p1}** → {d2} 低点 **{p2}**")
                if p2 < p1:
                    lines.append(f"  价格：{d1}低({p1}) > {d2}低({p2})  ↓ 价格创新低 {abs(p2-p1)/p1*100:.1f}%")
                else:
                    lines.append(f"  价格：{d1}低({p1}) < {d2}低({p2})  ↑ 价格回升 {(p2-p1)/p1*100:.1f}%")
                lines.append(f"  {ind_name}：第一低点 {v1:.4f} → 第二低点 {v2:.4f}  "
                             + ("↑ 指标抬升（背离确立）" if v2 > v1 else "↓ 指标同降（尚未背离）"))
            else:  # bear
                lines.append(f"**K线区间**：{d1} 高点 **{p1}** → {d2} 高点 **{p2}**")
                if p2 > p1:
                    lines.append(f"  价格：{d1}高({p1}) < {d2}高({p2})  ↑ 价格创新高 {(p2-p1)/p1*100:.1f}%")
                else:
                    lines.append(f"  价格：{d1}高({p1}) > {d2}高({p2})  ↓ 价格走低")
                lines.append(f"  {ind_name}：第一高点 {v1:.4f} → 第二高点 {v2:.4f}  "
                             + ("↓ 指标走弱（背离确立）" if v2 < v1 else "↑ 指标同升（尚未背离）"))

            # 进行时/早期信号的动态 reason
            if is_emerging and s.get("reason"):
                lines.append(f"**当前状态**：{s['reason']}")

            # 背离含义解释
            if base_type == "regular_bear":
                lines.append(f"**含义**：常规顶背离 = 价格创新高但动能衰减，是上涨趋势潜在反转的预警信号。")
            elif base_type == "regular_bull":
                lines.append(f"**含义**：常规底背离 = 价格创新低但动能反弹，是下跌趋势潜在反转的预警信号。")
            elif base_type == "hidden_bear":
                lines.append(f"**含义**：隐藏顶背离 = 价格走低但指标抬升，是下跌趋势延续的确认信号。")
            elif base_type == "hidden_bull":
                lines.append(f"**含义**：隐藏底背离 = 价格回升但指标仍弱，是上涨趋势延续的确认信号。")

            # 后市确认条件
            lines.append(f"**后市如何确认**：")
            if base_type == "regular_bear":
                confirm_price = round(p2 * 0.97, 2) if p2 else round(cur_price * 0.97, 2)
                ref_ma = round(min(ma5, ma10) if ma5 and ma10 else cur_price * 0.98, 2)
                lines.append(f"  1. 价格跌破 MA5（当前 {ma5:.2f}）或 MA10（{ma10:.2f}）**收盘确认**，顶背离大概率开始兑现")
                lines.append(f"  2. {ind_name} 出现死叉，且零轴向下运行，趋势转空确认")
                lines.append(f"  3. 成交量若再次放量上冲但价格无法超越 {d2} 高点 {p2}，形态固化")
                lines.append(f"  4. ⚠️ 若后续价格突破 {p2} 并站稳，顶背离信号失效")
            elif base_type == "regular_bull":
                lines.append(f"  1. 价格收复 MA5（当前 {ma5:.2f}）并站稳，底背离开始兑现")
                lines.append(f"  2. {ind_name} 出现金叉，柱线翻红并扩大，动能反转确认")
                lines.append(f"  3. 成交量在价格反弹时放大，价涨量增配合")
                lines.append(f"  4. ⚠️ 若后续价格跌破 {d2} 低点 {p2}，底背离信号失效")
            elif base_type == "hidden_bear":
                lines.append(f"  1. 当前价格若继续走低并跌破 MA20（{ma20:.2f}），延续趋势确认")
                lines.append(f"  2. {ind_name} 持续在零轴下方运行，空头趋势不变")
                lines.append(f"  ⚠️ 若价格强势反弹突破 {p1}，隐藏顶背离失效")
            elif base_type == "hidden_bull":
                lines.append(f"  1. 价格回调不破 {d2} 低点 {p2}，继续上行趋势确认")
                lines.append(f"  2. {ind_name} 金叉并在零轴上方，多头延续")
                lines.append(f"  ⚠️ 若价格跌破 {p2}，隐藏底背离失效")

            # 关键点位
            lines.append(f"**关键点位**：")
            if "bear" in base_type:
                stop_loss  = round(p2 * 1.02, 2) if p2 else round(cur_price * 1.03, 2)
                target1    = round(p2 * 0.95, 2) if p2 else round(cur_price * 0.95, 2)
                target2    = round(p2 * 0.90, 2) if p2 else round(cur_price * 0.90, 2)
                lines.append(f"  - 上方阻力：{p2}（{d2} 高点，即背离右侧顶点）")
                lines.append(f"  - 止损参考：{stop_loss}（若持空，跌破此价止损）")
                lines.append(f"  - 下方目标：初步 {target1}，若有效跌破 MA20 则看 {target2}")
                lines.append(f"  - 支撑参考：MA20={ma20:.2f}，若跌破将加速确认背离")
            else:
                stop_loss = round(p2 * 0.98, 2) if p2 else round(cur_price * 0.97, 2)
                target1   = round(p2 * 1.05, 2) if p2 else round(cur_price * 1.05, 2)
                target2   = round(p2 * 1.10, 2) if p2 else round(cur_price * 1.10, 2)
                lines.append(f"  - 下方支撑：{p2}（{d2} 低点，即背离右侧底点）")
                lines.append(f"  - 止损参考：{stop_loss}（若持多，跌破此价止损）")
                lines.append(f"  - 上方目标：初步 {target1}，若放量突破 MA20={ma20:.2f} 则看 {target2}")
                lines.append(f"  - 压力参考：MA10={ma10:.2f}，MA20={ma20:.2f}")

            # 操作建议
            lines.append(f"**操作建议**：")
            mat = s["maturity"]
            if base_type == "regular_bear":
                if mat == "CONFIRMED":
                    lines.append(f"  - 已确认，建议轻仓者谨慎追多，持仓者逢反弹至压力位分批减仓")
                    lines.append(f"  - 激进者可在价格跌破 MA5 且量能配合时尝试短空，止损设于 {p2}")
                elif mat == "FORMING":
                    lines.append(f"  - 进行中，建议暂缓追涨，等待方向选择确认后再操作")
                else:
                    lines.append(f"  - 迹象浮现（早期预警），建议观望为主，不追高，重点关注量能变化")
            elif base_type == "regular_bull":
                if mat == "CONFIRMED":
                    lines.append(f"  - 已确认，可在价格收复 MA5（{ma5:.2f}）后分批轻仓试多")
                    lines.append(f"  - 严格止损：跌破 {stop_loss} 立即止损，仓位不超过 3 成")
                elif mat == "FORMING":
                    lines.append(f"  - 进行中，耐心等待价格企稳信号（MA5 收口/缩量回调），不抢底")
                else:
                    lines.append(f"  - 迹象浮现（早期预警），观望为主，留意后续能否形成企稳形态")
            elif "bear" in base_type:
                lines.append(f"  - 隐藏顶背离为趋势延续信号，当前为空头市，反弹为卖出机会")
                lines.append(f"  - 建议持仓者坚持持仓，反弹至 {ma10:.2f}~{ma20:.2f} 区间减仓")
            else:
                lines.append(f"  - 隐藏底背离为趋势延续信号，当前为多头市，回调为买入机会")
                lines.append(f"  - 建议在价格回调至 MA5~MA10 区间（{ma5:.2f}~{ma10:.2f}）时分批买入")

            lines.append("")  # 空行分隔

        # 底部统一注释
        note = (
            "---\n"
            "**成熟度说明**：迹象浮现(预警) → 进行中(未确认) → 已确认(可参考)\n"
            "**背离类型**：常规背离=反转信号；隐藏背离=趋势延续信号\n"
            "**重要提示**：背离是预警信号而非充分条件，早期信号尤其须结合均线/成交量二次确认后操作\n"
        )

        # 综合评分：最强信号方向
        best = signals[0]
        base_type = best["type"].replace("_emerging", "")
        if "bull" in base_type:
            score = 50 + int((best["score"] / 100) * 30)
        else:
            score = 50 - int((best["score"] / 100) * 30)

        content = "\n".join(lines) + "\n" + note
        return Section(key="divergence", title="背离检测", content=content,
                       data={"signals": signals[:5], "best": best},
                       score=score, signal=self._score_to_signal(score))

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
