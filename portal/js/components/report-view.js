/**
 * components/report-view.js — 双栏结构化报告渲染组件
 *
 * 接受 merger.py 返回的 JSON，渲染：
 *   左栏：技术面
 *   右栏：基本面 + 产业链
 *   底部：综合结论 + 评分
 */

const SIGNAL_CONFIG = {
  buy:   { label: '买入',  bg: 'bg-green-100',  text: 'text-green-700',  border: 'border-green-300',  dot: '#16a34a' },
  watch: { label: '关注',  bg: 'bg-blue-100',   text: 'text-blue-700',   border: 'border-blue-300',   dot: '#2563eb' },
  hold:  { label: '持有',  bg: 'bg-gray-100',   text: 'text-gray-600',   border: 'border-gray-300',   dot: '#6b7280' },
  sell:  { label: '卖出',  bg: 'bg-red-100',    text: 'text-red-700',    border: 'border-red-300',    dot: '#dc2626' },
};

const DIM_ICONS = {
  technical:   '📊',
  fundamental: '📈',
  industry:    '🏭',
};

export class ReportView {
  /**
   * @param {HTMLElement} container  报告挂载节点
   */
  constructor(container) {
    this._container = container;
  }

  /** 渲染结构化报告（merger.py 返回的 JSON 对象） */
  render(report) {
    const { stock_code, stock_name, overall_score, overall_signal,
            overall_signal_label, conclusion, dimensions, generated_at } = report;

    const sig = SIGNAL_CONFIG[overall_signal] || SIGNAL_CONFIG.hold;

    this._container.innerHTML = `
      <!-- 报告头部：左侧股票信息 + 右侧雷达图 -->
      <div class="mb-5 p-4 rounded-xl bg-gradient-to-r from-gray-50 to-blue-50 border border-gray-200">
        <div class="flex items-center justify-between flex-wrap gap-3">
          <!-- 左侧：股票名称/代码/时间 -->
          <div>
            <span class="font-bold text-xl text-gray-900">${this._esc(stock_name)}</span>
            <span class="ml-2 font-mono text-sm text-gray-500">${this._esc(stock_code)}</span>
            <div class="mt-1 text-xs text-gray-400">${this._fmtTime(generated_at)}</div>
          </div>
          <!-- 右侧：ECharts 雷达图 -->
          <div id="radar-chart" style="width:220px;height:160px;flex-shrink:0"></div>
        </div>
      </div>

      <!-- 双栏维度报告 -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-5" id="report-columns"></div>

      <!-- 综合结论 -->
      <div class="p-4 rounded-xl border-2 ${sig.border} ${sig.bg}">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-base font-bold ${sig.text}">🎯 综合结论</span>
          <span class="text-xs px-2 py-0.5 rounded-full bg-white/70 ${sig.text} font-medium border ${sig.border}">
            ${overall_signal_label || sig.label}
          </span>
          <span class="text-xs text-gray-500 ml-auto">综合评分 ${overall_score}/100</span>
        </div>
        <p class="text-sm text-gray-700 leading-relaxed">${this._esc(conclusion)}</p>
      </div>
    `;

    // 渲染各维度列
    const colContainer = this._container.querySelector('#report-columns');

    // 技术面单独一栏，基本面+产业链合并一栏
    const techDims  = dimensions.filter(d => d.dimension === 'technical');
    const otherDims = dimensions.filter(d => d.dimension !== 'technical');

    if (techDims.length) {
      const col = document.createElement('div');
      col.className = 'space-y-4';
      techDims.forEach(d => col.appendChild(this._renderDimension(d, report)));
      colContainer.appendChild(col);
    }

    if (otherDims.length) {
      const col = document.createElement('div');
      col.className = 'space-y-4';
      otherDims.forEach(d => col.appendChild(this._renderDimension(d, report)));
      colContainer.appendChild(col);
    }

    // 延迟初始化图表（等 DOM 渲染完毕）
    setTimeout(() => {
      this._initRadar(report);
      this._initKlineChart(report);
    }, 0);
  }

  _renderDimension(dim, report) {
    const sig  = SIGNAL_CONFIG[dim.signal] || SIGNAL_CONFIG.hold;
    const icon = DIM_ICONS[dim.dimension] || '📋';

    const el = document.createElement('div');
    el.className = 'bg-white border border-gray-200 rounded-xl overflow-hidden';

    // 技术面且有 kline_data：在卡片顶部插入折线图占位
    const klineHtml = (dim.dimension === 'technical' && report && report.kline_data)
      ? `<div id="kline-chart-${this._esc(report.stock_code)}" style="height:180px;padding:4px 0"></div>`
      : '';

    el.innerHTML = `
      ${klineHtml}
      <!-- 维度头部 -->
      <div class="flex items-center justify-between px-4 py-3 bg-gray-50 border-b border-gray-100">
        <div class="flex items-center gap-2">
          <span class="text-base">${icon}</span>
          <span class="font-semibold text-sm text-gray-800">${this._esc(dim.name)}</span>
          ${dim.error ? '<span class="text-xs text-red-500">⚠️ 分析异常</span>' : ''}
        </div>
        <div class="flex items-center gap-2">
          ${this._scoreBadge(dim.score, dim.signal, 'sm')}
        </div>
      </div>

      <!-- 一句话摘要 -->
      ${dim.summary ? `
        <div class="px-4 py-2 bg-${sig.bg.replace('bg-','')} border-b border-gray-100">
          <p class="text-xs ${sig.text} leading-relaxed">${this._esc(dim.summary)}</p>
        </div>
      ` : ''}

      <!-- 子模块列表 -->
      <div class="divide-y divide-gray-100" id="sections-${dim.dimension}"></div>
    `;

    const sectionsEl = el.querySelector(`#sections-${dim.dimension}`);
    if (dim.error) {
      sectionsEl.innerHTML = `<div class="px-4 py-3 text-sm text-red-500">${this._esc(dim.error)}</div>`;
    } else {
      dim.sections.forEach(sec => sectionsEl.appendChild(this._renderSection(sec)));
    }

    return el;
  }

  _renderSection(sec) {
    const sig = SIGNAL_CONFIG[sec.signal] || SIGNAL_CONFIG.hold;
    const el = document.createElement('details');
    el.className = 'group';
    el.innerHTML = `
      <summary class="flex items-center justify-between px-4 py-2.5 cursor-pointer hover:bg-gray-50 select-none list-none">
        <div class="flex items-center gap-2">
          <span class="text-gray-400 group-open:rotate-90 transition-transform text-xs">▶</span>
          <span class="text-sm font-medium text-gray-700">${this._esc(sec.title)}</span>
        </div>
        <span class="text-xs px-1.5 py-0.5 rounded ${sig.bg} ${sig.text} font-medium">
          ${sig.label}
        </span>
      </summary>
      <div class="px-4 pb-3 pt-1 text-xs text-gray-600 leading-relaxed bg-gray-50/50">
        ${this._mdToHtml(sec.content)}
      </div>
    `;
    // 默认展开评分较高的子模块
    if (sec.score >= 65 || sec.score <= 35) {
      el.setAttribute('open', '');
    }
    return el;
  }

  _initRadar(rpt) {
    const el = this._container.querySelector('#radar-chart');
    if (!el || typeof echarts === 'undefined') return;

    // 销毁旧实例（如果有）
    const existing = echarts.getInstanceByDom(el);
    if (existing) existing.dispose();

    const chart = echarts.init(el);

    // 从 dimensions 取各维度评分
    const dimScores = {};
    (rpt.dimensions || []).forEach(d => { dimScores[d.dimension] = d.score; });

    const techScore  = dimScores['technical']   ?? 0;
    const fundScore  = dimScores['fundamental'] ?? 0;
    const indScore   = dimScores['industry']    ?? 0;

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        formatter: (params) => {
          const vals = params.value;
          return `技术面: ${vals[0]}<br/>基本面: ${vals[1]}<br/>产业链: ${vals[2]}`;
        },
      },
      radar: {
        indicator: [
          { name: '技术面', max: 100 },
          { name: '基本面', max: 100 },
          { name: '产业链', max: 100 },
        ],
        center: ['50%', '50%'],
        radius: '70%',
        axisName: { fontSize: 11, color: '#374151' },
        splitLine: { lineStyle: { color: '#e5e7eb' } },
        splitArea: { areaStyle: { color: ['rgba(239,246,255,0.3)', 'rgba(255,255,255,0)'] } },
        axisLine: { lineStyle: { color: '#d1d5db' } },
      },
      series: [{
        type: 'radar',
        data: [{
          value: [techScore, fundScore, indScore],
          name: '评分',
          itemStyle: { color: '#3b82f6' },
          lineStyle: { color: '#3b82f6', width: 2 },
          areaStyle: { color: 'rgba(59,130,246,0.18)' },
          symbol: 'circle',
          symbolSize: 5,
        }],
      }],
    });
  }

  _initKlineChart(rpt) {
    if (!rpt.kline_data || !rpt.kline_data.length) return;
    if (typeof echarts === 'undefined') return;

    const el = this._container.querySelector(`#kline-chart-${rpt.stock_code}`);
    if (!el) return;

    const existing = echarts.getInstanceByDom(el);
    if (existing) existing.dispose();

    const chart = echarts.init(el);

    // 取最近60条
    const data = rpt.kline_data.slice(-60);

    const dates  = data.map(d => d.date ? d.date.slice(5) : '');  // MM-DD
    const close  = data.map(d => d.close  ?? null);
    const ma5    = data.map(d => d.ma5    ?? null);
    const ma20   = data.map(d => d.ma20   ?? null);

    const makeLineSeries = (name, values, color, lineWidth = 1.5) => ({
      name,
      type: 'line',
      data: values,
      smooth: false,
      symbol: 'none',
      lineStyle: { color, width: lineWidth },
      itemStyle: { color },
    });

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', crossStyle: { color: '#9ca3af' } },
        textStyle: { fontSize: 11 },
        formatter: (params) => {
          const idx = params[0].dataIndex;
          const date = data[idx]?.date || '';
          let html = `<div style="font-size:11px"><b>${date}</b></div>`;
          params.forEach(p => {
            if (p.value != null) {
              html += `<div style="color:${p.color}">${p.seriesName}: ${Number(p.value).toFixed(2)}</div>`;
            }
          });
          return html;
        },
      },
      legend: {
        data: ['收盘价', 'MA5', 'MA20'],
        right: 8, top: 2,
        textStyle: { fontSize: 10, color: '#6b7280' },
        itemWidth: 12, itemHeight: 3,
      },
      grid: { left: 8, right: 8, top: 28, bottom: 20, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 9, color: '#9ca3af', interval: Math.floor(data.length / 6) },
        axisLine: { lineStyle: { color: '#e5e7eb' } },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { fontSize: 9, color: '#9ca3af', formatter: v => v.toFixed(1) },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      series: [
        makeLineSeries('收盘价', close, '#3b82f6', 2),
        makeLineSeries('MA5',   ma5,   '#f97316', 1),
        makeLineSeries('MA20',  ma20,  '#ef4444', 1),
      ],
    });
  }

  _scoreBadge(score, signal, size = 'md') {
    const sig = SIGNAL_CONFIG[signal] || SIGNAL_CONFIG.hold;
    const width  = size === 'sm' ? 'w-8 h-8 text-xs' : 'w-12 h-12 text-sm';
    const color  = score >= 70 ? '#16a34a' : score >= 55 ? '#2563eb' : score >= 40 ? '#6b7280' : '#dc2626';
    const radius = size === 'sm' ? 14 : 20;
    const circum = 2 * Math.PI * radius;
    const dash   = (score / 100) * circum;

    return `
      <div class="flex items-center gap-1.5">
        <svg class="${width}" viewBox="0 0 ${radius * 2 + 8} ${radius * 2 + 8}">
          <circle cx="${radius + 4}" cy="${radius + 4}" r="${radius}"
                  fill="none" stroke="#e5e7eb" stroke-width="3"/>
          <circle cx="${radius + 4}" cy="${radius + 4}" r="${radius}"
                  fill="none" stroke="${color}" stroke-width="3"
                  stroke-dasharray="${dash} ${circum}"
                  stroke-dashoffset="${circum * 0.25}"
                  stroke-linecap="round"/>
          <text x="${radius + 4}" y="${radius + 4}" text-anchor="middle"
                dominant-baseline="central" font-size="${size === 'sm' ? 9 : 11}"
                font-weight="bold" fill="${color}">${score}</text>
        </svg>
        <span class="text-xs px-1.5 py-0.5 rounded ${sig.bg} ${sig.text} font-medium">${sig.label}</span>
      </div>
    `;
  }

  /** 极简 Markdown → HTML */
  _mdToHtml(md) {
    if (!md) return '';
    const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    return md.split('\n').map(line => {
      if (/^\*\*(.+)\*\*$/.test(line)) return `<p class="font-semibold text-gray-800 mt-1">${esc(line.replace(/\*\*/g,''))}</p>`;
      if (/^#{1,3} (.+)/.test(line))   return `<p class="font-semibold text-gray-800 mt-1">${esc(line.replace(/^#+\s/,''))}</p>`;
      if (/^[-*] (.+)/.test(line))     return `<p class="pl-2">• ${esc(line.slice(2))}</p>`;
      if (line.trim() === '')          return '';
      let html = esc(line);
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/`(.+?)`/g, '<code class="bg-gray-200 px-0.5 rounded text-blue-700">$1</code>');
      // 符号着色
      html = html.replace(/(✅|🟢)/g, '<span class="text-green-600">$1</span>');
      html = html.replace(/(⚠️|🔴|❌)/g, '<span class="text-red-500">$1</span>');
      return `<p>${html}</p>`;
    }).filter(Boolean).join('\n');
  }

  _fmtTime(iso) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleString('zh-CN', {
        month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit',
      });
    } catch { return iso; }
  }

  _esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
}
