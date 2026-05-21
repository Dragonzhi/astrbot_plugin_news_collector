"""
AstrBot 每日新闻收集 + LLM 智能整理插件

每天定时从多个来源收集新闻（AI/科技/游戏开发），
使用 LLM 整理成结构化简报，推送到指定群组或用户。
"""

import asyncio
import datetime
import json
import os
import traceback
from typing import Any, List, Dict, Tuple
import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderType
from astrbot.core.message.message_event_result import MessageChain


# ========== 新闻来源 API 配置 ==========
NEWS_API_60S = "https://api.nycnm.cn/API/60s.php"          # 每日60s新闻
NEWS_API_AI = "https://api.nycnm.cn/API/aizixun.php"       # AI资讯
NEWS_API_HISTORY = "https://api.nycnm.cn/API/history.php"  # 历史今日
GITHUB_TRENDING_API = "https://api.oioweb.cn/api/github/trending"  # GitHub trending

# LLM 整理新闻的系统提示词
SYSTEM_PROMPT = """你是一个专业新闻整理助手。请根据以下收集到的原始新闻素材，整理成结构清晰的每日简报。

要求：
1. 按分类整理（AI/人工智能、科技圈/GitHub热门、游戏开发）
2. 每条新闻包含标题、摘要、来源和链接
3. 使用中文，语言简洁有力
4. 去掉重复或过时的信息
5. 链接保留原始URL
6. 格式要求：
   ## 分类名称
   ### 1. 新闻标题
   新闻摘要
   - 来源：xxx
   - 链接：xxx

如果某个分类没有新闻，跳过该分类，不要写"暂无"。
整体保持信息密度高，对技术/游戏开发者有参考价值。"""


@register(
    "astrbot_plugin_news_collector",
    "Hanako",
    "每日新闻收集 + LLM 智能整理。每天定时从多个来源收集新闻（AI/科技/游戏开发），使用 LLM 整理成结构化简报并推送。",
    "1.0.0",
)
class NewsCollectorPlugin(Star):
    """每日新闻收集 + LLM 整理插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 推送配置
        self.groups = getattr(self.config, "groups", [])
        self.push_time = getattr(self.config, "push_time", "08:00")

        # 分类启用开关
        self.enable_ai = getattr(self.config, "enable_ai", True)
        self.enable_tech = getattr(self.config, "enable_tech", True)
        self.enable_game = getattr(self.config, "enable_game", True)

        # LLM 配置
        self.enable_llm = getattr(self.config, "enable_llm_organize", True)
        self.llm_model = getattr(self.config, "llm_model", "")

        # 请求配置
        self.timeout = getattr(self.config, "timeout", 30)

        self.api_key = getattr(self.config, "api_key", "")

        logger.info(f"[新闻收集] 插件已加载: 推送时间={self.push_time}, "
                     f"LLM整理={'开' if self.enable_llm else '关'}, "
                     f"分类=[{'AI ' if self.enable_ai else ''}{'科技 ' if self.enable_tech else ''}{'游戏' if self.enable_game else ''}]")

        # 启动定时任务
        self._task = asyncio.create_task(self._daily_task())

    # ======================== 定时任务 ========================

    async def _daily_task(self):
        """定时任务主循环"""
        while True:
            try:
                sleep_seconds = self._calc_sleep()
                logger.info(f"[新闻收集] 距离下次推送还有 {sleep_seconds / 3600:.1f} 小时")
                await asyncio.sleep(sleep_seconds)

                if not self.groups:
                    logger.warning("[新闻收集] 未配置推送目标，跳过本次推送")
                    continue

                logger.info("[新闻收集] 开始收集新闻...")
                report = await self._build_report()
                if not report:
                    logger.warning("[新闻收集] 新闻简报为空，跳过推送")
                    continue

                for target in self.groups:
                    try:
                        mc = MessageChain().message(report)
                        await self.context.send_message(target, mc)
                        logger.info(f"[新闻收集] 已推送到 {target}")
                        await asyncio.sleep(2)
                    except Exception as e:
                        logger.error(f"[新闻收集] 推送失败 {target}: {e}")

                await asyncio.sleep(60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[新闻收集] 定时任务出错: {e}")
                traceback.print_exc()
                await asyncio.sleep(300)

    def _calc_sleep(self) -> float:
        """计算距离下次推送的秒数"""
        now = datetime.datetime.now()
        time_strs = self.push_time.replace("，", ",").split(",")
        candidates = []

        for t_str in time_strs:
            parts = t_str.strip().split(":")
            if len(parts) != 2:
                continue
            try:
                h, m = map(int, parts)
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target <= now:
                    target += datetime.timedelta(days=1)
                candidates.append(target)
            except ValueError:
                continue

        if not candidates:
            target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            candidates.append(target)

        next_push = min(candidates)
        return (next_push - now).total_seconds()

    # ======================== 新闻简报生成 ========================

    async def _build_report(self) -> str:
        """构建完整的新闻简报"""
        now = datetime.datetime.now()
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        date_str = now.strftime("%Y-%m-%d")

        # 1. 收集原始新闻
        raw_news = {}
        tasks = []

        if self.enable_ai:
            tasks.append(self._fetch_60s_news())
            tasks.append(self._fetch_ai_news())

        if self.enable_tech:
            tasks.append(self._fetch_github_trending())

        if self.enable_game:
            tasks.append(self._fetch_game_news())

        if not tasks:
            return ""

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 2. 按分类整理
        idx = 0
        if self.enable_ai:
            raw_news["AI/人工智能"] = results[idx] if not isinstance(results[idx], Exception) else []
            idx += 1
            raw_news["AI资讯"] = results[idx] if not isinstance(results[idx], Exception) else []
            idx += 1
        if self.enable_tech:
            raw_news["科技圈/GitHub热门"] = results[idx] if not isinstance(results[idx], Exception) else []
            idx += 1
        if self.enable_game:
            raw_news["游戏开发"] = results[idx] if not isinstance(results[idx], Exception) else []
            idx += 1

        # 过滤空分类
        raw_news = {k: v for k, v in raw_news.items() if v}

        if not raw_news:
            return f"每日新闻简报 — {date_str}（{weekday}）\n\n暂无新闻数据，请检查网络连接或新闻源是否可用。"

        # 3. 用LLM整理
        if self.enable_llm:
            try:
                organized = await self._organize_with_llm(raw_news)
                report = f"每日新闻简报 — {date_str}（{weekday}）\n\n{organized}"
                report += f"\n\n> 简报由 LLM 自动整理，内容截至 {now.strftime('%Y-%m-%d %H:%M')}"
                return report
            except Exception as e:
                logger.error(f"[新闻收集] LLM整理失败: {e}")

        # 4. 不用LLM，直接输出
        report = f"每日新闻简报 — {date_str}（{weekday}）\n\n"
        for category, items in raw_news.items():
            report += f"## {category}\n\n"
            for i, item in enumerate(items, 1):
                report += f"### {i}. {item.get('title', '未命名')}\n"
                report += f"{item.get('summary', '')}\n"
                if item.get("source"):
                    report += f"- 来源：{item['source']}\n"
                if item.get("url"):
                    report += f"- 链接：{item['url']}\n"
                report += "\n"
        report += f"> 简报由新闻收集机器人自动生成，内容截至 {now.strftime('%Y-%m-%d %H:%M')}"
        return report

    async def _organize_with_llm(self, raw_news: Dict[str, List[Dict]]) -> str:
        """使用 AstrBot 的 LLM 提供商整理新闻"""
        provider = self.context.provider_manager.get_using_provider(ProviderType.CHAT_COMPLETION)
        if not provider:
            raise Exception("没有可用的 LLM 提供商")

        # 构建新闻素材文本
        news_text = ""
        for category, items in raw_news.items():
            news_text += f"## {category}\n"
            for item in items:
                title = item.get("title", "无标题")
                summary = item.get("summary", "")
                source = item.get("source", "")
                url = item.get("url", "")
                news_text += f"- {title}"
                if summary:
                    news_text += f": {summary}"
                news_text += "\n"
                if source:
                    news_text += f"  来源：{source}\n"
                if url:
                    news_text += f"  链接：{url}\n"
            news_text += "\n"

        model = self.llm_model if self.llm_model else None
        resp = await provider.text_chat(
            prompt=f"请整理以下新闻素材：\n\n{news_text}",
            system_prompt=SYSTEM_PROMPT,
            model=model,
        )
        return resp.completion_text.strip()

    # ======================== 新闻源抓取 ========================

    async def _fetch_60s_news(self) -> List[Dict]:
        """获取每日60s综合新闻"""
        return await self._fetch_json_api(
            name="60s新闻",
            url=f"{NEWS_API_60S}?format=json",
            parser=self._parse_60s_news,
        )

    async def _fetch_ai_news(self) -> List[Dict]:
        """获取AI资讯"""
        return await self._fetch_json_api(
            name="AI资讯",
            url=f"{NEWS_API_AI}?format=json",
            parser=self._parse_ai_news,
        )

    async def _fetch_github_trending(self) -> List[Dict]:
        """获取GitHub Trending"""
        # 尝试多个 trending 源
        github_parsers = [
            (f"{GITHUB_TRENDING_API}", self._parse_github_trending_v1),
        ]

        for url, parser in github_parsers:
            try:
                return await self._fetch_json_api(
                    name="GitHub Trending",
                    url=url,
                    parser=parser,
                )
            except Exception:
                continue

        # 兜底：返回空
        return []

    async def _fetch_game_news(self) -> List[Dict]:
        """获取游戏开发相关新闻"""
        # 使用通用新闻 API 加上关键词筛选
        news = await self._fetch_60s_news()
        game_keywords = ["游戏", "Godot", "Unreal", "Unity", "引擎", "Game", "开发"]
        filtered = []
        for item in news:
            title = item.get("title", "")
            summary = item.get("summary", "")
            if any(kw.lower() in (title + summary).lower() for kw in game_keywords):
                filtered.append(item)

        if not filtered:
            # 返回几条通用的游戏开发相关信息
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            filtered = [
                {
                    "title": "Unreal Engine / Godot / Unity 引擎动态",
                    "summary": "各主流游戏引擎持续迭代中，建议关注官方更新日志获取最新特性。",
                    "source": "综合",
                    "url": "https://www.unrealengine.com/",
                },
                {
                    "title": f"{today} 游戏开发社区动态",
                    "summary": "今日暂无特定游戏开发新闻。可通过 GitHub Trending 或 GodotHub 社区获取最新动态。",
                    "source": "综合",
                    "url": "https://godothub.com/",
                },
            ]

        return filtered

    async def _fetch_json_api(self, name: str, url: str, parser) -> List[Dict]:
        """通用 JSON API 抓取模板"""
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=self.timeout) as resp:
                        if resp.status != 200:
                            raise Exception(f"HTTP {resp.status}")
                        data = await resp.json(content_type=None)
                        return parser(data)
            except Exception as e:
                logger.warning(f"[新闻收集] {name} 请求失败 ({attempt+1}/3): {e}")
                if attempt == 2:
                    raise
                await asyncio.sleep(1)

    def _parse_60s_news(self, data: dict) -> List[Dict]:
        """解析60s新闻返回"""
        results = []
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        news_list = payload.get("news", [])
        tip = payload.get("tip", "")

        for item in news_list:
            results.append({
                "title": item if isinstance(item, str) else str(item),
                "summary": "",
                "source": "每日60s",
                "url": "",
            })
        if tip:
            results.append({
                "title": "每日提示",
                "summary": tip,
                "source": "每日60s",
                "url": "",
            })
        return results

    def _parse_ai_news(self, data: dict) -> List[Dict]:
        """解析AI资讯返回"""
        results = []
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        news_list = payload.get("news", [])

        for item in news_list:
            if isinstance(item, dict):
                results.append({
                    "title": item.get("title", ""),
                    "summary": item.get("description", item.get("content", "")),
                    "source": item.get("source", "AI资讯"),
                    "url": item.get("url", item.get("link", "")),
                })
            elif isinstance(item, str):
                results.append({
                    "title": item,
                    "summary": "",
                    "source": "AI资讯",
                    "url": "",
                })
        return results

    def _parse_github_trending_v1(self, data: dict) -> List[Dict]:
        """解析 GitHub Trending API"""
        results = []
        if isinstance(data, dict):
            repos = data.get("data", [])
            if isinstance(repos, list):
                for repo in repos[:10]:
                    if isinstance(repo, dict):
                        name = repo.get("name", repo.get("full_name", ""))
                        desc = repo.get("description", repo.get("desc", ""))
                        url = repo.get("url", repo.get("html_url", ""))
                        stars = repo.get("stars", repo.get("stargazers_count", ""))
                        results.append({
                            "title": name,
                            "summary": desc + (f" ⭐{stars}" if stars else ""),
                            "source": "GitHub Trending",
                            "url": url or f"https://github.com/{name}",
                        })
                    elif isinstance(repo, str):
                        results.append({
                            "title": repo,
                            "summary": "",
                            "source": "GitHub Trending",
                            "url": f"https://github.com/{repo}",
                        })
            elif isinstance(repos, str):
                # 某些 API 返回纯文本列表
                lines = repos.strip().split("\n")
                for line in lines[:10]:
                    results.append({
                        "title": line.strip(),
                        "summary": "",
                        "source": "GitHub Trending",
                        "url": "",
                    })
        return results

    # ======================== 用户命令 ========================

    @filter.command("新闻")
    async def cmd_news(self, event: AstrMessageEvent):
        """手动获取今日新闻简报"""
        try:
            yield event.plain_result("正在收集新闻并整理，请稍候...")
            report = await self._build_report()
            if report:
                yield event.plain_result(report)
            else:
                yield event.plain_result("新闻收集失败，请稍后重试。")
        except Exception as e:
            yield event.plain_result(f"新闻收集失败: {e}")

    @filter.command("新闻状态")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看插件状态"""
        sleep_seconds = self._calc_sleep()
        hours = int(sleep_seconds / 3600)
        minutes = int((sleep_seconds % 3600) / 60)

        status = (
            f"📰 新闻收集插件运行中\n"
            f"推送时间: {self.push_time}\n"
            f"推送目标: {len(self.groups)} 个\n"
            f"LLM整理: {'开' if self.enable_llm else '关'}\n"
            f"新闻分类: "
            f"{'AI ' if self.enable_ai else ''}"
            f"{'科技 ' if self.enable_tech else ''}"
            f"{'游戏' if self.enable_game else ''}\n"
            f"距离下次推送: {hours}小时{minutes}分钟"
        )
        yield event.plain_result(status)

    # ======================== 管理员命令 ========================

    @filter.command_group("新闻管理")
    def news_admin(self):
        """新闻管理命令组"""
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @news_admin.command("push")
    async def admin_push(self, event: AstrMessageEvent):
        """手动推送新闻到所有目标"""
        if not self.groups:
            yield event.plain_result("未配置推送目标，请在插件配置中设置 groups。")
            return

        yield event.plain_result("正在收集新闻并推送...")
        try:
            report = await self._build_report()
            if report:
                for target in self.groups:
                    mc = MessageChain().message(report)
                    await self.context.send_message(target, mc)
                    await asyncio.sleep(2)
                yield event.plain_result(f"已推送到 {len(self.groups)} 个目标")
            else:
                yield event.plain_result("新闻简报为空，未推送")
        except Exception as e:
            yield event.plain_result(f"推送失败: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @news_admin.command("status")
    async def admin_status(self, event: AstrMessageEvent):
        """查看插件详细状态"""
        await self.cmd_status(event)

    # ======================== 生命周期 ========================

    async def terminate(self):
        """插件卸载时清理定时任务"""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[新闻收集] 插件已卸载")
