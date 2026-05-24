# astrbot_plugin_news_collector

米游社 COS 收图插件。从米游社 BBS 收集各游戏 COS/同人图片，定时推送到指定目标。

## 功能

- **收图**：从米游社各游戏论坛拉取带图片的帖子，自动下载并发送
- **多游戏**：支持原神、崩坏：星穹铁道、绝区零、崩坏3
- **按目标个性化**：不同目标可指定不同游戏
- **智能排序**：图片多的帖子优先
- **QQ 格式**：文字标题 + 逐张图片直接发送

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

| 分类 | emoji | forum_id |
|------|-------|----------|
| 原神 | 🌪️ | 49 |
| 崩坏：星穹铁道 | 🚂 | 57 |
| 绝区零 | ⚡ | 61 |
| 崩坏3 | 💥 | 31 |

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
