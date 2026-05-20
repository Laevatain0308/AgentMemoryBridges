# Web 界面设计

## 技术栈

- **CSS 框架**：[PicoCSS v2](https://picocss.com/)（轻量 class‑less 语义化框架）
- **交互**：[htmx 1.9](https://htmx.org/)（无 JS 的 AJAX 交互）
- **Markdown 渲染**：[marked.js](https://marked.js.org/)（客户端渲染，GitHub 风格）
- **模板引擎**：Jinja2（服务端渲染）

## 页面结构

| 路由 | 模板 | 说明 |
|------|------|------|
| `/` | index.html | 知识库首页，支持项目/分类/关键词筛选 |
| `/memory/{id}` | memory.html | 记忆详情页，Markdown 渲染 |
| `/login` | login.html | 管理员登录 |
| `/admin` | admin.html | Token 管理（创建/吊销） |
| `/admin/settings` | settings.html | 系统设置（Git 备份配置） |

## 样式定制

所有定制样式在 `server/static/style.css`，主要包括：

- 导航栏底部边框
- `.markdown-body` GitHub 风格渲染（标题、代码块、表格、引用块）
- 移动端响应式布局

## 配色

使用 PicoCSS 默认亮色主题，通过 CSS 变量可切换暗色模式：

```css
html[data-theme="dark"] { /* 待实现 */ }
```
