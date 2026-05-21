"""
AstrBot 每日新闻收集 + LLM 智能整理插件

每天定时收集新闻（AI/科技/GitHub/游戏开发），
使用 LLM + 联网搜索 整理成结构化简报，推送到指定目标。
"""

import asyncio
import datetime
import json
import traceback
from typing import Any, List, Dict, Optional

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderType
from astrbot.core.message.message_event_result import MessageChain


# ========== 公开 API ==========
GITHUB_SEARCH_API = "https://api.github.com/search/repositories"


# ========== 搜索 API 配置 ==========
SEARCH_PROVIDERS = {
    "bocha": {
        "url": "https://api.bochaai.com/v1/web-search",
        "method": "POST",
        "headers": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
        "payload": lambda query, count: {
            "query": query,
            "count": count,
            "summary": True,
        },
        "parser": lambda data: [
            {
                "title": item.get("name", ""),
                "snippet": item.get("snippet", ""),
                "url": item.get("url", ""),
            }
            for item in (data.get("data", {}).get("webPages", {}).get("value", []))
        ],
    },
    "tavily": {
        "url": "https://api.tavily.com/search",
        "method": "POST",
        "headers": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        "payload": lambda query, count: {
            "query": query,
            "max_results": count,
            "search_depth": "basic",
        },
        "parser": lambda data: [
            {
                "title": item.get("title", ""),
                "snippet": item.get("content", ""),
                "url": item.get("url", ""),
            }
            for item in data.get("results", [])
        ],
    },
    "brave": {
        "url": "https://api.search.brave.com/res/v1/web/search",
        "method": "GET",
        "headers": lambda key: {
            "Accept": "application/json",
            "X-Subscription-Token": key,
        },
        "payload": lambda query, count: {
            "q": query,
            "count": count,
        },
        "parser": lambda data: [
            {
                "title": item.get("title", ""),
                "snippet": item.get("description", ""),
                "url": item.get("url", ""),
            }
            for item in (data.get("web", {}).get("results", []))
        ],
    },
}


# ========== 新闻收集搜索词 ==========
SEARCH_QUERIES = {
    "AI/人工智能": [
        "2026年 AI 人工智能 大模型 最新进展",
        "2026 AI artificial intelligence news latest",
    ],
    "科技圈/GitHub热门": [
        "2026年 科技 开发者 工具 开源 最新动态",
        "2026 technology developer news trending",
    ],
    "游戏开发": [
        "2026年 游戏开发 引擎 Unreal Godot Unity 更新",
        "2026 game development engine update news",
    ],
}


@register(
    "astrbot_plugin_news_collector",
    "Hanako",
    "每日新闻收集 + LLM + 联网搜索。联网搜索最新新闻（AI/科技/游戏开发），LLM 整理成简报推送。",
    "1.1.0",
)
class NewsCollectorPlugin(Star):
    """每日新闻收集 + LLM 整理插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.groups = getattr(self.config, "groups", [])
        self.push_time = getattr(self.config, "push_time", "08:00")

        # 联网搜索配置
        self.search_provider = getattr(self.config, "search_provider", "")
        self.search_api_key = getattr(self.config, "search_api_key", "")
        self.search_count = getattr(self.config, "search_count", 5)

        # LLM 配置
        self.enable_llm = getattr(self.config, "enable_llm_organize", True)
        self.llm_model = getattr(self.config, "llm_model", "")
        self.timeout = getattr(self.config, "timeout", 30)

        logger.info(
            f"[新闻收集] 插件已加载: 推送时间={self.push_time}, "
            f"联网搜索={'开(' + self.search_provider + ')' if self.search_api_key else '关'}, "
            f"LLM整理={'开' if self.enable_llm else '关'}"
        )
        self._task = asyncio.create_task(self._daily_task())

    # ======================== 状态 ========================

    def _gen_status_text(self) -> str:
        sec = self._calc_sleep()
        h, m = int(sec / 3600), int((sec % 3600) / 60)
        search_status = f"{self.search_provider}" if self.search_api_key else "未配置"
        return (
            f"新闻收集插件运行中\n"
            f"推送时间: {self.push_time}\n"
            f"推送目标: {len(self.groups)} 个\n"
            f"联网搜索: {search_status}\n"
            f"LLM整理: {'开' if self.enable_llm else '关'}\n"
            f"距离下次推送: {h}小时{m}分钟"
        )

    # ======================== 定时任务 ========================

    async def _daily_task(self):
        while True:
            try:
                sec = self._calc_sleep()
                logger.info(f"[新闻收集] 距离下次推送还有 {sec / 3600:.1f} 小时")
                await asyncio.sleep(sec)
                if not self.groups:
                    logger.warning("[新闻收集] 未配置推送目标，跳过本次推送")
                    continue
                report = await self._build_report()
                if not report:
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
        now = datetime.datetime.now()
        candidates = []
        for t_str in self.push_time.replace("，", ",").split(","):
            parts = t_str.strip().split(":")
            if len(parts) != 2:
                continue
            try:
                h, m = map(int, parts)
                t = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if t <= now:
                    t += datetime.timedelta(days=1)
                candidates.append(t)
            except ValueError:
                continue
        if not candidates:
            t = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if t <= now:
                t += datetime.timedelta(days=1)
            candidates.append(t)
        return (min(candidates) - now).total_seconds()

    # ======================== 联网搜索 ========================

    async def _web_search(self, query: str, count: int = 5) -> List[Dict]:
        """通过配置的搜索引擎搜索网络"""
        if not self.search_api_key or self.search_provider not in SEARCH_PROVIDERS:
            return []

        provider = SEARCH_PROVIDERS[self.search_provider]
        headers = provider["headers"](self.search_api_key)
        payload_or_params = provider["payload"](query, count)
        method = provider["method"]
        url = provider["url"]

        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(verify_ssl=False),
            ) as session:
                if method == "POST":
                    async with session.post(
                        url, json=payload_or_params, headers=headers,
                        timeout=self.timeout
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(f"[搜索] {self.search_provider} 返回 {resp.status}")
                            return []
                        data = await resp.json()
                else:  # GET
                    async with session.get(
                        url, params=payload_or_params, headers=headers,
                        timeout=self.timeout
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(f"[搜索] {self.search_provider} 返回 {resp.status}")
                            return []
                        data = await resp.json()

                results = provider["parser"](data)
                logger.info(f"[搜索] \"{query[:30]}...\" 返回 {len(results)} 条结果")
                return results

        except asyncio.TimeoutError:
            logger.warning(f"[搜索] {self.search_provider} 超时")
            return []
        except Exception as e:
            logger.warning(f"[搜索] {self.search_provider} 失败: {e}")
            return []

    async def _search_category(self, category: str, count: int = 5) -> List[Dict]:
        """搜索某个分类的新闻"""
        queries = SEARCH_QUERIES.get(category, [])
        all_results = []
        seen_urls = set()

        for query in queries:
            results = await self._web_search(query, max(count, 3))
            for r in results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
            if len(all_results) >= count:
                break

        return all_results[:count]

    # ======================== 简报构建 ========================

    async def _build_report(self) -> str:
        now = datetime.datetime.now()
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        date_str = now.strftime("%Y-%m-%d")

        # 1. GitHub Trending（实时）
        github_items = await self._fetch_github_trending()

        # 2. 联网搜索各分类新闻
        search_results = {}  # {category: [items]}
        if self.search_api_key:
            categories = ["AI/人工智能", "科技圈/GitHub热门", "游戏开发"]
            tasks = {
                cat: self._search_category(cat, self.search_count)
                for cat in categories
            }
            for cat, coro in tasks.items():
                try:
                    items = await coro
                    if items:
                        search_results[cat] = items
                except Exception as e:
                    logger.warning(f"[新闻收集] 搜索 {cat} 失败: {e}")

        # 3. 传给 LLM 生成简报
        if self.enable_llm:
            try:
                report = await self._generate_report_via_llm(
                    date_str, weekday_cn, github_items, search_results
                )
                report += f"\n\n> 简报由 LLM + 联网搜索生成，内容截至 {now.strftime('%Y-%m-%d %H:%M')}"
                return report
            except Exception as e:
                logger.error(f"[新闻收集] LLM 生成简报失败: {e}")

        # 4. 兜底
        return self._fallback_report(date_str, weekday_cn, github_items, search_results)

    async def _generate_report_via_llm(
        self,
        date_str: str,
        weekday_cn: str,
        github_items: List[Dict],
        search_results: Dict[str, List[Dict]],
    ) -> str:
        provider = self.context.provider_manager.get_using_provider(
            ProviderType.CHAT_COMPLETION
        )
        if not provider:
            raise Exception("没有可用的 LLM 提供商")

        # 构建用户消息 - 把所有搜索数据和 GitHub 数据塞进去
        user_parts = [
            f"今天是 {date_str}（{weekday_cn}）。请根据以下数据写一份每日新闻简报。"
        ]

        # GitHub 数据
        if github_items:
            user_parts.append("\n\n[GitHub Trending 实时数据]")
            for item in github_items:
                user_parts.append(
                    f"- {item.get('title', '')}: {item.get('summary', '')}"
                )
                if item.get("url"):
                    user_parts.append(f"  URL: {item['url']}")

        # 联网搜索结果
        for category, items in search_results.items():
            user_parts.append(f"\n\n[联网搜索 - {category}]")
            for item in items:
                user_parts.append(f"- {item.get('title', '')}: {item.get('snippet', '')}")
                if item.get("url"):
                    user_parts.append(f"  URL: {item['url']}")

        system_prompt = f"""今天是 {date_str}（{weekday_cn}）。

你是一个专业新闻整理助手。根据上面提供的「GitHub Trending 实时数据」和「联网搜索结果」，生成一份每日新闻简报。

【核心要求】
1. 只使用上面提供的数据，不要编造信息
2. 按以下分类组织（没数据的分类就跳过）：
   - AI/人工智能
   - 科技圈/GitHub热门
   - 游戏开发
3. 保留每条新闻的来源 URL，方便用户点击

【输出格式 - QQ 消息友好】
不要用 Markdown，用 emoji + 符号排版：

📂 AI/人工智能

🔹 标题
摘要内容...
📎 来源：xxx
🔗 https://xxx

📂 科技圈/GitHub热门

🔹 项目名
项目描述...
📎 来源：GitHub Trending
🔗 https://github.com/xxx

注意：每条新闻都要有 🔗 链接，用户需要点击查看详情。"""

        model = self.llm_model if self.llm_model else None
        resp = await provider.text_chat(
            prompt="\n".join(user_parts),
            system_prompt=system_prompt,
            model=model,
        )
        return (
            f"每日新闻简报 — {date_str}（{weekday_cn}）\n\n"
            + resp.completion_text.strip()
        )

    def _fallback_report(
        self,
        date_str: str,
        weekday_cn: str,
        github_items: List[Dict],
        search_results: Dict[str, List[Dict]],
    ) -> str:
        """LLM 不可用时的兜底输出"""
        report = f"每日新闻简报 — {date_str}（{weekday_cn}）\n\n"

        # 联网搜索结果
        for category, items in search_results.items():
            report += f"📂 {category}\n\n"
            for i, item in enumerate(items[:5], 1):
                report += f"🔹 {item.get('title', '')}\n"
                if item.get("snippet"):
                    report += f"{item['snippet']}\n"
                if item.get("url"):
                    report += f"🔗 {item['url']}\n"
                report += "\n"

        # GitHub 数据
        if github_items:
            report += "📂 科技圈/GitHub热门\n\n"
            for item in github_items[:5]:
                report += f"🔹 {item.get('title', '')}\n"
                if item.get("summary"):
                    report += f"{item['summary']}\n"
                if item.get("url"):
                    report += f"🔗 {item['url']}\n"
                report += "\n"

        if not search_results and not github_items:
            report += "暂无新闻数据，请检查网络连接。\n"

        return report

    # ======================== GitHub Trending ========================

    async def _fetch_github_trending(self) -> List[Dict]:
        days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime(
            "%Y-%m-%d"
        )
        url = f"{GITHUB_SEARCH_API}?q=created:%3E{days_ago}&sort=stars&order=desc&per_page=8"
        headers = {"Accept": "application/vnd.github.v3+json"}
        try:
            async with aiohttp.ClientSession(
                headers=headers,
                connector=aiohttp.TCPConnector(verify_ssl=False),
            ) as session:
                async with session.get(url, timeout=self.timeout) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    results = []
                    for repo in data.get("items", []):
                        name = repo.get("full_name", "")
                        desc = repo.get("description") or ""
                        stars = repo.get("stargazers_count", 0)
                        lang = repo.get("language") or ""
                        url = repo.get("html_url", "")
                        summary = desc
                        if lang:
                            summary += f" [{lang}]"
                        summary += f" ⭐{stars}"
                        results.append({
                            "title": name,
                            "summary": summary,
                            "source": "GitHub Trending",
                            "url": url,
                        })
                    return results
        except Exception as e:
            logger.warning(f"[新闻收集] GitHub API 请求失败: {e}")
            return []

    # ======================== 用户命令 ========================

    @filter.command("新闻")
    async def cmd_news(self, event: AstrMessageEvent):
        try:
            yield event.plain_result("正在联网搜索新闻并整理，请稍候...")
            report = await self._build_report()
            yield event.plain_result(report or "新闻收集失败，请稍后重试。")
        except Exception as e:
            yield event.plain_result(f"新闻收集失败: {e}")
            logger.error(traceback.format_exc())

    @filter.command("新闻状态")
    async def cmd_status(self, event: AstrMessageEvent):
        yield event.plain_result(self._gen_status_text())

    # ======================== 管理员命令 ========================

    @filter.command_group("新闻管理")
    def news_admin(self):
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @news_admin.command("push")
    async def admin_push(self, event: AstrMessageEvent):
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
        yield event.plain_result(self._gen_status_text())

    # ======================== 生命周期 ========================

    async def terminate(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[新闻收集] 插件已卸载")
