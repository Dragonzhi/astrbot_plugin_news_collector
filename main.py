"""
AstrBot 每日新闻收集 + LLM 智能整理插件

每天定时收集新闻（AI/科技/GitHub/游戏开发），
使用 LLM 整理成结构化简报，推送到指定目标。
"""

import asyncio
import datetime
import traceback
from typing import Any, List, Dict, Tuple

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderType
from astrbot.core.message.message_event_result import MessageChain


# ========== 确认可用的公开 API ==========
GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
HN_TOP_STORIES = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"

# ========== 分组来源 ==========
# 每个分组对应简报中的一个章节。source 可以是：
#   "live"    → 从 API 拉取实时数据（items 会被传给 LLM 一起整理）
#   "llm"     → 靠 LLM 自身的知识生成内容（仅在 llm_prompt 里描述主题）
#   "hybrid"  → 同时使用实时数据 + LLM 补充
SECTION_CONFIG = {
    "AI/人工智能": {
        "source": "llm",
        "llm_prompt": "提供近期AI领域的重要新闻，包括大模型发布、公司战略变化、开源项目、技术突破等。每条给出标题、摘要、来源（如果知道）和链接（如果知道）。至少3条，最多5条。"
    },
    "科技圈/GitHub热门": {
        "source": "live",
        "api": "github_trending",
    },
    "游戏开发": {
        "source": "llm",
        "llm_prompt": "提供近期游戏开发领域的重要新闻，包括引擎（Unreal/Godot/Unity）更新、开发工具发布、行业新动向等。每条给出标题、摘要、来源。至少2条，最多4条。"
    },
}


@register(
    "astrbot_plugin_news_collector",
    "Hanako",
    "每日新闻收集 + LLM 智能整理。每天定时从多个来源收集新闻（AI/科技/游戏开发），使用 LLM 整理成结构化简报并推送。",
    "1.0.1",
)
class NewsCollectorPlugin(Star):
    """每日新闻收集 + LLM 整理插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.groups = getattr(self.config, "groups", [])
        self.push_time = getattr(self.config, "push_time", "08:00")
        self.enable_llm = getattr(self.config, "enable_llm_organize", True)
        self.llm_model = getattr(self.config, "llm_model", "")
        self.timeout = getattr(self.config, "timeout", 30)

        logger.info(
            f"[新闻收集] 插件已加载: 推送时间={self.push_time}, "
            f"LLM整理={'开' if self.enable_llm else '关'}"
        )
        self._task = asyncio.create_task(self._daily_task())

    # ======================== 对外方法：供 terminal 和命令共用 ========================

    def _gen_status_text(self) -> str:
        """生成统一的状态文本（非 async generator，可被任何上下文调用）"""
        sleep_seconds = self._calc_sleep()
        hours = int(sleep_seconds / 3600)
        minutes = int((sleep_seconds % 3600) / 60)
        sections = ", ".join(
            k for k, v in SECTION_CONFIG.items()
            if getattr(self.config, f"enable_{k.split('/')[0].lower()}", True)
        )
        return (
            f"新闻收集插件运行中\n"
            f"推送时间: {self.push_time}\n"
            f"推送目标: {len(self.groups)} 个\n"
            f"LLM整理: {'开' if self.enable_llm else '关'}\n"
            f"新闻分类: {sections}\n"
            f"距离下次推送: {hours}小时{minutes}分钟"
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
        return (min(candidates) - now).total_seconds()

    # ======================== 新闻收集 & 简报构建 ========================

    async def _build_report(self) -> str:
        """构建完整的新闻简报"""
        now = datetime.datetime.now()
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        date_str = now.strftime("%Y-%m-%d")

        # 1. 并行拉取所有 live 源
        github_items = await self._fetch_github_trending() if SECTION_CONFIG.get("科技圈/GitHub热门", {}).get("source") == "live" else []

        # 2. 组装传给 LLM 的素材
        live_sections = {}
        if github_items:
            live_sections["科技圈/GitHub热门"] = github_items

        llm_sections = {
            k: v["llm_prompt"]
            for k, v in SECTION_CONFIG.items()
            if v["source"] == "llm" and getattr(self.config, f"enable_{k.split('/')[0].lower()}", True)
        }

        # 3. 用 LLM 生成简报
        if self.enable_llm:
            try:
                report = await self._generate_report_via_llm(
                    date_str, weekday_cn, live_sections, llm_sections
                )
                report += f"\n\n> 简报由 LLM 自动生成，内容截至 {now.strftime('%Y-%m-%d %H:%M')}"
                return report
            except Exception as e:
                logger.error(f"[新闻收集] LLM 生成简报失败: {e}")

        # 4. 无 LLM 时的兜底
        fallback = f"每日新闻简报 — {date_str}（{weekday_cn}）\n\n"
        if github_items:
            fallback += "## 科技圈/GitHub热门\n\n"
            for i, item in enumerate(github_items[:5], 1):
                fallback += f"### {i}. {item.get('title', '')}\n{item.get('summary', '')}\n"
                if item.get("url"):
                    fallback += f"- 链接：{item['url']}\n"
                fallback += "\n"
        else:
            fallback += "暂无新闻数据。请检查网络连接。\n"
        return fallback

    async def _generate_report_via_llm(
        self,
        date_str: str,
        weekday_cn: str,
        live_sections: Dict[str, List[Dict]],
        llm_prompts: Dict[str, str],
    ) -> str:
        """调用 LLM 生成完整简报"""
        provider = self.context.provider_manager.get_using_provider(ProviderType.CHAT_COMPLETION)
        if not provider:
            raise Exception("没有可用的 LLM 提供商")

        # 构建用户消息
        user_parts = [f"今天是 {date_str}（{weekday_cn}）。请写一份今日每日新闻简报。"]

        if live_sections:
            user_parts.append("\n\n## 实时数据（请整合进简报）")
            for category, items in live_sections.items():
                user_parts.append(f"\n### {category}")
                for item in items:
                    user_parts.append(f"- {item.get('title', '')}: {item.get('summary', '')}")
                    if item.get("url"):
                        user_parts.append(f"  链接: {item['url']}")

        user_parts.append("\n\n## 请补充以下分类的新闻（基于你的知识）")
        for category, prompt in llm_prompts.items():
            user_parts.append(f"\n### {category}")
            user_parts.append(prompt)

        system_prompt = """你是一个专业新闻整理助手。请生成一份结构清晰的每日新闻简报。

要求：
1. 严格按以下分类组织：AI/人工智能、科技圈/GitHub热门、游戏开发
2. 所有实时数据 MUST 被纳入简报
3. 对于 LLM 知识补充的分类，基于你的训练数据提供新闻，标注日期范围
4. 每条新闻格式：
   ### 序号. 标题
   摘要
   - 来源：xxx
   - 链接：xxx（如果有）
5. 使用中文，语言简洁，对技术/游戏开发者有参考价值
6. 如果某个分类确实没有新闻，跳过该分类，不要写"暂无"
7. 标题前加一级标题 ## 分类名称"""

        model = self.llm_model if self.llm_model else None
        resp = await provider.text_chat(
            prompt="\n".join(user_parts),
            system_prompt=system_prompt,
            model=model,
        )
        return (f"每日新闻简报 — {date_str}（{weekday_cn}）\n\n" + resp.completion_text.strip())

    # ======================== 实时新闻源 ========================

    async def _fetch_github_trending(self) -> List[Dict]:
        """从 GitHub API 拉取热门仓库"""
        days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        url = f"{GITHUB_SEARCH_API}?q=created:%3E{days_ago}&sort=stars&order=desc&per_page=8"
        headers = {"Accept": "application/vnd.github.v3+json"}
        try:
            async with aiohttp.ClientSession(
                headers=headers,
                connector=aiohttp.TCPConnector(verify_ssl=False),
            ) as session:
                async with session.get(url, timeout=self.timeout) as resp:
                    if resp.status != 200:
                        logger.warning(f"[新闻收集] GitHub API 返回 {resp.status}")
                        return []
                    data = await resp.json()
                    items = data.get("items", [])
                    results = []
                    for repo in items:
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
        """手动获取今日新闻简报"""
        try:
            yield event.plain_result("正在收集新闻并整理，请稍候...")
            report = await self._build_report()
            yield event.plain_result(report or "新闻收集失败，请稍后重试。")
        except Exception as e:
            yield event.plain_result(f"新闻收集失败: {e}")
            logger.error(traceback.format_exc())

    @filter.command("新闻状态")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看插件状态"""
        yield event.plain_result(self._gen_status_text())

    # ======================== 管理员命令 ========================

    @filter.command_group("新闻管理")
    def news_admin(self):
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
