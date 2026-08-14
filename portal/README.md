# Portal — 股票分析配置中心

## 作用

Portal 是一个**纯静态网页**，不依赖任何后端服务。你可以：

- 通过浏览器管理自选股列表
- 调整每日分析参数（报告类型、大盘复盘、并发数等）
- 导出配置文件 `config/watchlist.json` 提交到仓库
- 由 GitHub Action `portal-daily-analysis` 读取该配置，每日定时运行分析并**通过邮件发送 HTML 报告**

整个流程无需接触代码：**网页选股 → 提交 JSON → 每日收邮件**。

---

## 文件结构

```
portal/
├── index.html              主页面（单页应用入口，GitHub Pages 直接托管）
├── send_report.py          邮件发送脚本（由 GitHub Action 调用）
└── js/
    ├── app.js              主入口：Tab 路由、初始化所有模块、绑定"保存配置"按钮
    ├── store.js            状态管理：持有所有配置、localStorage 自动持久化、生成 JSON 导出
    ├── tabs/
    │   ├── watchlist.js    选股管理 Tab：添加/删除/展示自选股列表
    │   ├── settings.js     分析设置 Tab：报告类型、大盘开关、并发数、邮件主题等
    │   └── guide.js        使用说明 Tab：分步配置指引、Secrets 说明、常见问题
    └── components/
        ├── modal.js        导出配置模态框：展示格式化 JSON + 一键复制到剪贴板
        └── toast.js        轻量通知条：操作成功/警告/错误的短暂提示
```

### 各文件职责详解

| 文件 | 职责 | 修改场景 |
|------|------|----------|
| `index.html` | HTML 骨架、引入 TailwindCSS CDN、挂载 `app.js` | 调整整体布局、添加新 Tab 入口 |
| `js/app.js` | Tab 切换路由、初始化所有 Tab 模块、绑定底部"保存配置"按钮 | 注册新 Tab 模块 |
| `js/store.js` | 全局状态（对应 `config/watchlist.json` 字段）、localStorage 持久化、发布-订阅通知 UI 刷新、`toJSON()` 导出 | 新增配置字段时在此加 `DEFAULT_STATE` 条目 |
| `js/tabs/watchlist.js` | 选股增删查、代码格式校验、列表渲染 | 修改股票卡片样式、添加股票名称查询等 |
| `js/tabs/settings.js` | 所有分析参数的表单控件，双向绑定 store | 新增参数时加 `_field()` 条目并绑定事件 |
| `js/tabs/guide.js` | 纯静态说明内容，无交互逻辑 | 更新使用步骤、Secrets 表格等文档 |
| `js/components/modal.js` | 导出模态框，展示 JSON + 复制按钮 | 调整模态框样式或导出格式 |
| `js/components/toast.js` | 右下角短暂通知条（成功/警告/错误） | 修改通知样式或持续时间 |
| `send_report.py` | 读取 `reports/` 目录报告文件，组合 Markdown 正文，调用 `EmailSender` 发送 HTML 邮件 | 修改邮件正文格式、调整报告文件路径匹配规则 |

---

## 与其他文件的关系

```
portal/index.html        ──生成──▶  config/watchlist.json（用户手动提交到仓库）
                                          │
                                          ▼
.github/workflows/portal-daily-analysis.yml   ──读取──▶  运行 python main.py
                                          │
                                          ▼
                                    reports/*.md（分析报告文件）
                                          │
                                          ▼
portal/send_report.py    ──读取──▶  EmailSender.send_to_email()  ──▶  你的邮箱
```

`send_report.py` 复用了现有的两个模块（不修改它们）：
- `src/notification_sender/email_sender.py` — SMTP 邮件发送，Markdown 自动转 HTML
- `src/formatters.py` — Markdown → HTML 转换（由 EmailSender 内部调用）

---

## 技术选型说明

| 选型 | 原因 |
|------|------|
| 纯 HTML + 原生 ES Modules | 无需 npm、无构建步骤，`git push` 即更新，GitHub Pages 直接托管 |
| TailwindCSS via CDN | 无需编译，样式完整，CDN 地址 `https://cdn.tailwindcss.com` |
| localStorage 持久化 | 用户编辑内容在浏览器中自动保存，刷新不丢失 |
| 发布-订阅（store.subscribe） | 任意模块修改 store 后，所有订阅的 Tab 自动刷新 UI，扩展新 Tab 不改动现有代码 |
| `SimpleNamespace` mock Config | `send_report.py` 直接用环境变量构造最小对象，避免触发完整 `src/config.py` 初始化（会加载全量依赖） |

---

## 扩展指南

### 新增一个 Tab

1. 在 `portal/js/tabs/` 新建 `my_tab.js`，导出一个类，实现 `init()` 方法：
   ```js
   export class MyTab {
     constructor(container, store, toast) { ... }
     init() { /* 渲染 HTML，绑定事件 */ }
   }
   ```
2. 在 `portal/js/app.js` 顶部 import，并在 `TABS` 数组中注册：
   ```js
   import { MyTab } from './tabs/my_tab.js';
   const TABS = [
     ...,
     { id: 'my_tab', label: '新功能', module: MyTab },
   ];
   ```

### 新增一个配置字段

1. 在 `js/store.js` 的 `DEFAULT_STATE` 中加入默认值
2. 在 `js/tabs/settings.js` 的 `_renderSkeleton()` 中添加对应表单控件，并在 `_bindEvents()` 绑定 `store.updateField()`
3. 在 `config/watchlist.json` 的注释中更新字段说明
4. 在 `.github/workflows/portal-daily-analysis.yml` 的"读取 watchlist.json"步骤中用 `python3 -c` 读取新字段并写入 `GITHUB_ENV`

---

## 本地预览

用浏览器直接打开 `portal/index.html`（双击或拖入浏览器）即可预览，无需启动任何服务器。

> **注意**：由于使用了 ES Modules（`<script type="module">`），部分浏览器在 `file://` 协议下可能因跨域策略报错。
> 解决方法：用 Python 启动一个本地 HTTP 服务：
> ```bash
> cd C:\Users\I762120\Desktop\incident\daily\portal
> python -m http.server 8080
> # 访问 http://localhost:8080
> ```

---

## GitHub Pages 部署

1. 进入仓库 **Settings → Pages**
2. Source 选择 **Deploy from a branch**
3. Branch 选 **main**，Folder 选 **/portal**
4. 保存后等待 1-2 分钟，访问 `https://<你的用户名>.github.io/<仓库名>/`

每次 push 到 main 分支后，页面自动更新，无需额外操作。
