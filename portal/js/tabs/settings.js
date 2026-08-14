/**
 * tabs/settings.js — 分析参数设置（纯静态，无需 server）
 * Toggle 用 JS 驱动，不依赖 Tailwind peer（innerHTML 里 peer 无效）
 */
export class SettingsTab {
  constructor(container, store, toast) {
    this._c = container; this._s = store; this._t = toast;
  }

  init() {
    this._c.innerHTML = `
      <div class="max-w-2xl mx-auto space-y-6 pb-20">
        <div>
          <h2 class="text-base font-semibold text-gray-900">分析设置</h2>
          <p class="text-xs text-gray-500 mt-0.5">修改后点击底部「保存配置」导出</p>
        </div>

        <div class="bg-white rounded-2xl border border-gray-100 shadow-sm divide-y divide-gray-50">

          ${this._row('报告类型', `
            <select id="cfg-report-type" class="form-select w-48">
              <option value="simple">简洁（推荐）</option>
              <option value="brief">极简</option>
              <option value="full">完整</option>
            </select>`, '影响邮件内容详细程度')}

          ${this._row('大盘复盘', `
            <div class="flex items-center gap-3">
              <button id="cfg-market-review-btn" type="button"
                class="relative flex-shrink-0 w-10 h-5 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-1 bg-blue-500"
                role="switch" aria-checked="true">
                <span id="cfg-market-thumb"
                  class="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform translate-x-5"></span>
              </button>
              <input type="checkbox" id="cfg-market-review" class="hidden" checked />
              <span class="text-sm text-gray-700" id="cfg-market-label">已启用</span>
            </div>`, '每日附带大盘指数复盘')}

          ${this._row('大盘市场', `
            <select id="cfg-market-region" class="form-select w-40">
              <option value="cn">A股（沪深）</option>
              <option value="hk">港股</option>
              <option value="us">美股</option>
              <option value="both">全市场</option>
            </select>`)}

          ${this._row('分析并发数', `
            <div class="flex items-center gap-2">
              <input type="number" id="cfg-workers" min="1" max="5" class="form-input w-20" />
              <span class="text-xs text-gray-400">建议 1-3，过高易限流</span>
            </div>`)}

          ${this._row('LLM 间隔(秒)', `
            <div class="flex items-center gap-2">
              <input type="number" id="cfg-delay" min="0" max="300" class="form-input w-20" />
              <span class="text-xs text-gray-400">防止 API 限流</span>
            </div>`)}

          ${this._row('强制运行', `
            <div class="flex items-center gap-3">
              <button id="cfg-force-btn" type="button"
                class="relative flex-shrink-0 w-10 h-5 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-1 bg-gray-200"
                role="switch" aria-checked="false">
                <span id="cfg-force-thumb"
                  class="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform translate-x-0"></span>
              </button>
              <input type="checkbox" id="cfg-force" class="hidden" />
              <span class="text-xs text-gray-500">跳过交易日检查（含节假日）</span>
            </div>`)}

          ${this._row('邮件主题前缀', `
            <input type="text" id="cfg-email-prefix" maxlength="30" placeholder="A股智能分析"
              class="form-input w-56" />`)}
        </div>

        <!-- AI Key 说明 -->
        <div class="bg-amber-50 border border-amber-100 rounded-2xl p-4">
          <p class="text-xs font-semibold text-amber-800 mb-1.5">🔑 AI 密钥 &amp; 邮件配置</p>
          <p class="text-xs text-amber-700 leading-relaxed">
            <code class="bg-amber-100 px-1 rounded">GEMINI_API_KEY</code>、
            <code class="bg-amber-100 px-1 rounded">EMAIL_SENDER</code>、
            <code class="bg-amber-100 px-1 rounded">EMAIL_PASSWORD</code> 等敏感配置
            存储在 GitHub 仓库 <strong>Settings → Secrets</strong> 中，不在此页面填写。
            详见「说明」Tab。
          </p>
        </div>
      </div>`;

    this._bindToggle('cfg-market-review-btn', 'cfg-market-thumb', 'cfg-market-review',
      (on) => {
        const lbl = this._c.querySelector('#cfg-market-label');
        if (lbl) lbl.textContent = on ? '已启用' : '已关闭';
        this._s.set('market_review_enabled', on);
      });

    this._bindToggle('cfg-force-btn', 'cfg-force-thumb', 'cfg-force',
      (on) => { this._s.set('force_run', on); });

    this._bindInput('cfg-report-type',   'report_type');
    this._bindInput('cfg-market-region', 'market_review_region');
    this._bindInput('cfg-workers',       'max_workers',    v => Math.max(1, Math.min(5, +v || 1)));
    this._bindInput('cfg-delay',         'analysis_delay', v => Math.max(0, +v || 0));
    this._bindInput('cfg-email-prefix',  'email.subject_prefix');

    this._s.subscribe(state => this._sync(state));
  }

  _row(label, control, hint = '') {
    return `
      <div class="flex items-center justify-between px-5 py-3.5 gap-4">
        <div class="flex-shrink-0">
          <p class="text-sm font-medium text-gray-700">${label}</p>
          ${hint ? `<p class="text-xs text-gray-400 mt-0.5">${hint}</p>` : ''}
        </div>
        <div class="flex-shrink-0">${control}</div>
      </div>`;
  }

  _bindToggle(btnId, thumbId, hiddenId, onChange) {
    const btn    = this._c.querySelector(`#${btnId}`);
    const thumb  = this._c.querySelector(`#${thumbId}`);
    const hidden = this._c.querySelector(`#${hiddenId}`);
    if (!btn) return;
    const setOn = (on) => {
      hidden.checked = on;
      btn.setAttribute('aria-checked', String(on));
      btn.classList.toggle('bg-blue-500', on);
      btn.classList.toggle('bg-gray-200', !on);
      thumb.classList.toggle('translate-x-5', on);
      thumb.classList.toggle('translate-x-0', !on);
      onChange(on);
    };
    btn.addEventListener('click', () => setOn(!hidden.checked));
  }

  _bindInput(id, key, xf = v => v) {
    const el = this._c.querySelector(`#${id}`);
    if (!el) return;
    el.addEventListener('input', () => this._s.set(key, xf(el.value)));
  }

  _sync(state) {
    const sv = (id, val) => { const el = this._c.querySelector(`#${id}`); if (el) el.value = val ?? ''; };
    sv('cfg-report-type',   state.report_type);
    sv('cfg-market-region', state.market_review_region);
    sv('cfg-workers',       state.max_workers);
    sv('cfg-delay',         state.analysis_delay);
    sv('cfg-email-prefix',  state.email?.subject_prefix ?? 'A股智能分析');

    this._applyToggle('cfg-market-review-btn', 'cfg-market-thumb', 'cfg-market-review',
      !!state.market_review_enabled,
      (on) => { const l = this._c.querySelector('#cfg-market-label'); if (l) l.textContent = on ? '已启用' : '已关闭'; });
    this._applyToggle('cfg-force-btn', 'cfg-force-thumb', 'cfg-force', !!state.force_run);
  }

  _applyToggle(btnId, thumbId, hiddenId, on, sideEffect) {
    const btn    = this._c.querySelector(`#${btnId}`);
    const thumb  = this._c.querySelector(`#${thumbId}`);
    const hidden = this._c.querySelector(`#${hiddenId}`);
    if (!btn) return;
    hidden.checked = on;
    btn.setAttribute('aria-checked', String(on));
    btn.classList.toggle('bg-blue-500', on);
    btn.classList.toggle('bg-gray-200', !on);
    thumb.classList.toggle('translate-x-5', on);
    thumb.classList.toggle('translate-x-0', !on);
    sideEffect?.(on);
  }
}
