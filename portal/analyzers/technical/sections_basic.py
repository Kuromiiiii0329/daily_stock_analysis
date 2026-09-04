"""
portal/analyzers/technical/sections_basic.py
纯量化 section 子模块 —— 均线/MACD/RSI/KDJ/布林带/量价/超买超卖。

特征：只读 compute_indicators 产出的指标列，无外部 IO、无 LLM，score/signal 为占位
（50/hold），真实打分由外层 llm_notes 回写。

从原 TechnicalAnalyzer 的对应 _analyze_* 方法逐字节搬迁，改为模块级纯函数。
"""
from __future__ import annotations

import pandas as pd

from ..base import Section


def analyze_ma(df, stock_code) -> Section:
    last = df.iloc[-1]
    close = last["close"]
    ma5   = last.get("ma5")
    ma10  = last.get("ma10")
    ma20  = last.get("ma20")
    ma30  = last.get("ma30")
    ma60  = last.get("ma60")
    ma120 = last.get("ma120")
    ma250 = last.get("ma250")

    def _v(x): return x if (x is not None and not pd.isna(x)) else None

    ma5, ma10, ma20, ma30, ma60, ma120, ma250 = (
        _v(ma5), _v(ma10), _v(ma20), _v(ma30), _v(ma60), _v(ma120), _v(ma250)
    )

    bias5  = (close - ma5)  / ma5  * 100 if ma5  else 0
    bias20 = (close - ma20) / ma20 * 100 if ma20 else 0
    bias30 = (close - ma30) / ma30 * 100 if ma30 else 0

    # 均线多空排列（短中期）— 仅客观描述形态，不打分（打分交给 LLM）
    alignment = ""
    if all(v is not None for v in [ma5, ma10, ma20]):
        if ma5 > ma10 > ma20:
            alignment = "多头排列（MA5>MA10>MA20）"
        elif ma5 < ma10 < ma20:
            alignment = "空头排列（MA5<MA10<MA20）"
        else:
            alignment = "均线缠绕（震荡）"

    # 中长期均线多空排列（30/60/120）
    mid_alignment = ""
    if all(v is not None for v in [ma30, ma60, ma120]):
        if ma30 > ma60 > ma120:
            mid_alignment = "中长期多头（MA30>MA60>MA120）"
        elif ma30 < ma60 < ma120:
            mid_alignment = "中长期空头（MA30<MA60<MA120）"

    # 价格与年线/120日线/60日线的位置（客观事实）
    above_ma250 = ma250 and close > ma250
    above_ma120 = ma120 and close > ma120
    above_ma60  = ma60  and close > ma60

    if abs(bias5) > 8:
        bias_warn = f"  ⚠️ 短期乖离率偏大（MA5乖离 {bias5:+.1f}%）\n"
    else:
        bias_warn = ""

    # 关键均线支撑/压力描述
    pos_lines = []
    if ma30:
        rel30 = "上方（支撑）" if close > ma30 else "下方（压力）"
        pos_lines.append(f"MA30={ma30:.2f}（月线，{rel30}）")
    if ma60:
        rel60 = "上方（支撑）" if close > ma60 else "下方（压力）"
        pos_lines.append(f"MA60={ma60:.2f}（季线，{rel60}）")
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
    if ma30:  ma_line += f"  MA30={ma30:.2f}"
    if ma60:  ma_line += f"  MA60={ma60:.2f}"
    if ma120: ma_line += f"  MA120={ma120:.2f}"
    if ma250: ma_line += f"  MA250={ma250:.2f}"

    content = (
        f"**{alignment}**"
        + (f"  {mid_alignment}" if mid_alignment else "") + "\n"
        f"- 当前价: {close:.2f}\n"
        f"{ma_line}\n"
        f"- 乖离率 MA5:{bias5:+.1f}%  MA20:{bias20:+.1f}%"
        + (f"  MA30:{bias30:+.1f}%" if ma30 else "") + "\n"
        + bias_warn
        + ("\n".join(f"- {l}" for l in pos_lines) + "\n" if pos_lines else "")
    )
    signal = "hold"
    return Section(key="ma_system", title="均线系统", content=content,
                   data={"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma30": ma30,
                         "ma60": ma60, "ma120": ma120, "ma250": ma250,
                         "close": close, "bias5": bias5, "bias20": bias20, "bias30": bias30,
                         "above_ma250": above_ma250, "above_ma120": above_ma120,
                         "above_ma60": above_ma60},
                   score=50, signal=signal)


def analyze_macd(df) -> Section:
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    dif, dea, bar = last.get("dif", 0), last.get("dea", 0), last.get("macd_bar", 0)
    prev_bar = prev.get("macd_bar", 0)

    # 金叉/死叉检测（客观事件，不打分）
    golden = prev.get("dif", 0) < prev.get("dea", 0) and dif > dea
    death  = prev.get("dif", 0) > prev.get("dea", 0) and dif < dea

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
                   score=50, signal="hold")


def analyze_rsi(df) -> Section:
    last = df.iloc[-1]
    r6, r12, r24 = (last.get(f"rsi{n}", 50) for n in [6, 12, 24])

    # 客观区间描述（不打分，打分交给 LLM）
    if r6 > 80:
        status = "严重超买区间（>80）"
    elif r6 > 70:
        status = "超买区间（>70）"
    elif r6 < 20:
        status = "严重超卖区间（<20）"
    elif r6 < 30:
        status = "超卖区间（<30）"
    else:
        status = "中性区间"

    content = (
        f"**RSI(6)={r6:.1f}  RSI(12)={r12:.1f}  RSI(24)={r24:.1f}**\n"
        f"- 区间：{status}\n"
        f"- 参考：超买>70，超卖<30\n"
    )
    return Section(key="rsi", title="RSI超买超卖", content=content,
                   data={"rsi6": r6, "rsi12": r12, "rsi24": r24},
                   score=50, signal="hold")


def analyze_kdj(df) -> Section:
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    k, d, j = last.get("kdj_k", 50), last.get("kdj_d", 50), last.get("kdj_j", 50)
    pk, pd_ = prev.get("kdj_k", 50), prev.get("kdj_d", 50)

    golden = pk < pd_ and k > d
    death  = pk > pd_ and k < d

    cross = "🟢 金叉" if golden else ("🔴 死叉" if death else "")
    overbought = " ⚠️ J值超买(>90)" if j > 90 else (" ✅ J值超卖(<10)" if j < 10 else "")

    content = (
        f"**K={k:.1f}  D={d:.1f}  J={j:.1f}** {cross}{overbought}\n"
        f"- K>D（多头信号）{' ✓' if k > d else ' ✗'}\n"
    )
    return Section(key="kdj", title="KDJ随机指标", content=content,
                   data={"k": k, "d": d, "j": j, "golden": golden, "death": death},
                   score=50, signal="hold")


def analyze_bollinger(df) -> Section:
    last = df.iloc[-1]
    close = last["close"]
    upper = last.get("boll_upper")
    mid   = last.get("boll_mid")
    lower = last.get("boll_lower")
    width = last.get("boll_width", 0)

    if upper is None or pd.isna(upper):
        return None  # 无真实布林带数据，不显示（不出假情报）

    pos_pct = (close - lower) / (upper - lower + 1e-9) * 100

    # 客观位置描述（不打分，打分交给 LLM）
    if close > upper:
        status = "价格突破上轨"
    elif close < lower:
        status = "价格跌破下轨"
    elif pos_pct > 75:
        status = "价格在布林带上半区"
    elif pos_pct < 25:
        status = "价格在布林带下半区"
    else:
        status = "价格在布林带中部"

    content = (
        f"**上轨={upper:.2f}  中轨={mid:.2f}  下轨={lower:.2f}**\n"
        f"- 带宽：{width:.1f}%  价格位置：{pos_pct:.0f}%\n"
        f"- 状态：{status}\n"
    )
    return Section(key="bollinger", title="布林带", content=content,
                   data={"upper": upper, "mid": mid, "lower": lower,
                         "width": width, "pos_pct": pos_pct},
                   score=50, signal="hold")


def analyze_volume(df) -> Section:
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    vol_ratio = last.get("vol_ratio", 1.0)
    close_change = (last["close"] - prev["close"]) / prev["close"] * 100

    # 客观量价关系描述（不打分，打分交给 LLM）
    if vol_ratio > 2 and close_change > 0:
        status = "放量上涨"
    elif vol_ratio > 2 and close_change < 0:
        status = "放量下跌"
    elif vol_ratio < 0.5 and close_change > 0:
        status = "缩量上涨"
    elif vol_ratio < 0.5 and close_change < 0:
        status = "缩量回调"
    else:
        status = "量能正常"

    content = (
        f"**量比={vol_ratio:.2f}x  当日涨跌={close_change:+.2f}%**\n"
        f"- 状态：{status}\n"
        f"- 量比>2为放量，<0.5为缩量\n"
    )
    return Section(key="volume", title="量价关系", content=content,
                   data={"vol_ratio": vol_ratio, "close_change": close_change},
                   score=50, signal="hold")


def analyze_overbought(df) -> Section:
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

    # 多指标投票汇总（客观事实，不打分——打分交给 LLM）
    if ob_votes >= 3:
        status = f"🔴 强烈超买（{ob_votes}/4 指标）"
    elif os_votes >= 3:
        status = f"🟢 强烈超卖（{os_votes}/4 指标）"
    elif ob_votes == 2:
        status = f"🟠 偏超买（{ob_votes}/4）"
    elif os_votes == 2:
        status = f"🔵 偏超卖（{os_votes}/4）"
    else:
        status = "⚪ 中性区域，无明显超买超卖"

    content = "**" + status + "**\n" + "\n".join(f"- {x}" for x in lines) + \
              "\n- 判据：多指标共振时信号更可靠（≥3 项同向为强信号）\n"
    return Section(key="overbought", title="超买超卖综合", content=content,
                   data={"rsi6": r6, "kdj_j": j, "wr14": wr, "boll_pos": boll_pos,
                         "ob_votes": ob_votes, "os_votes": os_votes},
                   score=50, signal="hold")
