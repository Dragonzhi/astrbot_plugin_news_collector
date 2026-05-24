# astrbot_plugin_news_collector

米游社 COS 收图插件。从米游社 BBS 拉取各游戏 COS 及同人帖子，以文字+图片混合消息形式推送。

> **平台声明**：本插件主要针对 **OneBot 协议（QQ 平台）** 优化，消息格式采用 QQ 友好的 emoji 排版与混合消息（文本+图片）。其他平台可能表现不一致。

## 功能

- **米游社 COS 收图**：拉取原神、崩坏：星穹铁道、绝区零、崩坏3 等游戏的 COS / 同人帖子
- **文字+图片混合消息**：每条帖子包含标题、摘要、链接和封面图
- **按目标个性化**：不同目标可指定不同游戏
- **LLM 整理**：用 LLM 对帖子进行摘要，生成更精炼的简报
- **自动去重**：记录帖子链接，同一条不重复推送
- **QQ 友好格式**：emoji 排版，支持链接点击跳转

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Dragonzhi/astrbot_plugin_news_collector.git
```

重启 AstrBot。

## 配置

### 推送目标

```
napcat:FriendMessage:你的QQ:原神,崩坏：星穹铁道
napcat:GroupMessage:群号:原神,崩坏3
```

### 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `groups` | list | `[]` | 推送目标列表 |
| `categories` | list | 原神,星铁,绝区零 | 默认游戏列表 |
| `push_time` | string | `08:00` | 推送时间 HH:MM |
| `enable_llm_organize` | bool | `true` | 是否用 LLM 整理简报 |
| `llm_model` | string | `""` | LLM 模型 ID（留空使用默认） |
| `dedup_ttl_days` | int | `7` | 去重保留天数 |
| `enable_image_limit` | bool | `true` | 是否限制图片数量 |
| `max_images` | int | `1` | 每条简报最大图片数（0=无限制）|

### 可用游戏

| 分类 | emoji | forum_id | 说明 |
|------|-------|----------|------|
| 原神 | 🌪️ | 49 | COS 区 |
| 崩坏：星穹铁道 | 🚂 | 62 | COS 区 |
| 绝区零 | ⚡ | 65 | COS 区 |
| 崩坏3 | 💥 | 4 | 同人图（含 COS） |

## 使用

| 命令 | 说明 |
|------|------|
| `/米游社` | 手动拉取并生成简报 |
| `/米游社状态` | 查看插件状态 |
| `/米游社诊断` | 检查各游戏 API 状态 |

管理员命令：

| 命令 | 说明 |
|------|------|
| `/米游社管理 push` | 手动推送给所有目标 |
| `/米游社管理 status` | 查看详细状态 |

## 数据来源

- API：`bbs-api.miyoushe.com/post/wapi/getForumPostList`
- 无需 API Key，无需登录
- 拉取各游戏论坛最新帖子，筛选带图片的

## 注意事项

- 消息格式为 `MessageChain([Plain(文字), Image(封面), ...])` 混合消息
- 图片通过 URL 直接嵌入，无需下载到本地
- 去重缓存文件：`_seen_news.json`

## License

AGPL-3.0
