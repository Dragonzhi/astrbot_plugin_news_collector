# astrbot_plugin_news_collector

AstrBot 每日新闻收集 + LLM 智能整理插件。

每天定时从多个来源收集新闻（AI/科技/GitHub Trending/游戏开发），使用 AstrBot 配置的 LLM 模型整理成结构化简报，推送到指定群组或用户。

## 功能

- **多源新闻收集**：从多个公开 API 聚合 AI 资讯、科技动态、GitHub Trending、游戏开发新闻
- **LLM 智能整理**：调用 AstrBot 配置的 LLM 模型，对原始新闻进行分类、摘要、去重，生成结构化简报
- **定时推送**：每天在指定时间自动推送到配置的群组或用户
- **手动查询**：通过 `/新闻` 命令随时获取当日简报
- **管理命令**：管理员可通过 `/新闻管理 push` 手动推送

## 安装

### 方式一：通过 AstrBot 面板安装

1. 进入 AstrBot Dashboard → 插件管理
2. 点击「安装插件」→ 输入仓库 URL：`https://github.com/your-username/astrbot_plugin_news_collector`
3. 在插件配置页面完成配置并启用

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/your-username/astrbot_plugin_news_collector.git
```

重启 AstrBot 或重载插件即可生效。

## 配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `groups` | list | `[]` | 推送目标列表。格式：`bot名称:MessageType:ID`，例如 `napcat:FriendMessage:3248569738` 推送私聊 |
| `push_time` | string | `08:00` | 每天推送时间，格式 HH:MM，多个时间用逗号分隔 |
| `enable_ai` | bool | `true` | 是否收集 AI/人工智能新闻 |
| `enable_tech` | bool | `true` | 是否收集科技圈/GitHub Trending 新闻 |
| `enable_game` | bool | `true` | 是否收集游戏开发新闻 |
| `enable_llm_organize` | bool | `true` | 是否使用 LLM 整理新闻（关闭则直接拼接原始数据） |
| `llm_model` | string | `""` | 用于新闻整理的模型 ID，留空使用 AstrBot 默认模型 |
| `api_key` | string | `""` | 新闻 API 的鉴权 Key（可选） |
| `timeout` | int | `30` | 请求新闻 API 的超时时间（秒） |

### 推送目标格式说明

推送目标使用 AstrBot 的统一目标格式：

| 类型 | 格式示例 | 说明 |
|------|----------|------|
| QQ 私聊 | `napcat:FriendMessage:你的QQ号` | 推送到自己的 QQ |
| QQ 群聊 | `napcat:GroupMessage:群号` | 推送到群 |
| 其他平台 | 类似格式，前缀为平台适配器名称 | 兼容 Telegram 等 |

## 使用

### 用户命令

| 命令 | 说明 |
|------|------|
| `/新闻` | 手动获取今日新闻简报 |
| `/新闻状态` | 查看插件运行状态 |

### 管理员命令

| 命令 | 说明 |
|------|------|
| `/新闻管理 push` | 手动推送新闻到所有配置目标 |
| `/新闻管理 status` | 查看插件详细状态 |

## 新闻来源

当前集成的新闻源：

| 来源 | 内容 | 接口 |
|------|------|------|
| 每日60s新闻 | 综合每日热点新闻 | `api.nycnm.cn` |
| AI资讯 | AI 领域最新动态 | `api.nycnm.cn` |
| GitHub Trending | GitHub 热门开源项目 | `api.oioweb.cn` |
| 游戏开发 | 游戏引擎/开发相关 | 从综合新闻中筛选 + 兜底条目 |

> 新闻源 API 均为公开免费的聚合接口。如某个接口不可用，插件会自动跳过，不影响其他分类。

## 新闻简报示例

```
每日新闻简报 — 2026-05-21（周四）

## AI/人工智能

### 1. Google I/O 2026：Gemini 全家桶发布
Google 在 I/O 大会上正式发布 Gemini 3.5 Flash、Gemini Omni
和 Gemini Spark 三大模型，战略重心转向 AI Agent 生态。
- 来源：Google Blog
- 链接：https://blog.google/...

## 科技圈/GitHub热门

### 1. github/spec-kit
Spec-Driven Development 工具包，79K Stars
- 来源：GitHub Trending
- 链接：https://github.com/github/spec-kit

> 简报由 LLM 自动整理，内容截至 2026-05-21 16:26
```

## 依赖

- `aiohttp`（通常 AstrBot 已自带）
- AstrBot >= 4.0

## License

AGPL-3.0
