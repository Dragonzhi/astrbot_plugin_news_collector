# astrbot_plugin_news_collector

AstrBot 每日新闻收集插件。联网搜索 + LLM 整理 + 自动去重。

每天定时搜索新闻（AI/科技/GitHub/游戏开发），去重后交给 LLM 整理成结构化简报，推送到指定目标。

## 功能

- **联网搜索**：支持 Bocha / Tavily / Brave 搜索引擎，获取实时新闻
- **GitHub Trending**：从 GitHub Search API 拉取最近一周热门仓库
- **LLM 智能整理**：调用 AstrBot 配置的 LLM 模型，将搜索结果整理成结构清晰的简报
- **自动去重**：基于 URL 缓存，已推送过的新闻不会重复出现
- **QQ 友好格式**：使用 emoji 排版，非 Markdown，QQ 正常显示
- **定时推送**：每天指定时间自动推送到配置的目标
- **手动查询**：`/新闻` 命令随时获取当日简报

## 安装

### 面板安装

AstrBot Dashboard → 插件管理 → 输入仓库 URL 安装。

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/your-username/astrbot_plugin_news_collector.git
```

重启 AstrBot 或重载插件。

## 配置

### 基本配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `groups` | list | `[]` | 推送目标。格式：`napcat:FriendMessage:你的QQ号` |
| `push_time` | string | `08:00` | 每天推送时间 HH:MM，多个用逗号分隔 |

### 联网搜索（推荐配置）

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `search_provider` | string | `""` | 搜索引擎：`bocha`（推荐国内用）、`tavily`、`brave`。留空则不搜索 |
| `search_api_key` | string | `""` | 对应搜索引擎的 API Key |
| `search_count` | int | `5` | 每个分类搜索返回的结果数 |

搜索引擎注册（免费额度够用）：
- **Bocha**（推荐）：https://bochaai.com → API 管理 → 创建应用
- **Tavily**：https://tavily.com → 注册获取 API Key（每月 1000 次免费）
- **Brave**：https://brave.com/search/api/ → 注册获取

### LLM 整理

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable_llm_organize` | bool | `true` | 是否用 LLM 整理简报。关闭则直接拼接原文 |
| `llm_model` | string | `""` | 指定模型 ID，留空用 AstrBot 默认模型 |

### 去重

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `dedup_ttl_days` | int | `7` | 已推送新闻的去重保留天数。设为 0 关闭去重 |

### 其他

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `timeout` | int | `30` | 请求超时（秒） |

## 使用

### 用户命令

| 命令 | 说明 |
|------|------|
| `/新闻` | 手动获取今日新闻简报 |
| `/新闻状态` | 查看插件运行状态 |

### 管理员命令

| 命令 | 说明 |
|------|------|
| `/新闻管理 push` | 手动推送新闻到所有目标 |
| `/新闻管理 status` | 查看插件详细状态 |

## 输出示例

```
每日新闻简报 — 2026-05-21（周四）

📂 AI/人工智能

🔹 Gemini 3.0 发布
Google 发布新一代大模型 Gemini 3.0，支持原生多模态...
📎 来源：Google Blog
🔗 https://blog.google/...

📂 科技圈/GitHub热门

🔹 vercel-labs/zerolang
专为 AI 智能体设计的编程语言，4142 Stars
📎 来源：GitHub Trending
🔗 https://github.com/vercel-labs/zerolang

📂 游戏开发

🔹 Unreal Engine 5.7 发布
Epic 发布 UE 5.7，大幅优化 Nanite 性能...
📎 来源：Epic Games
🔗 https://www.unrealengine.com/...

> 简报由 LLM + 联网搜索生成，内容截至 2026-05-21 16:26
```

## 数据来源

| 来源 | 内容 | 方式 |
|------|------|------|
| 联网搜索引擎 | AI、科技、游戏开发三大分类新闻 | 按关键词搜索（需配置 API Key） |
| GitHub Search API | 最近一周热门仓库 | 自动拉取，无需配置 |
| LLM | 对以上数据进行分类、摘要、整理 | 调用 AstrBot 配置的 Provider |

## 去重机制

插件会记录每次推送的新闻 URL，下次搜索时自动过滤已推送过的内容。

- 缓存文件位置：`data/plugins/astrbot_plugin_news_collector/_seen_news.json`
- 默认保留 7 天，可在配置中修改
- 删除该文件可重置去重缓存

## 依赖

- `aiohttp`（通常 AstrBot 已自带）
- AstrBot >= 4.0

## License

AGPL-3.0
