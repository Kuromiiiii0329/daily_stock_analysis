"""
portal/analyzers/fundamental.py
基本面分析器

复用 data_provider/fundamental_adapter.py（财报/分红）
和 data_provider 实时行情（PE/PB/市值）。
子模块：financials / growth / dividend / capital_flow / valuation / business
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path

# path setup handled by server.py startup

from .base import BaseAnalyzer, DimensionResult, Section

logger = logging.getLogger(__name__)


class FundamentalAnalyzer(BaseAnalyzer):
    name = "基本面"
    dimension = "fundamental"
    description = "财报/成长/分红/主力资金/估值/主营业务"

    MODULES = {
        "financials":    "核心财报（营收/净利/ROE/现金流）",
        "growth":        "成长能力（YoY增速）",
        "dividend":      "分红质量（股息率）",
        "capital_flow":  "主力资金流向",
        "valuation":     "估值水平（PE/PB/市值）",
        "business":      "主营业务（LLM + 搜索）",
    }
    DEFAULT_MODULES = ["financials", "growth", "valuation", "capital_flow"]

    def analyze(self, stock_code, stock_name, df, modules, llm_call, search):
        result = DimensionResult(dimension=self.dimension, name=self.name)
        sections = []

        # ── 获取基本面数据 ───────────────────────────────────
        fundamental_bundle = {}
        realtime_data = {}
        try:
            from data_provider.fundamental_adapter import AkshareFundamentalAdapter
            adapter = AkshareFundamentalAdapter()
            fundamental_bundle = adapter.get_fundamental_bundle(stock_code) or {}
        except Exception as e:
            logger.warning("fundamental_adapter error for %s: %s", stock_code, e)

        try:
            from data_provider import DataFetcherManager
            from src.config import get_config
            config = get_config()
            mgr = DataFetcherManager(config)
            realtime_data = mgr.get_realtime_quote(stock_code) or {}
        except Exception as e:
            logger.warning("realtime quote error for %s: %s", stock_code, e)

        # ── 各子模块分析 ─────────────────────────────────────
        try:
            earnings = fundamental_bundle.get("earnings", {})
            fin_report = earnings.get("financial_report", {})
            growth = fundamental_bundle.get("growth", {})

            if "financials" in modules:
                sections.append(self._analyze_financials(fin_report, growth))
            if "growth" in modules:
                sections.append(self._analyze_growth(growth))
            if "dividend" in modules:
                sections.append(self._analyze_dividend(earnings.get("dividend", {})))
            if "capital_flow" in modules:
                sections.append(self._analyze_capital_flow(fundamental_bundle))
            if "valuation" in modules:
                sections.append(self._analyze_valuation(realtime_data))
            if "business" in modules and llm_call:
                sections.append(self._analyze_business(stock_code, stock_name, llm_call, search))

        except Exception as e:
            logger.exception("FundamentalAnalyzer section error for %s: %s", stock_code, e)
            result.error = str(e)

        result.sections = sections
        scored = [s for s in sections if s.score != 50]
        result.score = int(sum(s.score for s in scored) / len(scored)) if scored else 50
        result.signal = self._score_to_signal(result.score)

        fin_sec = next((s for s in sections if s.key == "financials"), None)
        val_sec = next((s for s in sections if s.key == "valuation"), None)
        summary_parts = []
        if fin_sec:
            summary_parts.append(fin_sec.content.split("\n")[0])
        if val_sec:
            summary_parts.append(val_sec.content.split("\n")[0])
        result.summary = "；".join(summary_parts) or f"基本面评分 {result.score}/100"

        return result

    # ── 子模块 ───────────────────────────────────────────────
    def _analyze_financials(self, fin_report: dict, growth: dict) -> Section:
        if not fin_report:
            return Section(key="financials", title="核心财报",
                           content="暂无财报数据", score=50, signal="hold")

        rev     = fin_report.get("revenue")
        profit  = fin_report.get("net_profit_parent")
        ocf     = fin_report.get("operating_cash_flow")
        roe     = fin_report.get("roe") or growth.get("roe")
        period  = fin_report.get("report_date", "")
        margin  = growth.get("gross_margin")

        lines = [f"**报告期：{period}**"]
        score = 50

        if rev:
            lines.append(f"- 营业收入：{self._fmt_money(rev)}")
        if profit:
            lines.append(f"- 归母净利润：{self._fmt_money(profit)}")
            if rev and rev != 0:
                net_margin = profit / rev * 100
                lines.append(f"- 净利率：{net_margin:.1f}%")
        if ocf:
            lines.append(f"- 经营现金流：{self._fmt_money(ocf)}")
            # 现金流质量：经营现金流 > 净利润 为好信号
            if profit and ocf > profit:
                score = min(score + 8, 80)
                lines.append("  ✅ 经营现金流 > 净利润（盈利质量好）")
        if roe:
            lines.append(f"- ROE：{roe:.1f}%")
            if roe > 15:
                score = min(score + 10, 85)
            elif roe < 5:
                score = max(score - 10, 20)
        if margin:
            lines.append(f"- 毛利率：{margin:.1f}%")

        content = "\n".join(lines)
        return Section(key="financials", title="核心财报", content=content,
                       data=fin_report, score=score, signal=self._score_to_signal(score))

    def _analyze_growth(self, growth: dict) -> Section:
        if not growth:
            return Section(key="growth", title="成长能力",
                           content="暂无增长数据", score=50, signal="hold")

        rev_yoy    = growth.get("revenue_yoy")
        profit_yoy = growth.get("net_profit_yoy")

        score = 50
        lines = ["**同比增速（YoY）**"]
        if rev_yoy is not None:
            lines.append(f"- 营收同比：{rev_yoy:+.1f}%")
            if rev_yoy > 20:   score = min(score + 12, 85)
            elif rev_yoy > 10: score = min(score + 6, 75)
            elif rev_yoy < 0:  score = max(score - 10, 25)
        if profit_yoy is not None:
            lines.append(f"- 净利润同比：{profit_yoy:+.1f}%")
            if profit_yoy > 20:   score = min(score + 12, 88)
            elif profit_yoy > 10: score = min(score + 6, 78)
            elif profit_yoy < 0:  score = max(score - 10, 22)

        if score >= 70:
            lines.append("✅ 高成长，业绩驱动力强")
        elif score < 40:
            lines.append("⚠️ 增长承压，需关注基本面改善信号")

        content = "\n".join(lines)
        return Section(key="growth", title="成长能力", content=content,
                       data=growth, score=score, signal=self._score_to_signal(score))

    def _analyze_dividend(self, dividend: dict) -> Section:
        if not dividend:
            return Section(key="dividend", title="分红质量",
                           content="暂无分红数据", score=50, signal="hold")

        ttm_yield  = dividend.get("ttm_dividend_yield_pct")
        ttm_dps    = dividend.get("ttm_cash_dividend_per_share")
        ttm_count  = dividend.get("ttm_event_count", 0)

        score = 50
        lines = ["**近12个月分红情况**"]
        if ttm_dps:
            lines.append(f"- TTM每股现金分红：{ttm_dps:.4f} 元")
        if ttm_yield:
            lines.append(f"- TTM股息率：{ttm_yield:.2f}%")
            if ttm_yield > 4:   score = 72
            elif ttm_yield > 2: score = 60
            elif ttm_yield > 0: score = 52
        lines.append(f"- 近12月分红次数：{ttm_count}")
        if ttm_count == 0:
            lines.append("  ⚠️ 近期无分红")
            score = max(score - 5, 45)

        content = "\n".join(lines)
        return Section(key="dividend", title="分红质量", content=content,
                       data=dividend, score=score, signal=self._score_to_signal(score))

    def _analyze_capital_flow(self, bundle: dict) -> Section:
        cf = bundle.get("capital_flow", {})
        stock_flow = cf.get("stock_flow", {})
        if not stock_flow:
            return Section(key="capital_flow", title="主力资金",
                           content="暂无资金流向数据", score=50, signal="hold")

        main_in  = stock_flow.get("main_net_inflow", 0) or 0
        in_5d    = stock_flow.get("inflow_5d", 0) or 0
        in_10d   = stock_flow.get("inflow_10d", 0) or 0

        score = 50
        if main_in > 0:   score = min(score + 10, 72)
        elif main_in < 0: score = max(score - 10, 30)
        if in_5d > 0 and in_10d > 0:
            score = min(score + 8, 80)

        main_str = f"{main_in/1e8:+.2f}亿" if abs(main_in) > 1e6 else f"{main_in/1e4:+.2f}万"
        lines = [
            f"**主力净流入（今日）：{main_str}**",
            f"- 5日净流入：{in_5d/1e8:+.2f}亿" if abs(in_5d) > 1e6 else f"- 5日净流入：{in_5d/1e4:+.2f}万",
            f"- 10日净流入：{in_10d/1e8:+.2f}亿" if abs(in_10d) > 1e6 else f"- 10日净流入：{in_10d/1e4:+.2f}万",
        ]
        if main_in > 0:
            lines.append("✅ 主力持续流入，多头力量占优")
        else:
            lines.append("⚠️ 主力净流出，注意风险")

        # 板块资金
        rankings = cf.get("sector_rankings", {})
        top = rankings.get("top", [])[:3]
        if top:
            lines.append(f"- 资金流入板块前三：{', '.join(t.get('name','') for t in top)}")

        content = "\n".join(lines)
        return Section(key="capital_flow", title="主力资金", content=content,
                       data=stock_flow, score=score, signal=self._score_to_signal(score))

    def _analyze_valuation(self, realtime: dict) -> Section:
        if not realtime:
            return Section(key="valuation", title="估值水平",
                           content="暂无实时行情数据", score=50, signal="hold")

        pe  = realtime.get("pe_ratio") or realtime.get("pe")
        pb  = realtime.get("pb_ratio") or realtime.get("pb")
        mv  = realtime.get("total_mv") or realtime.get("market_cap")
        circ = realtime.get("circ_mv")

        score = 50
        lines = ["**估值指标**"]
        if pe:
            lines.append(f"- 市盈率（PE）：{pe:.1f}x")
            if pe < 15:    score = min(score + 10, 75)
            elif pe > 50:  score = max(score - 10, 30)
            elif pe < 0:   lines.append("  ⚠️ PE为负（亏损）")
        if pb:
            lines.append(f"- 市净率（PB）：{pb:.2f}x")
            if pb < 1:    lines.append("  ✅ PB<1，资产折价")
        if mv:
            mv_str = f"{mv/1e8:.0f}亿" if mv > 1e8 else f"{mv/1e4:.0f}万"
            lines.append(f"- 总市值：{mv_str}")
        if circ:
            circ_str = f"{circ/1e8:.0f}亿" if circ > 1e8 else f"{circ/1e4:.0f}万"
            lines.append(f"- 流通市值：{circ_str}")

        content = "\n".join(lines)
        return Section(key="valuation", title="估值水平", content=content,
                       data={"pe": pe, "pb": pb, "mv": mv},
                       score=score, signal=self._score_to_signal(score))

    def _analyze_business(self, stock_code, stock_name, llm_call, search) -> Section:
        search_results = ""
        if search:
            try:
                results = search(f"{stock_name} 主营业务 行业地位 竞争优势")
                if results:
                    snippets = [r.get("snippet", r.get("content", ""))[:200] for r in results[:3]]
                    search_results = "\n".join(f"- {s}" for s in snippets if s)
            except Exception as e:
                logger.warning("business search error: %s", e)

        prompt = f"""请简要分析 {stock_name}（{stock_code}）的主营业务情况：
{f"参考资讯：" + chr(10) + search_results if search_results else ""}

请输出（不超过150字）：
1. 核心主营业务（1-2句）
2. 行业地位（龙头/二线/细分领域）
3. 主要竞争优势或风险
4. 近期经营亮点或隐忧"""
        try:
            content = llm_call(prompt).strip()
        except Exception as e:
            content = f"主营业务分析失败：{e}"
        return Section(key="business", title="主营业务", content=content, score=50, signal="hold")

    @staticmethod
    def _fmt_money(val) -> str:
        if val is None: return "N/A"
        if abs(val) >= 1e8:  return f"{val/1e8:.2f}亿"
        if abs(val) >= 1e4:  return f"{val/1e4:.2f}万"
        return f"{val:.2f}"

    @staticmethod
    def _score_to_signal(score: int) -> str:
        if score >= 70: return "buy"
        if score >= 55: return "watch"
        if score >= 40: return "hold"
        return "sell"
