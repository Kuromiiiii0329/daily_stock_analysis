/**
 * tabs/run.js — 立即运行分析
 *
 * 关键设计：
 * - 纯静态模式默认可用（大盘/全量走 /run，UI 可见）
 * - "直接分析"或"直接保存"首次点击时自动尝试启动 server
 * - server 不在线时弹出一次性引导，之后静默轮询恢复
 */

import { ReportView } from '../components/report-view.js';

const SERVER = 'http://127.0.0.1:7788';

const DIM_DEFS = {
  technical: {
    label: '📊 技术面', color: 'blue',
    mods: {
      ma_system: '均线系统 MA5/10/20/60', macd: 'MACD',
      rsi: 'RSI', kdj: 'KDJ', bollinger: '布林带',
      volume: '量价关系', pattern: 'K线形态(LLM)',
      wave: '波浪理论(LLM)', chan: '缠论(LLM)',
    },
    defaults: ['ma_system','macd','rsi','kdj','bollinger','volume'],
  },
  fundamental: {
    label: '📈 基本面', color: 'emerald',
    mods: {
      financials: '核心财报', growth: '成长能力',
      dividend: '分红质量', capital_flow: '主力资金',
      valuation: '估值水平（PE/PB）', business: '主营业务(LLM)',
    },
    defaults: ['financials','growth','valuation','capital_flow'],
  },
  industry: {
    label: '🏭 产业链', color: 'violet',
    mods: {
      key_commodity: '核心商品/产品价格',
      industry_chain: '产业链地位',
      competitors: '竞争格局',
      policy: '政策风向',
    },
    defaults: ['key_commodity','industry_chain','competitors','policy'],
  },
};

export class RunTab {
  constructor(container, store, toast) {
    this._c = container; this._s = store; this._t = toast;
    this._online  = false;
    this._sse     = null;
    this._report  = null;
    this._selDims = new Set(['technical','fundamental','industry']);
    this._selMods = Object.fromEntries(
      Object.entries(DIM_DEFS).map(([k,v]) => [k, new Set(v.defaults)])
    );
    this._serverStarting = false; // 防止重复触发启动
  }

  init() {
    this._render();
    this._s.subscribe(st => this._syncChips(st));
  }

  setServerStatus(online) {
    const changed = online !== this._online;
    this._online = online;
    if (changed) this._updateOnlineUI();
  }

  // ── 主渲染 ────────────────────────────────────────────────
  _render() {
    this._c.innerHTML = `
      <div class="max-w-3xl mx-auto space-y-5 pb-20">

        <!-- 页头 -->
        <div class="flex items-start justify-between">
          <div>
            <h2 class="text-base font-semibold text-gray-900">立即运行分析</h2>
            <p class="text-xs text-gray-500 mt-0.5">深度双维度分析 · 或快速触发大盘/全量任务</p>
          </div>
          <!-- 服务状态 chip -->
          <div id="run-svc-chip"
            class="flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-medium
                   bg-gray-50 border-gray-200 text-gray-400">
            <span class="status-dot offline" id="run-dot"></span>
            <span id="run-svc-label">纯静态模式</span>
          </div>
        </div>

        <!-- ① 深度分析卡片 -->
        <div class="bg-white border border-gray-100 rounded-2xl shadow-sm overflow-hidden">
          <div class="px-5 pt-4 pb-3 border-b border-gray-50">
            <p class="text-sm font-semibold text-gray-800">🔬 深度分析</p>
            <p class="text-xs text-gray-400 mt-0.5">技术面 + 基本面 + 产业链，生成结构化报告</p>
          </div>
          <div class="px-5 py-4 space-y-4">

            <!-- 股票输入 -->
            <div class="flex gap-2">
              <input id="run-code" type="text" placeholder="股票代码，如 002466" maxlength="12"
                class="form-input flex-1" autocomplete="off" />
              <input id="run-name" type="text" placeholder="股票名称（可选）" maxlength="20"
                class="form-input w-36" />
            </div>

            <!-- 自选股快速选 -->
            <div id="run-chips" class="flex flex-wrap gap-1.5 min-h-[1.5rem]"></div>

            <!-- 维度选择 -->
            <div class="space-y-2" id="dim-list"></div>

            <!-- 开始按钮 -->
            <button id="btn-deep"
              class="w-full py-2.5 rounded-xl text-sm font-bold transition-all
                     bg-blue-600 hover:bg-blue-700 text-white shadow-sm shadow-blue-200
                     active:scale-[.99]">
              🔬 开始深度分析
            </button>
          </div>
        </div>

        <!-- ② 快速任务行 -->
        <div class="grid grid-cols-2 gap-3">
          <div class="bg-white border border-gray-100 rounded-2xl shadow-sm p-4">
            <p class="text-sm font-semibold text-gray-800 mb-1">🌐 大盘复盘</p>
            <p class="text-xs text-gray-400 mb-3">分析大盘指数走势和市场情绪</p>
            <div class="flex items-center justify-between">
              <label class="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer">
                <input type="checkbox" id="run-market-force" class="accent-blue-600" />强制运行
              </label>
              <button id="btn-market"
                class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs
                       font-semibold rounded-lg transition-colors">
                ▶ 运行
              </button>
            </div>
          </div>
          <div class="bg-white border border-gray-100 rounded-2xl shadow-sm p-4">
            <p class="text-sm font-semibold text-gray-800 mb-1">⚡ 全量分析</p>
            <p class="text-xs text-gray-400 mb-3">按 watchlist.json 分析所有自选股</p>
            <div class="flex items-center justify-between">
              <label class="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer">
                <input type="checkbox" id="run-full-force" class="accent-blue-600" />强制运行
              </label>
              <button id="btn-full"
                class="px-3 py-1.5 bg-purple-600 hover:bg-purple-700 text-white text-xs
                       font-semibold rounded-lg transition-colors">
                ▶ 运行
              </button>
            </div>
          </div>
        </div>

        <!-- ③ 日志区（运行时显示） -->
        <div id="run-log-section" class="hidden bg-white border border-gray-100 rounded-2xl shadow-sm overflow-hidden">
          <div class="flex items-center justify-between px-4 py-2.5 border-b border-gray-50">
            <div class="flex items-center gap-2">
              <span class="text-xs font-semibold text-gray-700">运行日志</span>
              <span id="run-badge"
                class="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">运行中</span>
            </div>
            <button id="btn-clear-log" class="text-xs text-gray-300 hover:text-gray-500">清空</button>
          </div>
          <div id="log-box" class="h-48 overflow-y-auto p-3 bg-gray-950" id="log-box"></div>
        </div>

        <!-- ④ 报告区（完成后显示） -->
        <div id="run-report-section" class="hidden">
          <div class="flex items-center justify-between mb-3">
            <p class="text-sm font-semibold text-gray-800">📄 分析报告</p>
            <button id="btn-copy-report"
              class="text-xs px-3 py-1.5 bg-gray-100 hover:bg-gray-200 rounded-lg text-gray-600 transition-colors">
              复制
            </button>
          </div>
          <div id="report-structured"></div>
          <div id="report-markdown" class="hidden bg-white border border-gray-100 rounded-2xl p-5
               text-sm text-gray-700 leading-relaxed"></div>
        </div>
      </div>`;

    this._buildDims();
    this._bindEvents();
    this._updateOnlineUI();
    this._report = new ReportView(this._c.querySelector('#report-structured'));
  }

  // ── 维度选择器 ────────────────────────────────────────────
  _buildDims() {
    const list = this._c.querySelector('#dim-list');
    Object.entries(DIM_DEFS).forEach(([dimKey, dimDef]) => {
      const colorMap = { blue: 'blue', emerald: 'emerald', violet: 'violet' };
      const c = colorMap[dimDef.color] || 'blue';
      const wrap = document.createElement('div');
      wrap.className = `border border-gray-100 rounded-xl overflow-hidden`;
      const checked = this._selDims.has(dimKey);
      wrap.innerHTML = `
        <div class="flex items-center gap-3 px-4 py-2.5 bg-gray-50 hover:bg-gray-100 cursor-pointer transition-colors"
             data-dim="${dimKey}">
          <input type="checkbox" id="dim-${dimKey}" ${checked ? 'checked' : ''}
            class="w-4 h-4 accent-${c}-600 cursor-pointer" />
          <label for="dim-${dimKey}" class="text-sm font-medium text-gray-700 cursor-pointer flex-1 select-none">
            ${dimDef.label}
          </label>
          <span class="text-xs text-gray-400">▼ 子模块</span>
        </div>
        <div class="dim-mods ${checked ? '' : 'hidden'} px-4 py-2.5 grid grid-cols-2 sm:grid-cols-3 gap-2 bg-white"
             data-dim="${dimKey}">
          ${Object.entries(dimDef.mods).map(([mk, ml]) => `
            <label class="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer hover:text-${c}-600">
              <input type="checkbox" class="mod-cb accent-${c}-600 w-3.5 h-3.5"
                data-dim="${dimKey}" data-mod="${mk}"
                ${this._selMods[dimKey]?.has(mk) ? 'checked' : ''} />
              ${ml}
            </label>`).join('')}
        </div>`;
      list.appendChild(wrap);
    });

    // 维度行点击（不点 checkbox 本身时切换）
    list.querySelectorAll('[data-dim]').forEach(row => {
      if (row.classList.contains('dim-mods')) return;
      row.addEventListener('click', e => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'LABEL') return;
        const cb = row.querySelector('input[type=checkbox]');
        cb.checked = !cb.checked; cb.dispatchEvent(new Event('change'));
      });
    });
    list.querySelectorAll('[id^="dim-"]').forEach(cb => {
      cb.addEventListener('change', () => {
        const k = cb.id.replace('dim-', '');
        const modsEl = list.querySelector(`.dim-mods[data-dim="${k}"]`);
        cb.checked ? this._selDims.add(k) : this._selDims.delete(k);
        modsEl?.classList.toggle('hidden', !cb.checked);
      });
    });
    list.querySelectorAll('.mod-cb').forEach(cb => {
      cb.addEventListener('change', () => {
        const { dim, mod } = cb.dataset;
        cb.checked ? this._selMods[dim]?.add(mod) : this._selMods[dim]?.delete(mod);
      });
    });
  }

  // ── 自选股芯片 ────────────────────────────────────────────
  _syncChips(state) {
    const chips = this._c.querySelector('#run-chips');
    if (!chips) return;
    const stocks = state.stock_list || [];
    if (!stocks.length) {
      chips.innerHTML = '<span class="text-xs text-gray-300">（自选股为空，先去「自选股」Tab 添加）</span>';
      return;
    }
    chips.innerHTML = stocks.map(code => `
      <button class="chip px-2.5 py-1 text-xs border border-gray-200 rounded-lg font-mono text-gray-600"
              data-code="${code}">${code}</button>`).join('');
    chips.querySelectorAll('.chip').forEach(btn => {
      btn.addEventListener('click', () => {
        this._c.querySelector('#run-code').value = btn.dataset.code;
        chips.querySelectorAll('.chip').forEach(b => b.classList.toggle('selected', b === btn));
      });
    });
  }

  // ── 在线状态 UI 更新 ───────────────────────────────────────
  _updateOnlineUI() {
    const dot   = this._c.querySelector('#run-dot');
    const label = this._c.querySelector('#run-svc-label');
    const chip  = this._c.querySelector('#run-svc-chip');
    if (!dot) return;
    if (this._online) {
      dot.className   = 'status-dot online';
      label.textContent = '本地服务在线';
      chip.className = chip.className.replace(/bg-\S+|border-\S+|text-\S+/g, '').trim()
        + ' bg-green-50 border-green-200 text-green-700';
    } else {
      dot.className   = 'status-dot offline';
      label.textContent = '纯静态模式';
      chip.className = chip.className.replace(/bg-\S+|border-\S+|text-\S+/g, '').trim()
        + ' bg-gray-50 border-gray-200 text-gray-400';
    }
  }

  // ── 事件绑定 ──────────────────────────────────────────────
  _bindEvents() {
    this._c.querySelector('#btn-deep').addEventListener('click',   () => this._deepAnalyze());
    this._c.querySelector('#btn-market').addEventListener('click', () => this._quickRun('market'));
    this._c.querySelector('#btn-full').addEventListener('click',   () => this._quickRun('full'));
    this._c.querySelector('#run-code').addEventListener('keydown', e => { if (e.key === 'Enter') this._deepAnalyze(); });
    this._c.querySelector('#btn-clear-log').addEventListener('click', () => {
      this._c.querySelector('#log-box').innerHTML = '';
    });
    this._c.querySelector('#btn-copy-report').addEventListener('click', async () => {
      const box = this._c.querySelector('#report-markdown');
      const sbox = this._c.querySelector('#report-structured');
      try {
        await navigator.clipboard.writeText(box.dataset.raw || sbox.innerText || '');
        this._t.show('已复制', 'success');
      } catch { this._t.show('复制失败', 'warning'); }
    });
  }

  // ── 自动启动 server ────────────────────────────────────────
  /**
   * 点击"直接分析"或"直接保存"时调用。
   * 若 server 已在线，直接返回 true。
   * 若未在线，尝试通过 fetch 触发 server（实际上由用户后台运行 server.py，
   * 这里弹一次提示，然后轮询等待最多 20 秒）。
   */
  async _ensureServer() {
    if (this._online) return true;
    if (this._serverStarting) {
      this._t.show('正在等待本地服务启动...', 'warning');
      return false;
    }
    this._serverStarting = true;

    // 弹出提示
    this._t.show('需要本地服务，请运行：python portal/server.py', 'warning', 8000);

    // 轮询等待 20 秒
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const r = await fetch(`${SERVER}/health`, { signal: AbortSignal.timeout(1000) });
        if (r.ok) {
          this._online = true;
          this._serverStarting = false;
          this._updateOnlineUI();
          // 通知 app.js 的全局状态（通过自定义事件）
          window.dispatchEvent(new CustomEvent('server-online'));
          return true;
        }
      } catch {}
    }
    this._serverStarting = false;
    this._t.show('本地服务未启动，请先运行 server.py', 'error');
    return false;
  }

  // ── 深度分析 ──────────────────────────────────────────────
  async _deepAnalyze() {
    const code = this._c.querySelector('#run-code').value.trim().toUpperCase();
    const name = this._c.querySelector('#run-name').value.trim();
    if (!code) { this._t.show('请输入股票代码', 'warning'); return; }

    const dims = [...this._selDims];
    if (!dims.length) { this._t.show('请至少选择一个分析维度', 'warning'); return; }

    // ★ 自动激活 server
    const ok = await this._ensureServer();
    if (!ok) return;

    const modulesMap = Object.fromEntries(dims.map(d => [d, [...(this._selMods[d] || [])]]));
    this._prepareLog(`🔬 深度分析：${name || code}（${code}）`);

    let taskId;
    try {
      const res = await fetch(`${SERVER}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stock_code: code, stock_name: name || code, dimensions: dims, modules: modulesMap }),
      });
      const d = await res.json();
      if (!d.ok) throw new Error(d.error);
      taskId = d.task_id;
    } catch (e) {
      this._t.show(`启动失败: ${e.message}`, 'error');
      this._resetBtns(); return;
    }
    this._listenSSE(taskId, 'structured');
  }

  // ── 快速任务 ──────────────────────────────────────────────
  async _quickRun(mode) {
    // ★ 自动激活 server
    const ok = await this._ensureServer();
    if (!ok) return;

    const force = this._c.querySelector(mode === 'market' ? '#run-market-force' : '#run-full-force')?.checked;
    this._prepareLog(mode === 'market' ? '🌐 大盘复盘' : '⚡ 全量分析');

    let taskId;
    try {
      const res = await fetch(`${SERVER}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, force_run: !!force, no_notify: true }),
      });
      const d = await res.json();
      if (!d.ok) throw new Error(d.error);
      taskId = d.task_id;
    } catch (e) {
      this._t.show(`启动失败: ${e.message}`, 'error');
      this._resetBtns(); return;
    }
    this._listenSSE(taskId, 'markdown');
  }

  // ── 日志区准备 ────────────────────────────────────────────
  _prepareLog(title) {
    if (this._sse) { this._sse.close(); this._sse = null; }
    const logSec = this._c.querySelector('#run-log-section');
    const rptSec = this._c.querySelector('#run-report-section');
    logSec.classList.remove('hidden');
    rptSec.classList.add('hidden');
    this._c.querySelector('#log-box').innerHTML = '';
    this._c.querySelector('#report-structured').innerHTML = '';
    const badge = this._c.querySelector('#run-badge');
    badge.textContent = '运行中';
    badge.className = 'text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium';
    this._appendLog(title, 'info');
    this._disableBtns();
  }

  // ── SSE 监听 ──────────────────────────────────────────────
  _listenSSE(taskId, rtype) {
    const sse = new EventSource(`${SERVER}/run/stream/${taskId}`);
    this._sse = sse;
    const box = this._c.querySelector('#log-box');
    sse.onmessage = e => {
      const d = JSON.parse(e.data);
      this._appendLog(d.log, d.status === 'error' ? 'error' : 'default');
    };
    sse.addEventListener('done', e => {
      sse.close(); this._sse = null;
      const d = JSON.parse(e.data);
      const ok = d.status === 'done';
      const badge = this._c.querySelector('#run-badge');
      badge.textContent = ok ? '完成 ✓' : '失败 ✗';
      badge.className = `text-xs px-2 py-0.5 rounded-full font-medium ${ok ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`;
      this._resetBtns();
      if (d.has_report) this._loadReport(taskId, d.report_type || rtype);
    });
    sse.onerror = () => {
      sse.close(); this._sse = null;
      this._appendLog('⚠️ 连接断开，拉取结果...', 'warn');
      setTimeout(() => this._loadReport(taskId, rtype), 600);
      this._resetBtns();
    };
  }

  async _loadReport(taskId, rtype) {
    try {
      const res = await fetch(`${SERVER}/run/report/${taskId}`);
      const d = await res.json();
      const box = this._c.querySelector('#log-box');
      if (d.logs) {
        const ex = box.children.length;
        d.logs.slice(ex).forEach(l => this._appendLog(l));
      }
      if (d.report) {
        rtype === 'structured' ? this._showStructured(d.report) : this._showMarkdown(d.report);
      }
    } catch {}
  }

  _showStructured(json) {
    try {
      const rpt = typeof json === 'string' ? JSON.parse(json) : json;
      const sec = this._c.querySelector('#run-report-section');
      this._c.querySelector('#report-markdown').classList.add('hidden');
      sec.classList.remove('hidden');
      this._report.render(rpt);
      sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch { this._showMarkdown(String(json)); }
  }

  _showMarkdown(md) {
    const sec = this._c.querySelector('#run-report-section');
    const el  = this._c.querySelector('#report-markdown');
    this._c.querySelector('#report-structured').innerHTML = '';
    el.dataset.raw = md;
    el.innerHTML   = this._md(md);
    el.classList.remove('hidden');
    sec.classList.remove('hidden');
    sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  _appendLog(line, type = 'default') {
    const box = this._c.querySelector('#log-box');
    if (!line || !box) return;
    const div = document.createElement('div');
    div.className = 'text-xs leading-relaxed font-mono';
    const colors = {
      info:    '#6ee7b7', error: '#fca5a5',
      warn:    '#fcd34d', default: '#d1fae5',
    };
    let col = colors.default;
    if (/✅|成功|完成/.test(line))      col = colors.info;
    else if (/❌|失败|ERROR/.test(line)) col = colors.error;
    else if (/⚠|WARNING/.test(line))    col = colors.warn;
    div.style.color = col;
    div.textContent = line;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  _disableBtns() {
    ['#btn-deep','#btn-market','#btn-full'].forEach(s =>
      this._c.querySelector(s) && (this._c.querySelector(s).disabled = true));
  }
  _resetBtns() {
    ['#btn-deep','#btn-market','#btn-full'].forEach(s =>
      this._c.querySelector(s) && (this._c.querySelector(s).disabled = false));
  }

  _md(md) {
    const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    return md.split('\n').map(l => {
      if (/^### (.+)/.test(l)) return `<h3 class="font-bold mt-3 mb-1 text-sm">${esc(l.slice(4))}</h3>`;
      if (/^## (.+)/.test(l))  return `<h2 class="font-bold mt-4 mb-2 text-base border-b pb-1">${esc(l.slice(3))}</h2>`;
      if (/^# (.+)/.test(l))   return `<h1 class="font-bold mt-5 mb-2 text-lg">${esc(l.slice(2))}</h1>`;
      if (/^---+$/.test(l))    return '<hr class="my-3 border-gray-200"/>';
      if (/^[-*] (.+)/.test(l)) return `<p class="ml-4">• ${esc(l.slice(2))}</p>`;
      if (!l.trim()) return '<br/>';
      let h = esc(l);
      h = h.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
      h = h.replace(/`(.+?)`/g,'<code class="bg-gray-100 px-1 rounded text-blue-700 text-xs">$1</code>');
      return `<p>${h}</p>`;
    }).join('\n');
  }
}
