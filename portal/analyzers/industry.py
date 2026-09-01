"""
portal/analyzers/industry.py
产业链分析器（核心新增）

功能：
1. LLM 自动识别该股票的核心产业链关键词（如天齐锂业 → 碳酸锂价格）
2. 搜索关键词最新资讯
3. LLM 综合分析：产业链地位/上下游/大宗商品/竞争格局/政策风向

子模块：chain_keywords / key_commodity / industry_chain / competitors / policy
"""
from __future__ import annotations
import json
import logging
import sys
from pathlib import Path

# path setup handled by server.py startup

from .base import BaseAnalyzer, DimensionResult, Section

logger = logging.getLogger(__name__)

# ── 大宗商品期货代码映射（akshare futures_main_sina 使用）────────
COMMODITY_FUTURES_MAP = {
    "碳酸锂": "LC",   # 碳酸锂期货
    "锂": "LC",
    "铜": "CU",
    "铝": "AL",
    "钢": "RB",     # 螺纹钢
    "煤": "ZC",     # 郑煤
    "原油": "SC",
    "石油": "SC",
    "黄金": "AU",
    "白银": "AG",
}

# ── 内置产业链关键词模板（LLM 生成失败时的降级方案）────────────
INDUSTRY_KEYWORDS_FALLBACK = {
    "锂": ["碳酸锂价格", "锂矿供需", "新能源电池产业链"],
    "钴": ["钴价格走势", "钴矿供需"],
    "铜": ["铜价格走势", "铜矿供需", "铜期货"],
    "铝": ["铝价格走势", "电解铝产能"],
    "钢": ["钢铁价格", "铁矿石价格", "螺纹钢期货"],
    "煤": ["动力煤价格", "焦煤价格", "能源供给"],
    "石油": ["原油价格", "炼化利润", "成品油价格"],
    "化工": ["化工原料价格", "石油化工链"],
    "白酒": ["白酒行业景气", "茅台价格", "高端消费"],
    "猪": ["猪肉价格", "猪周期", "饲料价格"],
    "光伏": ["硅料价格", "光伏装机量", "组件价格"],
    "半导体": ["芯片供需", "半导体景气", "晶圆代工"],
    "新能源": ["电池材料价格", "碳酸锂", "新能源汽车销量"],
    "医药": ["医保政策", "集采价格", "创新药研发"],
    "房地产": ["土地市场", "房价走势", "地产政策"],
    "银行": ["利率政策", "不良贷款率", "息差"],
}


class IndustryAnalyzer(BaseAnalyzer):
    name = "产业链"
    dimension = "industry"
    description = "产业链地位/大宗商品/竞争格局/政策风向（LLM + 搜索）"

    MODULES = {
        # ── 板块子模块（真实数据，efinance/同花顺，不依赖 LLM）──
        "sector_membership": "所属板块（真实数据）",
        "sector_momentum":   "板块景气/相对强弱",
        "sector_fund_flow":  "板块资金/轮动",
        # ── 产业链子模块（LLM + 搜索）──
        "chain_keywords": "识别产业链关键词",
        "key_commodity":  "核心大宗/产品价格走势",
        "industry_chain": "产业链地位（上中下游）",
        "competitors":    "竞争格局",
        "policy":         "政策风向",
    }
    DEFAULT_MODULES = ["sector_membership", "sector_momentum", "sector_fund_flow",
                       "key_commodity", "industry_chain", "competitors", "policy"]

    def analyze(self, stock_code, stock_name, df, modules, llm_call, search):
        result = DimensionResult(dimension=self.dimension, name=self.name)
        sections = []

        # ── 板块子模块（真实数据，不依赖 LLM，优先执行）────────
        sector_keys = [k for k in modules if k in ("sector_membership", "sector_momentum", "sector_fund_flow")]
        if sector_keys:
            sections.extend(self._analyze_sectors(stock_code, stock_name, df, sector_keys))

        # ── 产业链 LLM 子模块（需要 API Key）──────────────────
        llm_keys = [k for k in modules if k not in ("sector_membership", "sector_momentum", "sector_fund_flow")]
        if not llm_call:
            if sections:
                # 有板块数据 → 仅缺 LLM 部分，整体仍可用
                result.sections = sections
                self._finalize_score(result, sections)
                result.summary = self._sector_summary(sections) or f"产业链评分 {result.score}/100（LLM 未配置，仅板块数据）"
                return result
            result.error = "产业链分析需要 LLM（未配置 API Key）"
            result.score = 50
            return result

        # Step 1：识别产业链关键词（优先读 meta 缓存里已有的关键词）
        keywords = self._get_chain_keywords(stock_code, stock_name, llm_call)
        if "chain_keywords" in modules:
            kw_content = "**自动识别的产业链关键词：**\n" + "\n".join(f"- {k}" for k in keywords)
            sections.append(Section(key="chain_keywords", title="产业链关键词",
                                    content=kw_content, data={"keywords": keywords},
                                    score=50, signal="hold"))

        # Step 2：搜索各关键词最新资讯（带本地缓存）
        search_context = {}
        try:
            from portal.data_cache import StockDataCache
            cache = StockDataCache()
        except Exception:
            cache = None

        if keywords:
            for kw in keywords[:4]:
                # 先查本地缓存
                if cache:
                    cached = cache.get_commodity(stock_code, kw)
                    if cached is not None:
                        search_context[kw] = cached
                        logger.info("Industry cache hit '%s': %d snippets", kw, len(cached))
                        continue

                # 缓存未命中 → 网络搜索
                if search:
                    try:
                        results = search(kw)
                        snippets = [r.get("snippet", r.get("content", ""))[:300]
                                    for r in (results or [])[:3] if r]
                        search_context[kw] = snippets
                        logger.info("Industry search '%s': %d results", kw, len(snippets))
                        # 写入本地缓存
                        if cache and snippets:
                            cache.save_commodity(stock_code, kw, snippets)
                    except Exception as e:
                        logger.warning("Search error for '%s': %s", kw, e)

        # Step 3：各子模块 LLM 分析
        if "key_commodity" in modules:
            sections.append(self._analyze_commodity(
                stock_name, keywords, search_context, llm_call))

        if "industry_chain" in modules:
            sections.append(self._analyze_chain_position(
                stock_code, stock_name, keywords, search_context, llm_call))

        if "competitors" in modules:
            sections.append(self._analyze_competitors(
                stock_code, stock_name, search_context, llm_call))

        if "policy" in modules:
            sections.append(self._analyze_policy(
                stock_name, keywords, search_context, llm_call))

        result.sections = sections
        self._finalize_score(result, sections)

        # 摘要：优先板块相对强弱，其次大宗/产业链
        sector_sum = self._sector_summary(sections)
        commodity_sec = next((s for s in sections if s.key == "key_commodity"), None)
        chain_sec     = next((s for s in sections if s.key == "industry_chain"), None)
        if sector_sum:
            result.summary = sector_sum
        elif commodity_sec:
            result.summary = commodity_sec.content.split("\n")[0]
        elif chain_sec:
            result.summary = chain_sec.content.split("\n")[0]
        else:
            result.summary = f"产业链评分 {result.score}/100"

        return result

    # ── 统一评分 + 板块摘要 ───────────────────────────────────
    @staticmethod
    def _finalize_score(result, sections):
        scored = [s for s in sections if s.score != 50]
        result.score = int(sum(s.score for s in scored) / len(scored)) if scored else 50
        result.signal = IndustryAnalyzer._score_to_signal(result.score)

    @staticmethod
    def _sector_summary(sections) -> str:
        """从板块子模块提取一句话摘要（相对强弱优先）。"""
        mom = next((s for s in sections if s.key == "sector_momentum"), None)
        if mom and mom.content:
            return mom.content.split("\n")[0]
        mem = next((s for s in sections if s.key == "sector_membership"), None)
        if mem and mem.data.get("primary"):
            names = "、".join(mem.data["primary"][:3])
            return f"所属核心题材板块：{names}"
        return ""

    # ── 板块子模块（真实数据）────────────────────────────────
    def _analyze_sectors(self, stock_code, stock_name, df, sector_keys) -> list:
        """
        基于真实板块数据（efinance/同花顺）生成板块 Section。
        核心接口 get_belong_board 稳定；板块K线/全市场快照为可选增强（带超时保护）。
        """
        sections = []
        try:
            from portal.analyzers.sector import SectorData
            sd = SectorData()
        except Exception as e:
            logger.warning("SectorData 加载失败: %s", e)
            sections.append(Section(key="sector_membership", title="所属板块",
                                    content="板块数据模块加载失败，跳过板块分析。",
                                    score=50, signal="hold"))
            return sections

        # Step 1：个股所属板块（稳定核心接口）
        boards = sd.get_stock_boards(stock_code)
        if not boards:
            sections.append(Section(key="sector_membership", title="所属板块",
                                    content="⚠️ 未获取到板块归属数据（数据源不可用），板块分析降级跳过。",
                                    score=50, signal="hold"))
            return sections

        primary = sd.pick_primary_boards(boards, top=6)
        primary_names = [b["name"] for b in primary]

        # 个股当日涨幅（从 K 线 df 末行取，用于 alpha）
        stock_pct = self._stock_today_pct(df)

        # ── 子模块 1：所属板块 ────────────────────────────────
        if "sector_membership" in sector_keys:
            lines = ["**核心题材板块**（按相关性）："]
            for b in primary:
                pct = b.get("pct")
                tag = f"（当日 {pct:+.2f}%）" if isinstance(pct, (int, float)) else ""
                lines.append(f"- {b['name']} {tag}")
            other_names = [b["name"] for b in boards if b["name"] not in primary_names][:8]
            if other_names:
                lines.append("\n**其他归属**：" + "、".join(other_names))
            sections.append(Section(
                key="sector_membership", title="所属板块（真实数据）",
                content="\n".join(lines),
                data={"primary": primary_names, "all": [b["name"] for b in boards]},
                score=50, signal="hold"))

        # ── 子模块 2：板块景气 / 个股相对强弱 ─────────────────
        if "sector_momentum" in sector_keys:
            sections.append(self._sector_momentum_section(sd, primary, stock_pct, stock_name))

        # ── 子模块 3：板块资金 / 轮动 ─────────────────────────
        if "sector_fund_flow" in sector_keys:
            sections.append(self._sector_fund_section(sd, primary))

        return sections

    @staticmethod
    def _stock_today_pct(df):
        """从 K 线 DataFrame 末行取当日涨跌幅（%）。"""
        if df is None or (hasattr(df, "empty") and df.empty):
            return None
        try:
            cols = {c.lower(): c for c in df.columns}
            if "pct_chg" in cols:
                v = df[cols["pct_chg"]].iloc[-1]
                return round(float(v), 2)
            if "close" in cols and len(df) >= 2:
                c = df[cols["close"]].astype(float)
                return round((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2)
        except Exception:
            pass
        return None

    def _sector_momentum_section(self, sd, primary, stock_pct, stock_name) -> Section:
        """板块景气 + 个股 alpha（个股涨幅 − 板块涨幅）。"""
        if not primary:
            return Section(key="sector_momentum", title="板块景气/相对强弱",
                           content="无核心题材板块，跳过景气分析。", score=50, signal="hold")

        # 用板块当日涨幅（get_belong_board 已含，稳定）算个股 alpha
        board_pcts = [b["pct"] for b in primary if isinstance(b.get("pct"), (int, float))]
        avg_board_pct = round(sum(board_pcts) / len(board_pcts), 2) if board_pcts else None

        lines = []
        score, signal = 50, "hold"

        if stock_pct is not None and avg_board_pct is not None:
            alpha = round(stock_pct - avg_board_pct, 2)
            strength = "跑赢板块 💪" if alpha > 0.5 else ("跑输板块 📉" if alpha < -0.5 else "与板块同步")
            lines.append(f"个股当日 {stock_pct:+.2f}% vs 核心板块均值 {avg_board_pct:+.2f}%，"
                         f"**超额 α = {alpha:+.2f}%（{strength}）**")
            # alpha 映射评分：跑赢偏强
            if alpha > 1.0:   score, signal = 66, "watch"
            elif alpha > 0.3: score, signal = 58, "watch"
            elif alpha < -1.0: score, signal = 38, "hold"
            elif alpha < -0.3: score, signal = 46, "hold"
        elif stock_pct is not None:
            lines.append(f"个股当日涨幅 {stock_pct:+.2f}%（板块涨幅数据缺失，无法算超额）")
        else:
            lines.append("个股/板块涨幅数据不足，暂无法计算相对强弱。")

        # 板块景气：正涨幅板块占比
        if board_pcts:
            up = sum(1 for p in board_pcts if p > 0)
            lines.append(f"核心题材中 {up}/{len(board_pcts)} 个板块当日上涨"
                         f"（{'题材整体活跃' if up > len(board_pcts)/2 else '题材偏弱'}）")

        # 可选：拉主板块 K 线算近 20 日走势（带超时保护，拿不到就跳过）
        try:
            b0 = primary[0]
            kl = sd.get_sector_kline(b0["bk"], b0["name"], days=60)
            if kl is not None and not kl.empty and "close" in kl.columns and len(kl) >= 20:
                c = kl["close"].astype(float)
                ret20 = round((c.iloc[-1] / c.iloc[-20] - 1) * 100, 1)
                lines.append(f"主板块「{b0['name']}」近 20 交易日 {ret20:+.1f}%"
                             f"（{'上升趋势' if ret20 > 0 else '下行趋势'}）")
        except Exception:
            pass

        return Section(key="sector_momentum", title="板块景气/相对强弱",
                       content="\n".join(lines),
                       data={"stock_pct": stock_pct, "avg_board_pct": avg_board_pct},
                       score=score, signal=signal)

    def _sector_fund_section(self, sd, primary) -> Section:
        """板块资金/轮动：用全市场快照给主板块在全市场的涨幅排名。"""
        snap = sd.get_market_snapshot()
        if snap is None or (hasattr(snap, "empty") and snap.empty):
            # 快照不可用 → 用 get_belong_board 的板块涨幅做简单强弱判断
            board_pcts = [(b["name"], b["pct"]) for b in primary if isinstance(b.get("pct"), (int, float))]
            if board_pcts:
                board_pcts.sort(key=lambda x: x[1], reverse=True)
                top = board_pcts[0]
                content = (f"（全市场快照不可用，基于板块当日涨幅）\n"
                           f"核心题材中最强：**{top[0]} {top[1]:+.2f}%**")
                return Section(key="sector_fund_flow", title="板块资金/轮动",
                               content=content, score=50, signal="hold")
            return Section(key="sector_fund_flow", title="板块资金/轮动",
                           content="板块行情快照与涨幅数据均不可用，跳过。", score=50, signal="hold")

        try:
            total = len(snap)
            names = set(b["name"] for b in primary)
            hit = snap[snap["name"].isin(names)] if "name" in snap.columns else snap.iloc[0:0]
            lines = [f"全市场共 {total} 个概念板块。核心题材板块表现："]
            score, signal = 50, "hold"
            if not hit.empty and "pct_chg" in hit.columns:
                ranked = snap.sort_values("pct_chg", ascending=False).reset_index(drop=True) \
                    if "pct_chg" in snap.columns else snap
                for _, r in hit.iterrows():
                    nm = r["name"]; pc = r.get("pct_chg")
                    rank = ranked.index[ranked["name"] == nm].tolist()
                    rank_txt = f"全市场第 {rank[0]+1}/{total}" if rank else ""
                    tr = r.get("turnover"); vr = r.get("volume_ratio")
                    extra = []
                    if isinstance(tr, (int, float)): extra.append(f"换手{tr:.1f}%")
                    if isinstance(vr, (int, float)): extra.append(f"量比{vr:.2f}")
                    lines.append(f"- {nm} {pc:+.2f}% {rank_txt} {' '.join(extra)}")
                # 主板块排名靠前 → 偏强
                best_rank = min((ranked.index[ranked["name"] == n].tolist() or [total])[0] for n in names)
                if best_rank < total * 0.1:   score, signal = 64, "watch"
                elif best_rank < total * 0.3: score, signal = 56, "watch"
                elif best_rank > total * 0.7: score, signal = 42, "hold"
            else:
                lines.append("（核心题材未在快照中匹配到）")
            return Section(key="sector_fund_flow", title="板块资金/轮动",
                           content="\n".join(lines), score=score, signal=signal)
        except Exception as e:
            logger.warning("sector_fund_section error: %s", e)
            return Section(key="sector_fund_flow", title="板块资金/轮动",
                           content="板块资金分析异常，跳过。", score=50, signal="hold")

    # ── 核心：自动识别产业链关键词 ───────────────────────────
    def _get_chain_keywords(self, stock_code: str, stock_name: str, llm_call) -> list[str]:
        prompt = f"""你是A股产业链分析专家。
请为 {stock_name}（{stock_code}）列出5-8个最重要的产业链搜索关键词，
用于搜索该公司相关的大宗商品价格、上下游行业动态、产品供需等信息。

要求：
- 每个关键词要具体、可搜索（如"碳酸锂价格走势"而非"锂"）
- 优先列出对公司盈利影响最直接的关键词
- 涵盖：核心产品/原材料价格、行业景气度、上下游需求

请直接返回 JSON 数组格式，例如：["碳酸锂价格", "锂矿供需", "新能源电池装机量"]
只返回JSON，不要其他文字。"""
        try:
            resp = llm_call(prompt).strip()
            # 提取 JSON 数组
            start = resp.find("[")
            end   = resp.rfind("]") + 1
            if start >= 0 and end > start:
                keywords = json.loads(resp[start:end])
                if isinstance(keywords, list):
                    return [str(k) for k in keywords[:8]]
        except Exception as e:
            logger.warning("Keywords LLM error for %s: %s", stock_name, e)

        # 降级：根据股票名称关键词匹配
        for key, kws in INDUSTRY_KEYWORDS_FALLBACK.items():
            if key in stock_name:
                return kws
        return [f"{stock_name}行业景气", f"{stock_name}竞争格局", f"{stock_name}政策动向"]

    # ── 各子模块 ─────────────────────────────────────────────
    def _get_commodity_price(self, keyword: str) -> str:
        """根据关键词查期货主力合约最新价，返回格式化字符串，失败返回空字符串。"""
        code = None
        for key, futures_code in COMMODITY_FUTURES_MAP.items():
            if key in keyword:
                code = futures_code
                break
        if not code:
            return ""
        try:
            import akshare as ak
            df = ak.futures_main_sina(symbol=code)
            if df is None or df.empty:
                return ""
            # 取最新一行
            latest = df.iloc[-1]
            price = float(latest.get("收盘价", latest.get("close", 0)))
            date  = str(latest.get("日期", latest.get("date", "")))[:10]
            if price <= 0:
                return ""
            return f"{keyword}：{price:.0f} 元/吨（{date}）"
        except Exception as e:
            logger.debug("_get_commodity_price %s(%s) error: %s", keyword, code, e)
            return ""

    def _analyze_commodity(self, stock_name, keywords, search_context, llm_call) -> Section:
        price_lines = [self._get_commodity_price(kw) for kw in keywords[:3]]
        price_text  = "\n".join(l for l in price_lines if l)
        context_text = self._build_context(search_context, keywords[:3])
        prompt = f"""你是商品市场分析师。
请基于以下信息，分析与 {stock_name} 最相关的大宗商品/核心产品价格走势：
{"【实时期货价格】\n" + price_text + "\n\n" if price_text else ""}
{context_text if context_text else f"无实时搜索数据，请基于你的知识分析 {stock_name} 相关产品价格走势。"}

请输出（不超过200字）：
1. 核心商品/产品当前价格趋势（上涨/下跌/震荡）
2. 价格走势对公司利润的直接影响（利多/利空/中性）
3. 未来3-6个月预判

输出最后一行格式：【信号】利多/利空/中性"""
        content, score, signal = self._llm_with_signal(llm_call, prompt)
        return Section(key="key_commodity", title="核心大宗/产品价格",
                       content=content, score=score, signal=signal)

    def _analyze_chain_position(self, stock_code, stock_name, keywords, search_context, llm_call) -> Section:
        context_text = self._build_context(search_context, keywords)
        prompt = f"""你是产业链分析专家。
请分析 {stock_name}（{stock_code}）在产业链中的位置和竞争优势：

{context_text if context_text else "请基于你的知识进行分析。"}

请输出（不超过200字）：
1. 公司在产业链的位置（上游/中游/下游）
2. 对上下游的议价能力
3. 产业链中的核心竞争优势或薄弱环节
4. 当前产业链景气度

输出最后一行格式：【信号】利多/利空/中性"""
        content, score, signal = self._llm_with_signal(llm_call, prompt)
        return Section(key="industry_chain", title="产业链地位",
                       content=content, score=score, signal=signal)

    def _analyze_competitors(self, stock_code, stock_name, search_context, llm_call) -> Section:
        context_text = self._build_context(search_context, list(search_context.keys())[:2])
        prompt = f"""你是行业竞争分析专家。
请分析 {stock_name}（{stock_code}）的竞争格局：

{context_text if context_text else "请基于你的知识进行分析。"}

请输出（不超过150字）：
1. 主要竞争对手（2-3家）
2. 公司市场份额估计
3. 相对竞争优势/劣势
4. 行业集中度趋势（集中/分散/整合中）

输出最后一行格式：【信号】利多/利空/中性"""
        content, score, signal = self._llm_with_signal(llm_call, prompt)
        return Section(key="competitors", title="竞争格局",
                       content=content, score=score, signal=signal)

    def _analyze_policy(self, stock_name, keywords, search_context, llm_call) -> Section:
        policy_keywords = [k for k in keywords if any(p in k for p in ["政策", "监管", "补贴", "双碳", "限制"])]
        context_text = self._build_context(search_context, policy_keywords or keywords[:2])
        prompt = f"""你是政策研究专家。
请分析当前影响 {stock_name} 所在行业的政策风向：

{context_text if context_text else "请基于你的知识进行分析。"}

请输出（不超过150字）：
1. 近期最重要的相关政策（支持/限制/中性）
2. 政策对行业/公司的短期影响
3. 政策趋势研判

输出最后一行格式：【信号】利多/利空/中性"""
        content, score, signal = self._llm_with_signal(llm_call, prompt)
        return Section(key="policy", title="政策风向",
                       content=content, score=score, signal=signal)

    # ── 工具方法 ─────────────────────────────────────────────
    @staticmethod
    def _build_context(search_context: dict, keywords: list) -> str:
        parts = []
        for kw in keywords:
            snippets = search_context.get(kw, [])
            if snippets:
                parts.append(f"【{kw}】\n" + "\n".join(f"  {s}" for s in snippets[:2]))
        return "\n\n".join(parts)

    @staticmethod
    def _llm_with_signal(llm_call, prompt) -> tuple[str, int, str]:
        """调用 LLM，从末尾提取【信号】标记，返回 (content, score, signal)。"""
        try:
            content = llm_call(prompt).strip()
        except Exception as e:
            return f"分析失败：{e}", 50, "hold"

        score = 50
        signal = "hold"
        signal_map = {"利多": ("buy", 68), "利空": ("sell", 32), "中性": ("hold", 50)}
        for label, (sig, sc) in signal_map.items():
            if f"【信号】{label}" in content or f"【信号】 {label}" in content:
                signal, score = sig, sc
                break

        return content, score, signal

    @staticmethod
    def _score_to_signal(score: int) -> str:
        if score >= 65: return "buy"
        if score >= 52: return "watch"
        if score >= 38: return "hold"
        return "sell"
