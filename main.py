"""
AstrBot 每日新闻收集 + LLM 智能整理插件

支持多分类新闻搜索、按目标个性化推送。
每个推送目标可以指定不同的新闻分类，互不干扰。
"""

import asyncio
import datetime
import json
import os
import traceback
from typing import Any, List, Dict, Optional, Tuple

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderType
from astrbot.core.message.message_event_result import MessageChain


# ========== GitHub API ==========
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
        "payload": lambda q, c: {"query": q, "count": c, "summary": True},
        "parser": lambda d: [
            {"title": i.get("name", ""), "snippet": i.get("snippet", ""), "url": i.get("url", "")}
            for i in (d.get("data", {}).get("webPages", {}).get("value", []))
        ],
    },
    "tavily": {
        "url": "https://api.tavily.com/search",
        "method": "POST",
        "headers": lambda key: {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        "payload": lambda q, c: {"query": q, "max_results": c, "search_depth": "basic"},
        "parser": lambda d: [
            {"title": i.get("title", ""), "snippet": i.get("content", ""), "url": i.get("url", "")}
            for i in d.get("results", [])
        ],
    },
    "brave": {
        "url": "https://api.search.brave.com/res/v1/web/search",
        "method": "GET",
        "headers": lambda key: {"Accept": "application/json", "X-Subscription-Token": key},
        "payload": lambda q, c: {"q": q, "count": c},
        "parser": lambda d: [
            {"title": i.get("title", ""), "snippet": i.get("description", ""), "url": i.get("url", "")}
            for i in (d.get("web", {}).get("results", []))
        ],
    },
}

# ========== 新闻分类注册表 ==========
# 每个分类可配置多个搜索词（依次搜索直到凑够数量），支持中英文
# source: "web" 联网搜索 / "github" 用 GitHub API
CATEGORIES = {
    "AI/人工智能": {
        "emoji": "🤖",
        "source": "web",
        "queries": [
            "2026年 AI 人工智能 大模型 最新进展",
            "2026 AI artificial intelligence news latest",
        ],
    },
    "科技圈/GitHub热门": {
        "emoji": "💻",
        "source": "github",
        "queries": [],
    },
    "游戏开发": {
        "emoji": "🎮",
        "source": "web",
        "queries": [
            "Unreal Engine Godot Unity 最新版本 更新 2026",
            "2026 game engine update release new features",
        ],
    },
    "热门游戏": {
        "emoji": "🌟",
        "source": "web",
        "queries": [
            "2026年 原神 米哈游 最新动态 版本更新",
            "洛克王国 2026 最新 手游 消息",
            "地平线6 Forza Horizon 6 发售 最新",
            "2026 热门游戏 新游 发售 最火",
        ],
    },
    "时事热点": {
        "emoji": "📰",
        "source": "web",
        "queries": [
            "2026年 今日 热点 新闻 头条",
            "today breaking news world events 2026",
        ],
    },
    "科技数码": {
        "emoji": "📱",
        "source": "web",
        "queries": [
            "2026年 科技 数码 新品 手机 电脑 硬件",
            "2026 tech gadgets smartphone hardware launch",
        ],
    },
}


@register(
    "astrbot_plugin_news_collector",
    "Hanako",
    "多分类新闻收集 + LLM 整理 + 按目标个性化推送。支持 AI/科技/游戏/二次元/时事等分类，每个目标可指定不同分类。",
    "2.0.0",
)
class NewsCollectorPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 推送时间
        self.push_time = getattr(self.config, "push_time", "08:00")

        # 解析目标列表（支持每目标指定分类）
        # 格式: platform:type:id[:分类1,分类2]
        raw_groups: list = getattr(self.config, "groups", [])
        self.targets: List[Dict] = []  # [{id, categories}]
        default_cats = getattr(self.config, "categories", ["AI/人工智能", "科技圈/GitHub热门", "游戏开发"])
        for raw in raw_groups:
            parts = raw.rsplit(":", 1)  # 从右边切一次，分出 目标 和 分类
            if len(parts) == 2 and parts[1].count("/") + parts[1].count(",") > 0:
                # 有分类指定
                target_id = parts[0]
                cats = [c.strip() for c in parts[1].split(",") if c.strip() in CATEGORIES]
                self.targets.append({"id": target_id, "categories": cats or default_cats})
            else:
                # 无分类指定，走默认
                self.targets.append({"id": raw, "categories": default_cats})

        # 联网搜索配置
        self.search_provider = getattr(self.config, "search_provider", "")
        self.search_api_key = getattr(self.config, "search_api_key", "")
        self.search_count = getattr(self.config, "search_count", 5)

        # LLM 配置
        self.enable_llm = getattr(self.config, "enable_llm_organize", True)
        self.llm_model = getattr(self.config, "llm_model", "")
        self.timeout = getattr(self.config, "timeout", 30)

        # 去重
        self._seen_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_seen_news.json")
        self._seen_urls: Dict[str, str] = {}
        self._dedup_ttl = getattr(self.config, "dedup_ttl_days", 7)
        self._load_seen()

        # 日志
        cats_summary = ", ".join(str(t["categories"]) for t in self.targets[:3])
        logger.info(
            f"[新闻收集] 插件 v2 已加载: 推送时间={self.push_time}, "
            f"目标={len(self.targets)}个, "
            f"联网={'开' if self.search_api_key else '关'}, "
            f"LLM={'开' if self.enable_llm else '关'}"
        )
        logger.info(f"[新闻收集] 目标详情: {[(t['id'][:30], t['categories']) for t in self.targets]}")

        self._task = asyncio.create_task(self._daily_task())

    # ======================== 去重 ========================

    def _load_seen(self):
        try:
            if os.path.exists(self._seen_file):
                with open(self._seen_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._seen_urls = data.get("urls", {})
                today = datetime.date.today()
                cutoff = today - datetime.timedelta(days=self._dedup_ttl)
                expired = [u for u, d in self._seen_urls.items()
                           if datetime.date.fromisoformat(d) < cutoff]
                for u in expired:
                    del self._seen_urls[u]
                if expired:
                    logger.info(f"[去重] 清理了 {len(expired)} 条过期记录")
        except Exception as e:
            logger.warning(f"[去重] 加载失败: {e}")
            self._seen_urls = {}

    def _save_seen(self):
        try:
            with open(self._seen_file, "w", encoding="utf-8") as f:
                json.dump({"urls": self._seen_urls, "version": 2}, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[去重] 保存失败: {e}")

    def _filter_fresh(self, items: List[Dict]) -> List[Dict]:
        fresh, skipped = [], 0
        for item in items:
            url = item.get("url", "")
            if not url or len(url) < 20 or url not in self._seen_urls:
                fresh.append(item)
            else:
                skipped += 1
        if skipped:
            logger.info(f"[去重] 过滤了 {skipped} 条")
        return fresh

    def _mark_as_seen(self, items: List[Dict]):
        today = datetime.date.today().isoformat()
        added = 0
        for item in items:
            url = item.get("url", "")
            if url and len(url) >= 20 and url not in self._seen_urls:
                self._seen_urls[url] = today
                added += 1
        if added:
            self._save_seen()
            logger.info(f"[去重] 新增 {added} 条")

    # ======================== 定时任务 ========================

    async def _daily_task(self):
        while True:
            try:
                sec = self._calc_sleep()
                logger.info(f"[新闻收集] 距离下次推送还有 {sec/3600:.1f} 小时")
                await asyncio.sleep(sec)

                if not self.targets:
                    logger.warning("[新闻收集] 未配置推送目标")
                    continue

                # 收集所有目标需要的分类（去重）
                needed_cats = set()
                for t in self.targets:
                    needed_cats.update(t["categories"])

                # 按分类搜索（一次搜索，多目标复用）
                cat_results = await self._search_all_categories(list(needed_cats))

                # 按目标生成简报并推送
                all_seen = []
                for t in self.targets:
                    report = await self._build_report_for_target(
                        t["categories"], cat_results
                    )
                    if not report:
                        continue
                    try:
                        mc = MessageChain().message(report)
                        await self.context.send_message(t["id"], mc)
                        logger.info(f"[新闻收集] 已推送到 {t['id'][:40]}")
                        await asyncio.sleep(2)
                    except Exception as e:
                        logger.error(f"[新闻收集] 推送失败 {t['id'][:40]}: {e}")

                # 标记所有用到的新闻为已见
                for items in cat_results.values():
                    all_seen.extend(items)
                if all_seen:
                    self._mark_as_seen(all_seen)

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

    # ======================== 按分类搜索 ========================

    async def _search_all_categories(self, categories: List[str]) -> Dict[str, List[Dict]]:
        """搜索所有需要的分类，返回 {分类名: [items]}"""
        results = {}
        tasks = {}
        for cat in categories:
            if cat in CATEGORIES:
                cfg = CATEGORIES[cat]
                if cfg["source"] == "web" and self.search_api_key:
                    tasks[cat] = self._search_category_web(cat, cfg["queries"])
                elif cfg["source"] == "github":
                    tasks[cat] = self._fetch_github_trending()
                # 不认识的 source 跳过
        for cat, coro in tasks.items():
            try:
                items = await coro
                if items:
                    fresh = self._filter_fresh(items)
                    if fresh:
                        results[cat] = fresh
            except Exception as e:
                logger.warning(f"[搜索] {cat} 失败: {e}")
        return results

    def _today_query(self, query: str) -> str:
        """给搜索词加日期前缀，确保搜到当天/近期的内容"""
        now = datetime.datetime.now()
        date_prefix = now.strftime("%Y年%m月%d日")
        return f"{date_prefix} {query}"

    async def _search_category_web(self, category: str, queries: List[str]) -> List[Dict]:
        """对某个分类执行联网搜索（多查询词直到凑够数量）"""
        all_results, seen_urls = [], set()
        for query in queries:
            date_query = self._today_query(query)
            items = await self._web_search(date_query, self.search_count)
            for item in items:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(item)
            if len(all_results) >= self.search_count:
                break
        return all_results[:self.search_count]

    async def _web_search(self, query: str, count: int = 5) -> List[Dict]:
        if not self.search_api_key or self.search_provider not in SEARCH_PROVIDERS:
            return []
        prov = SEARCH_PROVIDERS[self.search_provider]
        headers = prov["headers"](self.search_api_key)
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(verify_ssl=False),
            ) as session:
                if prov["method"] == "POST":
                    async with session.post(prov["url"], json=prov["payload"](query, count),
                                            headers=headers, timeout=self.timeout) as r:
                        if r.status != 200:
                            return []
                        return prov["parser"](await r.json())
                else:
                    async with session.get(prov["url"], params=prov["payload"](query, count),
                                           headers=headers, timeout=self.timeout) as r:
                        if r.status != 200:
                            return []
                        return prov["parser"](await r.json())
        except Exception as e:
            logger.warning(f"[搜索] \"{query[:30]}...\" 失败: {e}")
            return []

    async def _fetch_github_trending(self) -> List[Dict]:
        days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        url = f"{GITHUB_SEARCH_API}?q=created:%3E{days_ago}&sort=stars&order=desc&per_page=8"
        try:
            async with aiohttp.ClientSession(
                headers={"Accept": "application/vnd.github.v3+json"},
                connector=aiohttp.TCPConnector(verify_ssl=False),
            ) as s:
                async with s.get(url, timeout=self.timeout) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()
                    return [
                        {
                            "title": repo.get("full_name", ""),
                            "snippet": (repo.get("description") or "") + f" ⭐{repo.get('stargazers_count', 0)}",
                            "url": repo.get("html_url", ""),
                        }
                        for repo in data.get("items", [])
                    ]
        except Exception as e:
            logger.warning(f"[GitHub] 请求失败: {e}")
            return []

    # ======================== 按目标生成简报 ========================

    async def _build_report_for_target(
        self, categories: List[str], cat_results: Dict[str, List[Dict]]
    ) -> str:
        """为某个目标生成简报（只含指定分类的新闻）"""
        now = datetime.datetime.now()
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        date_str = now.strftime("%Y-%m-%d")

        # 只取该目标需要的分类
        target_data = {cat: cat_results[cat] for cat in categories if cat in cat_results}

        if not target_data:
            return ""

        if self.enable_llm:
            try:
                report = await self._llm_organize(date_str, weekday_cn, target_data)
                report += f"\n\n> 简报由 LLM + 联网搜索生成，内容截至 {now.strftime('%Y-%m-%d %H:%M')}"
                return report
            except Exception as e:
                logger.error(f"[LLM] 整理失败: {e}")

        return self._fallback_report(date_str, weekday_cn, target_data)

    async def _llm_organize(
        self, date_str: str, weekday_cn: str, target_data: Dict[str, List[Dict]]
    ) -> str:
        provider = self.context.provider_manager.get_using_provider(ProviderType.CHAT_COMPLETION)
        if not provider:
            raise Exception("没有可用的 LLM 提供商")

        now = datetime.datetime.now()

        # 构建素材
        parts = [f"今天是 {date_str}（{weekday_cn}）。请根据以下数据写一份每日新闻简报。"]
        for category, items in target_data.items():
            emoji = CATEGORIES.get(category, {}).get("emoji", "📌")
            parts.append(f"\n\n[{emoji} {category}]")
            for item in items:
                snippet = item.get('snippet', '')[:300]  # 截断过长数据
                parts.append(f"- {item.get('title', '')}: {snippet}")
                if item.get("url"):
                    parts.append(f"  URL: {item['url']}")

        # 动态构建 system prompt（根据实际分类）
        cat_lines = []
        for cat in target_data:
            emoji = CATEGORIES.get(cat, {}).get("emoji", "📌")
            cat_lines.append(f"   {emoji} {cat}")
        cats_str = "\n".join(cat_lines)
        first_emoji = CATEGORIES.get(list(target_data.keys())[0], {}).get("emoji", "📌") if target_data else "📌"

        system_prompt = f"""今天是 {date_str}（{weekday_cn}）。

你是一个专业新闻整理助手。根据上面提供的搜索数据，生成一份每日新闻简报。

【核心要求 - 时效性】
1. 今天是 {date_str}！优先采用搜索数据中日期最近的内容
2. 如果搜索数据中明确标注了日期，只保留 {now.year} 年 {now.month} 月的内容
3. 宁可少报，也不要使用过时的旧闻

【核心要求 - 内容】
1. 只使用上面提供的数据，不要编造信息
2. 按以下分类组织（下面每个分类都要出现在简报里）：
{cats_str}
3. 保留每条新闻的来源 URL
4. 每条新闻的摘要要写得完整充实（至少 2-3 句话），方便快速了解
5. 如果某个分类有多条新闻，全部展示，不要删减
6. 整体内容要足够丰富，不要过于简略

【输出格式 - QQ 消息友好】
不要用 Markdown，用 emoji + 符号排版。每个分类之间空一行。

格式示例：
{first_emoji} AI/人工智能

🔹 标题
完整摘要内容...（至少写 2-3 句话，不要只写一行）
📎 来源：xxx
🔗 https://xxx

🔹 第二条新闻标题
完整摘要内容...
📎 来源：xxx
🔗 https://xxx

每条新闻都要有 🔗 链接。每个分类至少写 3 条新闻。如果数据充足尽量多写。"""

        model = self.llm_model if self.llm_model else None
        resp = await provider.text_chat(
            prompt="\n".join(parts),
            system_prompt=system_prompt,
            model=model,
        )
        return f"每日新闻简报 — {date_str}（{weekday_cn}）\n\n" + resp.completion_text.strip()

    def _fallback_report(
        self, date_str: str, weekday_cn: str, target_data: Dict[str, List[Dict]]
    ) -> str:
        report = f"每日新闻简报 — {date_str}（{weekday_cn}）\n\n"
        for category, items in target_data.items():
            emoji = CATEGORIES.get(category, {}).get("emoji", "📌")
            report += f"{emoji} {category}\n\n"
            for item in items[:5]:
                title = item.get('title', '')
                snippet = (item.get('snippet', '') or '')[:200]  # 截断，避免转发失败
                url = item.get('url', '')
                report += f"🔹 {title}\n"
                if snippet:
                    report += f"{snippet}\n"
                if url:
                    report += f"🔗 {url}\n"
                report += "\n"
        return report

    # ======================== 状态 ========================

    def _gen_status_text(self) -> str:
        sec = self._calc_sleep()
        h, m = int(sec / 3600), int((sec % 3600) / 60)
        search_status = f"{self.search_provider}" if self.search_api_key else "未配置"
        lines = [
            f"新闻收集插件运行中 (v2.0)",
            f"推送时间: {self.push_time}",
            f"推送目标: {len(self.targets)} 个",
        ]
        for t in self.targets:
            short_id = t["id"][:40]
            cats = ", ".join(f"{CATEGORIES.get(c,{}).get('emoji','')}{c}" for c in t["categories"])
            lines.append(f"  → {short_id}: {cats}")
        lines += [
            f"联网搜索: {search_status}",
            f"LLM整理: {'开' if self.enable_llm else '关'}",
            f"去重缓存: {len(self._seen_urls)} 条",
            f"距离下次推送: {h}小时{m}分钟",
        ]
        return "\n".join(lines)

    # ======================== 用户命令 ========================

    @filter.command("新闻")
    async def cmd_news(self, event: AstrMessageEvent):
        try:
            yield event.plain_result("正在联网搜索新闻并整理，请稍候...")
            # 读取配置的默认分类
            default_cats = getattr(self.config, "categories", ["AI/人工智能", "科技圈/GitHub热门", "游戏开发"])
            needed = list(set(cat for t in self.targets for cat in t["categories"]) | set(default_cats))
            cat_results = await self._search_all_categories(needed)
            report = await self._build_report_for_target(default_cats, cat_results)
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
        if not self.targets:
            yield event.plain_result("未配置推送目标")
            return
        yield event.plain_result("正在收集新闻并推送...")
        try:
            needed_cats = set()
            for t in self.targets:
                needed_cats.update(t["categories"])
            cat_results = await self._search_all_categories(list(needed_cats))
            all_seen = []
            for t in self.targets:
                report = await self._build_report_for_target(t["categories"], cat_results)
                if not report:
                    continue
                mc = MessageChain().message(report)
                await self.context.send_message(t["id"], mc)
                await asyncio.sleep(2)
            for items in cat_results.values():
                all_seen.extend(items)
            if all_seen:
                self._mark_as_seen(all_seen)
            yield event.plain_result(f"已推送到 {len(self.targets)} 个目标")
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
