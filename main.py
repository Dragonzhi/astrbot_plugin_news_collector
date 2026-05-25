"""
米游社 COS 收图插件

从米游社 BBS 拉取各游戏的 COS/同人帖子，
以文字（标题+链接）+ 图片的混合消息形式发送到指定目标。
"""

import asyncio
import datetime
import json
import os
import re
import traceback
from typing import Any, List, Dict

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderType
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.message.components import Image, Plain


# ========== 米游社 API ==========
MIYOUSHE_API = "https://bbs-api.miyoushe.com/post/wapi/getForumPostList"

# 游戏论坛配置
GAMES = {
    "原神": {
        "forum_id": 49,  # COS 区
        "emoji": "🌪️",
    },
    "崩坏：星穹铁道": {
        "forum_id": 62,  # COS 区
        "emoji": "🚂",
    },
    "绝区零": {
        "forum_id": 65,  # COS 区
        "emoji": "⚡",
    },
    "崩坏3": {
        "forum_id": 4,   # 同人图（含 COS）
        "emoji": "💥",
    },
}

POSTS_PER_GAME = 5
IMG_CDN = "https://upload-bbs.miyoushe.com"


@register(
    "astrbot_plugin_news_collector",
    "Hanako",
    "米游社 COS 收图。从米游社 BBS 收集各游戏 COS 帖子并推送。",
    "3.2.2",
    repo="https://github.com/Dragonzhi/astrbot_plugin_news_collector",
)
class MiyousheCosPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.push_time = getattr(self.config, "push_time", "08:00")

        # 解析目标
        raw_groups: list = getattr(self.config, "groups", [])
        self.targets: List[Dict] = []
        default_cats = getattr(self.config, "categories", list(GAMES.keys()))
        if isinstance(default_cats, str):
            default_cats = [c.strip() for c in default_cats.split(",")]
        default_cats = [c for c in default_cats if c in GAMES] or list(GAMES.keys())[:3]

        for raw in raw_groups:
            parts = raw.rsplit(":", 1)
            if len(parts) == 2:
                cats = [c.strip() for c in parts[1].split(",") if c.strip() in GAMES]
                if cats:
                    self.targets.append({"id": parts[0], "categories": cats})
                else:
                    self.targets.append({"id": raw, "categories": default_cats})
            else:
                self.targets.append({"id": raw, "categories": default_cats})

        # LLM 配置
        self.enable_llm = getattr(self.config, "enable_llm_organize", True)
        self.llm_model = getattr(self.config, "llm_model", "")

        # 图片限制配置
        self.enable_image_limit = getattr(self.config, "enable_image_limit", True)
        self.max_images = getattr(self.config, "max_images", 1)
        if self.max_images < 0:
            self.max_images = 1

        # 去重
        self._seen_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_seen_news.json"
        )
        self._seen_urls: Dict[str, str] = {}
        self._dedup_ttl = getattr(self.config, "dedup_ttl_days", 7)
        self._load_seen()

        logger.info(
            f"[米游社COS] 插件 v3.2.2 已加载: 推送={self.push_time}, "
            f"目标={len(self.targets)}个, LLM={'开' if self.enable_llm else '关'}, "
            f"图片限制={'开' if self.enable_image_limit else '关'}(max={self.max_images})"
        )
        for t in self.targets:
            logger.info(f"[米游社COS] 目标: {t['id'][:50]} -> {t['categories']}")
        sec = self._calc_sleep()
        logger.info(f"[米游社COS] 距首次推送还有 {sec/3600:.1f}h")
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
                expired = [
                    u for u, d in self._seen_urls.items()
                    if datetime.date.fromisoformat(d) < cutoff
                ]
                for u in expired:
                    del self._seen_urls[u]
                if expired:
                    logger.info(f"[去重] 清理 {len(expired)} 条")
        except Exception as e:
            logger.warning(f"[去重] 加载失败: {e}")
            self._seen_urls = {}

    def _save_seen(self):
        try:
            with open(self._seen_file, "w", encoding="utf-8") as f:
                json.dump({"urls": self._seen_urls, "version": 3}, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[去重] 保存失败: {e}")

    def _filter_fresh(self, items: List[Dict]) -> List[Dict]:
        fresh, skipped = [], 0
        for item in items:
            url = item.get("url", "")
            if not url or url not in self._seen_urls:
                fresh.append(item)
            else:
                skipped += 1
        if skipped:
            logger.info(f"[去重] 过滤 {skipped} 条")
        return fresh

    def _mark_as_seen(self, items: List[Dict]):
        today = datetime.date.today().isoformat()
        added = 0
        for item in items:
            url = item.get("url", "")
            if url and url not in self._seen_urls:
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
                logger.info(f"[米游社COS] 距下次推送还有 {sec/3600:.1f}h")
                await asyncio.sleep(sec)
                if not self.targets:
                    continue

                needed = set()
                for t in self.targets:
                    needed.update(t["categories"])
                cat_posts = await self._fetch_all(list(needed))
                all_seen = []
                for t in self.targets:
                    chain = await self._build_report(t["categories"], cat_posts)
                    if not chain:
                        logger.warning(f"[米游社COS] {t['id'][:40]} 简报为空，跳过推送")
                        continue
                    try:
                        mc = MessageChain(chain)
                        await self.context.send_message(t["id"], mc)
                        logger.info(f"[米游社COS] 已推送 {t['id'][:40]}")
                        await asyncio.sleep(2)
                    except Exception as e:
                        logger.error(f"[米游社COS] 推送失败 {t['id'][:40]}: {e}")
                for items in cat_posts.values():
                    all_seen.extend(items)
                if all_seen:
                    self._mark_as_seen(all_seen)
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[米游社COS] 定时任务出错: {e}")
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

    # ======================== API 拉取 ========================

    async def _fetch_all(self, categories: List[str]) -> Dict[str, List[Dict]]:
        results = {}
        tasks = {}
        for cat in categories:
            if cat in GAMES:
                tasks[cat] = self._fetch_game_posts(cat)
        for cat, coro in tasks.items():
            try:
                items = await coro
                if items:
                    fresh = self._filter_fresh(items)
                    if fresh:
                        results[cat] = fresh
            except Exception as e:
                logger.warning(f"[米游社COS] {cat} 拉取失败: {e}")
        return results

    async def _fetch_game_posts(self, game: str) -> List[Dict]:
        cfg = GAMES.get(game)
        if not cfg:
            return []
        forum_id = cfg["forum_id"]
        url = f"{MIYOUSHE_API}?forum_id={forum_id}&page_size={POSTS_PER_GAME}&sort_type=1"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.miyoushe.com/",
        }
        try:
            async with aiohttp.ClientSession(
                headers=headers,
                connector=aiohttp.TCPConnector(verify_ssl=False),
            ) as session:
                async with session.get(url, timeout=15) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    if data.get("retcode") != 0:
                        return []

                    items = data.get("data", {}).get("list", [])
                    results = []
                    for item in items:
                        post = item.get("post", {})
                        subject = post.get("subject", "").strip()
                        content = post.get("content", "").strip()
                        created = post.get("created_at", 0)
                        images = post.get("images", [])
                        post_id = post.get("post_id", "")

                        if not subject:
                            continue

                        clean_content = re.sub(r"<[^>]+>", "", content)

                        cover = ""
                        if images:
                            cover = images[0]
                        if not cover:
                            cover = post.get("cover", "")
                        if not cover:
                            vod_list = post.get("vod_list", [])
                            if vod_list and isinstance(vod_list, list):
                                for vod in vod_list:
                                    if isinstance(vod, dict):
                                        cover = vod.get("cover") or vod.get("screenshot") or ""
                                        if cover:
                                            break
                        # 视频帖子：从 item.video 或 item.vod 取封面
                        if not cover:
                            video = item.get("video", {})
                            if isinstance(video, dict):
                                cover = video.get("cover", "")
                        if not cover:
                            vod = item.get("vod", {})
                            if isinstance(vod, dict):
                                cover = vod.get("cover", "")

                        if cover and not cover.startswith("http"):
                            cover = f"{IMG_CDN}/{cover.lstrip('/')}"

                        post_url = f"https://www.miyoushe.com/ys/article/{post_id}" if post_id else ""
                        dt = datetime.datetime.fromtimestamp(created)
                        time_str = dt.strftime("%m/%d %H:%M")

                        results.append({
                            "title": subject,
                            "snippet": clean_content[:200],
                            "url": post_url,
                            "time": time_str,
                            "image": cover,
                            "source": f"米游社·{game}",
                        })
                    return results
        except asyncio.TimeoutError:
            return []
        except Exception as e:
            logger.warning(f"[米游社COS] {game} 请求失败: {e}")
            return []

    # ======================== 简报生成 ========================

    async def _build_report(
        self, categories: List[str], cat_posts: Dict[str, List[Dict]]
    ) -> List:
        now = datetime.datetime.now()
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        date_str = now.strftime("%Y-%m-%d")

        target_data = {cat: cat_posts[cat] for cat in categories if cat in cat_posts}
        if not target_data:
            return []

        if self.enable_llm:
            try:
                return await self._organize_with_llm(date_str, weekday_cn, target_data)
            except Exception as e:
                logger.error(f"[LLM] 整理失败: {e}")

        return self._simple_report(date_str, weekday_cn, target_data)

    async def _organize_with_llm(
        self, date_str: str, weekday_cn: str, target_data: Dict[str, List[Dict]]
    ) -> List:
        provider = self.context.provider_manager.get_using_provider(ProviderType.CHAT_COMPLETION)
        if not provider:
            raise Exception("没有可用的 LLM 提供商")

        parts = [f"今天是{date_str}（{weekday_cn}）。请根据以下来自米游社的帖子数据，整理一份游戏资讯简报。"]
        for game, posts in target_data.items():
            emoji = GAMES.get(game, {}).get("emoji", "🎮")
            parts.append(f"\n\n[{emoji} {game}]")
            for p in posts:
                parts.append(f"- {p['title']}")
                if p.get("snippet"):
                    parts.append(f"  摘要: {p['snippet'][:200]}")
                if p.get("time"):
                    parts.append(f"  时间: {p['time']}")
                if p.get("url"):
                    parts.append(f"  URL: {p['url']}")

        cat_lines = []
        for cat in target_data:
            emoji = GAMES.get(cat, {}).get("emoji", "🎮")
            cat_lines.append(f"   {emoji} {cat}")
        cats_str = "\n".join(cat_lines)
        first_emoji = GAMES.get(list(target_data.keys())[0], {}).get("emoji", "🎮") if target_data else "🎮"

        system_prompt = f"""今天是{date_str}（{weekday_cn}）。
你是一个游戏资讯助手。根据米游社的帖子数据，生成一份游戏资讯简报。
【要求】
1. 只使用上面提供的帖子数据，不要编造
2. 按游戏分类组织：
{cats_str}
3. 每个帖子给出简要摘要（1-2句话），让读者快速了解内容
4. 保留帖子链接

【输出格式 - QQ友好】
{first_emoji} 原神

📌 标题
摘要...
🔗 https://...

{first_emoji} 崩坏：星穹铁道
📌 标题
摘要...
🔗 https://..."""

        model = self.llm_model if self.llm_model else None
        resp = await provider.text_chat(
            prompt="\n".join(parts),
            system_prompt=system_prompt,
            model=model,
        )
        header = f"米游社资讯 · {date_str}（{weekday_cn}）\n\n"
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        footer = f"\n\n> 内容来自米游社，截至 {now_str}"
        llm_text = resp.completion_text.strip()

        chain = [Plain(header)]
        img_count = 0
        all_posts = []
        for posts in target_data.values():
            for p in posts:
                all_posts.append(p)

        remaining_text = llm_text
        for p in all_posts:
            title = p['title']
            img_url = p.get("image")
            idx = remaining_text.find(title)
            if idx != -1:
                before = remaining_text[:idx + len(title)]
                chain.append(Plain(before))
                remaining_text = remaining_text[idx + len(title):]
                if img_url:
                    if not self.enable_image_limit or img_count < self.max_images:
                        chain.append(Image.fromURL(img_url))
                        img_count += 1
            else:
                pass

        chain.append(Plain(remaining_text + footer))
        return chain

    def _simple_report(
        self, date_str: str, weekday_cn: str, target_data: Dict[str, List[Dict]]
    ) -> List:
        chain = [Plain(f"米游社资讯 · {date_str}（{weekday_cn}）\n\n")]
        img_count = 0
        for game, posts in target_data.items():
            emoji = GAMES.get(game, {}).get("emoji", "🎮")
            chain.append(Plain(f"{emoji} {game}\n\n"))
            for p in posts[:5]:
                text = f"📌 {p['title']}\n"
                if p.get("snippet"):
                    text += f"{p['snippet'][:200]}\n"
                if p.get("time"):
                    text += f"⏰ {p['time']}\n"
                if p.get("url"):
                    text += f"🔗 {p['url']}\n"
                text += "\n"
                chain.append(Plain(text))

                img_url = p.get("image")
                if img_url:
                    chain.append(Image.fromURL(img_url))
                    img_count += 1
                    if self.enable_image_limit and img_count >= self.max_images:
                        return chain
        return chain

    # ======================== 状态 ========================

    def _gen_status_text(self) -> str:
        sec = self._calc_sleep()
        h, m = int(sec / 3600), int((sec % 3600) / 60)
        lines = [
            "米游社 COS 收图插件运行中 (v3.2.2)",
            f"推送时间: {self.push_time}",
            f"推送目标: {len(self.targets)} 个",
        ]
        for t in self.targets:
            short_id = t["id"][:40]
            cats = ", ".join(f"{GAMES.get(c,{}).get('emoji','')}{c}" for c in t["categories"])
            lines.append(f"  -> {short_id}: {cats}")
        lines += [
            f"LLM整理: {'开' if self.enable_llm else '关'}",
            f"图片限制: {'开' if self.enable_image_limit else '关'} (max={self.max_images})",
            f"去重缓存: {len(self._seen_urls)} 条",
            f"距下次推送: {h}小时{m}分钟",
        ]
        return "\n".join(lines)

    # ======================== 命令 ========================

    @filter.command("米游社")
    async def cmd_miyoushe(self, event: AstrMessageEvent):
        try:
            yield event.plain_result("正在拉取米游社最新帖子...")
            default_cats = getattr(self.config, "categories", list(GAMES.keys()))
            if isinstance(default_cats, str):
                default_cats = [c.strip() for c in default_cats.split(",")]
            cats = [c for c in default_cats if c in GAMES]
            if not cats:
                cats = list(GAMES.keys())[:3]
                logger.warning(f"[米游社COS] 配置文件中的 categories 不匹配任何游戏，使用默认: {cats}")
            cat_posts = await self._fetch_all(cats)
            if not cat_posts:
                yield event.plain_result(
                    "未能拉取到任何帖子，可能原因：\n"
                    "1. 服务器 IP 被米游社 API 限制\n"
                    "2. 网络连接异常\n"
                    "3. 论坛 ID 可能已变更"
                )
                return
            chain = await self._build_report(cats, cat_posts)
            if chain:
                yield event.chain_result(chain)
            else:
                yield event.plain_result("简报为空，可能是 LLM 整理失败。")
        except Exception as e:
            yield event.plain_result(f"拉取失败: {e}")
            logger.error(traceback.format_exc())

    @filter.command("米游社状态")
    async def cmd_status(self, event: AstrMessageEvent):
        yield event.plain_result(self._gen_status_text())

    @filter.command("米游社诊断")
    async def cmd_diagnose(self, event: AstrMessageEvent):
        try:
            yield event.plain_result("正在诊断米游社 API...")
            cats = list(GAMES.keys())
            parts = [f"诊断结果 ({len(cats)} 个游戏):"]
            for cat in cats:
                try:
                    posts = await self._fetch_game_posts(cat)
                    emoji = GAMES[cat]["emoji"]
                    if posts:
                        parts.append(f"  {emoji} {cat}: {len(posts)} 条")
                        for p in posts[:2]:
                            parts.append(f"    - {p['title'][:50]}")
                    else:
                        parts.append(f"  {emoji} {cat}: 返回空（可能被限制）")
                except Exception as e:
                    parts.append(f"  {cat}: 出错 {e}")
            yield event.plain_result("\n".join(parts))
        except Exception as e:
            yield event.plain_result(f"诊断失败: {e}")
            logger.error(traceback.format_exc())

    @filter.command_group("米游社管理")
    def admin_group(self):
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @admin_group.command("push")
    async def admin_push(self, event: AstrMessageEvent):
        if not self.targets:
            yield event.plain_result("未配置推送目标")
            return
        yield event.plain_result("正在拉取并推送...")
        try:
            needed = set()
            for t in self.targets:
                needed.update(t["categories"])
            if not needed:
                needed = set(list(GAMES.keys())[:3])
            cat_posts = await self._fetch_all(list(needed))
            all_seen = []
            for t in self.targets:
                chain = await self._build_report(t["categories"], cat_posts)
                if not chain:
                    continue
                mc = MessageChain(chain)
                await self.context.send_message(t["id"], mc)
                await asyncio.sleep(2)
            for items in cat_posts.values():
                all_seen.extend(items)
            if all_seen:
                self._mark_as_seen(all_seen)
            yield event.plain_result(f"已推送到 {len(self.targets)} 个目标")
        except Exception as e:
            yield event.plain_result(f"推送失败: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @admin_group.command("status")
    async def admin_status(self, event: AstrMessageEvent):
        yield event.plain_result(self._gen_status_text())

    async def terminate(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[米游社COS] 插件已卸载")
