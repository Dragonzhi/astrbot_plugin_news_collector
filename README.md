# astrbot_plugin_news_collector

米游社 COS 帖子收集插件。从米游社 BBS 拉取各游戏 COS 及同人相关帖子，按目标个性化推送。

> **平台声明**：本插件主要针对 **OneBot 协议（QQ 平台）** 优化，消息格式采用 QQ 友好的 emoji 排版与混合消息（文本+图片）。其他平台可能表现不一致。

## 功能

- **米游社 COS 帖子**：拉取原神、崩坏：星穹铁道、绝区零、崩坏3 等游戏的 COS / 同人相关帖子
- **按目标个性化**：不同目标可指定不同游戏，比如大号收原神+星铁，群收绝区零
- **LLM 整理**：用 LLM 对帖子进行摘要，生成更精炼的简报
- **自动去重**：记录帖子 ID，同一条不重复推送
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
| `/收图` | 手动收图发到当前对话 |
| `/收图状态` | 查看插件状态 |
| `/收图诊断` | 检查各游戏能拉到多少帖子多少图 |

管理员命令：

| 命令 | 说明 |
|------|------|
| `/收图管理 push` | 手动推送给所有目标 |
| `/收图管理 status` | 查看详细状态 |

## 数据来源

- API：`bbs-api.miyoushe.com/post/wapi/getForumPostList`
- 无需 API Key，无需登录
- 拉取各游戏论坛最新帖子，筛选带图片的

## 注意事项

- 每次最多推送 9 张图（QQ 单次消息上限）
- 图片下载到临时目录，发送后自动清理
- `_seen_news.json` 缓存文件已弃用，可删除

## License

AGPL-3.0
