/**
 * store.js — 全局状态管理 + localStorage 持久化
 */

const KEY = 'dsa_portal_v2';

const DEFAULTS = {
  stock_list: [],
  report_type: 'simple',
  market_review_enabled: true,
  market_review_region: 'cn',
  analysis_delay: 0,
  max_workers: 1,
  force_run: false,
  email: { enabled: true, subject_prefix: 'A股智能分析' },
};

class Store {
  constructor() {
    this._state = this._load();
    this._subs = [];
  }

  get() { return structuredClone(this._state); }

  addStock(raw) {
    const code = raw.trim().toUpperCase();
    if (!code) return { ok: false, msg: '代码不能为空' };
    if (this._state.stock_list.includes(code)) return { ok: false, msg: `${code} 已存在` };
    this._state.stock_list = [...this._state.stock_list, code];
    this._save(); this._emit(); return { ok: true };
  }

  removeStock(code) {
    this._state.stock_list = this._state.stock_list.filter(c => c !== code);
    this._save(); this._emit();
  }

  set(key, value) {
    if (key.includes('.')) {
      const [p, c] = key.split('.');
      this._state[p] = { ...this._state[p], [c]: value };
    } else {
      this._state[key] = value;
    }
    this._save(); this._emit();
  }

  subscribe(fn) { this._subs.push(fn); fn(this.get()); }

  toJSON() {
    return JSON.stringify({
      _schema_version: '1',
      _comment: '由 portal/index.html 生成，提交到仓库后被 portal-daily-analysis.yml 读取',
      updated_at: new Date().toISOString(),
      ...this._state,
    }, null, 2);
  }

  _emit() { const s = this.get(); this._subs.forEach(f => f(s)); }

  _save() {
    try { localStorage.setItem(KEY, JSON.stringify(this._state)); } catch {}
  }

  _load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) return this._merge(DEFAULTS, JSON.parse(raw));
    } catch {}
    return structuredClone(DEFAULTS);
  }

  _merge(base, over) {
    const r = structuredClone(base);
    for (const k of Object.keys(over)) {
      if (k in r && typeof r[k] === 'object' && !Array.isArray(r[k]) && r[k] !== null)
        r[k] = this._merge(r[k], over[k]);
      else r[k] = over[k];
    }
    return r;
  }
}

export const store = new Store();
