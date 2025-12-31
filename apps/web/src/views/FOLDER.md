<!-- Input: 路由导航 -->
<!-- Output: 页面级 Vue 组件 -->
<!-- Pos: 前端视图目录 -->

# `apps/web/src/views/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 对应 Router 路由的页面级组件。
- `ChatView` 负责流式对话交互（工具调用进度以自然语言展示，并在完成后自动折叠；侧栏统计包含轮询接口平均耗时）；`ConfigView` 提供按用户在线 LLM 配置（独立端口 8001）。

## 文件
| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `ChatView.vue` | 对话页 | 集成 SSE 客户端、Markdown 渲染与信号侧边栏（信号默认仅展示最新 N 条并优先最新）。 |
| `DashboardView.vue` | 看板页 | 展示窗口内信号统计、热点事件类型、最新输出与轮询平均耗时。 |
| `ConfigView.vue` | 配置页 | 在线 LLM（base_url/model/api_key）配置（public: `/config`；也可在 admin build 8001 入口使用）。 |
| `AdminView.vue` | Legacy | 旧管理台页面（已不在默认路由中使用）。 |
