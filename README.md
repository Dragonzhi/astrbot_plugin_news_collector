# astrbot_plugin_news_collector

AstrBot 多分类新闻收集插件。联网搜索 + LLM 整理 + 自动去重 + **按目标个性化推送**。

每个推送目标（好友/群）可以指定不同的新闻分类，互不干扰。

## 功能

- **多分类新闻**：内置 AI/人工智能、科技圈/GitHub热门、游戏开发、二次元/游戏、时事热点、科技数码等分类
- **按目标个性化**：每个目标可指定不同的分类，例如大号收 AI+科技，群聊收二次元+时事
- **联网搜索**：支持 Bocha / Tavily / Brave 搜索引擎
- **LLM 智能整理**：调用 AstrBot 配置的 LLM，将搜索结果整理成简报
- **自动去重**：基于 URL 缓存，确保同一条新闻不会重复推送
- **QQ 友好格式**：使用 emoji 排版，非 Markdown

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/your-username/astrbot_plugin_news_collector.git
```

重启 AstrBot 或重载插件。

## 配置

### 推送目标（关键配置）

`groups` 是一个列表，每行一个目标。支持两种格式：

**简单格式（走默认分类）：**
```
napcat:FriendMessage:你的QQ号
napcat:GroupMessage:群号
```

**扩展格式（指定分类）：**
```
napcat:FriendMessage:你的QQ号:AI/人工智能,科技圈/GitHub热门,游戏开发
napcat:GroupMessage:群号:二次元/游戏,时事热点
```

> 分类名称用中文逗号或英文逗号分隔均可。

### 其他配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `categories` | list | AI,科技,游戏 | 默认分类（目标未指定时的兜底） |
| `push_time` | string | `08:00` | 每天推送时间 HH:MM |
| `search_provider` | string | `""` | 搜索引擎：bocha/tavily/brave |
| `search_api_key` | string | `""` | 搜索引擎 API Key |
| `search_count` | int | `5` | 每类搜索返回条数 |
| `enable_llm_organize` | bool | `true` | 是否用 LLM 整理 |
| `llm_model` | string | `""` | LLM 模型 ID（留空用默认） |
| `dedup_ttl_days` | int | `7` | 去重保留天数 |
| `timeout` | int | `30` | 请求超时（秒） |

## 可用分类

| 分类 | emoji | 说明 |
|------|-------|------|
| `AI/人工智能` | 🤖 | AI 大模型、技术突破、行业动态 |
| `科技圈/GitHub热门` | 💻 | GitHub Trending 热门仓库 |
| `游戏开发` | 🎮 | 游戏引擎更新、开发工具 |
| `二次元/游戏` | 🌟 | ACG 二次元、游戏发行 |
| `时事热点` | 📰 | 今日热点、社会新闻 |
| `科技数码` | 📱 | 手机、电脑、硬件、数码产品 |

## 使用

| 命令 | 说明 |
|------|------|
| `/新闻` | 手动获取简报（默认分类） |
| `/新闻状态` | 查看运行状态和每个目标的分类 |

管理员命令：

| 命令 | 说明 |
|------|------|
| `/新闻管理 push` | 手动推送给所有目标 |
| `/新闻管理 status` | 查看详细状态 |

## 配置示例

```json
{
  "groups": [
    "napcat:FriendMessage:3248569738:AI/人工智能,科技圈/GitHub热门,游戏开发",
    "napcat:GroupMessage:984686890:二次元/游戏,时事热点"
  ],
  "push_time": "08:00",
  "search_provider": "bocha",
  "search_api_key": "你的APIKey"
}
```

这样配置后：
- 每天早上 8 点，你的大号收到 AI + 科技 + 游戏的简报
- 群 984686890 收到二次元 + 时事的简报
- 两个目标互不干扰

## 去重

插件会记录每次推送的新闻 URL，同一条新闻不会重复推送给同一个人或群。

- 缓存文件：`data/plugins/astrbot_plugin_news_collector/_seen_news.json`
- 默认保留 7 天，可通过 `dedup_ttl_days` 配置
- 删除该文件可重置去重缓存

## License

AGPL-3.0
