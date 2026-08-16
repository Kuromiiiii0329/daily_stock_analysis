/**
 * tabs/watchlist.js — 选股管理（支持实时行情显示）
 */
export class WatchlistTab {
  constructor(container, store, toast) {
    this._c = container; this._s = store; this._t = toast;
    this._online = false;
    this._quotes = {};   // { "600519": { name, price, pct_chg, volume } }
  }

  init() {
    this._c.innerHTML = `
      <div class="max-w-2xl mx-auto space-y-5 pb-20">
        <div>
          <h2 class="text-base font-semibold text-gray-900">自选股管理</h2>
          <p class="text-xs text-gray-500 mt-0.5">支持 A股（600519）、港股（00700）、美股（AAPL）</p>
        </div>

        <!-- 输入区 -->
        <div class="flex gap-2">
          <input id="wl-input" type="text" placeholder="输入股票代码，如 600519" maxlength="12"
            class="form-input flex-1" autocomplete="off" />
          <button id="wl-add"
            class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors">
            添加
          </button>
        </div>

        <!-- 列表 -->
        <div id="wl-list" class="space-y-1.5"></div>
        <p id="wl-count" class="text-xs text-gray-400"></p>
      </div>`;

    const input = this._c.querySelector('#wl-input');
    this._c.querySelector('#wl-add').addEventListener('click', () => this._add(input));
    input.addEventListener('keydown', e => { if (e.key === 'Enter') this._add(input); });
    this._s.subscribe(state => this._render(state));

    // 初始尝试加载行情
    this._loadQuotes();
  }

  /** 由 app 层在服务器状态变更时调用 */
  setServerStatus(online) {
    this._online = online;
    if (online) this._loadQuotes();
  }

  async _loadQuotes() {
    const state = this._s.getState ? this._s.getState() : null;
    const stocks = (state && state.stock_list) ? state.stock_list : [];
    if (!stocks.length) return;

    if (!this._online) return;

    try {
      const codes = stocks.join(',');
      const res = await fetch(`/quote?codes=${encodeURIComponent(codes)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok || !Array.isArray(data.quotes)) return;

      const map = {};
      for (const q of data.quotes) {
        map[q.code] = { name: q.name, price: q.price, pct_chg: q.pct_chg, volume: q.volume };
      }
      this._quotes = map;
      // 触发重渲染
      const cur = state || {};
      this._render(cur);
    } catch (_) {
      // 网络失败，静默忽略
    }
  }

  _add(input) {
    const r = this._s.addStock(input.value);
    if (r.ok) {
      input.value = '';
      this._t.show('已添加', 'success');
      this._loadQuotes();
    } else {
      this._t.show(r.msg, 'warning');
    }
  }

  _render(state) {
    const list  = this._c.querySelector('#wl-list');
    const count = this._c.querySelector('#wl-count');
    if (!list) return;
    const stocks = state.stock_list || [];
    if (!stocks.length) {
      list.innerHTML = `
        <div class="py-12 text-center text-gray-300">
          <div class="text-4xl mb-2">📋</div>
          <p class="text-sm">还没有自选股</p>
        </div>`;
      if (count) count.textContent = '';
      return;
    }
    list.innerHTML = stocks.map((c, i) => {
      const q = this._quotes[c] || {};
      const name = q.name ? `<span class="text-xs text-gray-500">${this._esc(q.name)}</span>` : '';

      let priceHtml = '';
      if (q.price != null) {
        priceHtml = `<span class="text-xs text-gray-600 font-mono">${q.price.toFixed(2)}</span>`;
      }

      let pctHtml = '';
      if (q.pct_chg != null) {
        const sign  = q.pct_chg >= 0 ? '+' : '';
        const color = q.pct_chg > 0 ? 'text-green-500' : q.pct_chg < 0 ? 'text-red-500' : 'text-gray-400';
        pctHtml = `<span class="text-xs font-semibold ${color}">${sign}${q.pct_chg.toFixed(2)}%</span>`;
      }

      const hasQuote = name || priceHtml || pctHtml;

      return `
        <div class="flex items-center justify-between px-4 py-2.5 bg-white rounded-xl border border-gray-100
                    hover:border-blue-200 hover:shadow-sm transition-all group">
          <div class="flex items-center gap-3">
            <span class="text-xs text-gray-300 w-4 text-right">${i + 1}</span>
            <div class="flex flex-col">
              <span class="font-mono font-semibold text-sm text-gray-800">${this._esc(c)}</span>
              ${hasQuote ? `<div class="flex items-center gap-1.5 mt-0.5">${name}${priceHtml}${pctHtml}</div>` : ''}
            </div>
          </div>
          <button data-del="${this._esc(c)}"
            class="text-gray-200 hover:text-red-400 transition-colors text-lg leading-none opacity-0 group-hover:opacity-100">×</button>
        </div>`;
    }).join('');

    list.querySelectorAll('[data-del]').forEach(b =>
      b.addEventListener('click', () => {
        this._s.removeStock(b.dataset.del);
        this._t.show('已移除', 'info');
      }));
    if (count) count.textContent = `共 ${stocks.length} 只`;
  }

  _esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
}
