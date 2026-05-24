"""
米游社 COS 收图插件

从米游社 BBS 拉取各游戏的 COS/同人帖子，
下载图片并发送到指定目标。
"""

import asyncio
import datetime
import json
import os
import tempfile
import traceback
from typing import Any, List, Dict

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain


# ========== 米游社 API ==========
MIYOUSHE_API = "https://bbs-api.miyoushe.com/post/wapi/getForumPostList"

# 游戏论坛配置
GAMES = {
    "原神": {"forum_id": 49, "emoji": "🌪️"},
    "崩坏：星穹铁道": {"forum_id": 57, "emoji": "🚂"},
    "绝区零": {"forum_id": 61, "emoji": "⚡"},
    "崩坏3": {"forum_id": 31, "emoji": "💥"},
}

POSTS_PER_GAME = 5
IMGS_PER_POST = 1  # 每个帖子下载几张图
MAX_IMGS_TOTAL = 9  # 总共最多几张（QQ一次最多9图）


@register(
    "astrbot_plugin_news_collector",
    "Hanako",
    "米游社 COS 收图。从米游社 BBS 收集各游戏 COS 图片并推送。",
    "3.1.0",
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

        logger.info(
            f"[米游社COS] 插件 v3.1.0 已加载: 推送={self.push_time}, 目标={len(self.targets)}个"
        )
        for t in self.targets:
            logger.info(f"[米游社COS] 目标: {t['id'][:50]} -> {t['categories']}")
        sec = self._calc_sleep()
        logger.info(f"[米游社COS] 距首次推送还有 {sec/3600:.1f}h")
        self._task = asyncio.create_task(self._daily_task())

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
                images = await self._collect_images(list(needed))

                if not images:
                    logger.warning("[米游社COS] 没有找到带图的帖子")
                    continue

                for t in self.targets:
                    await self._send_images(t["id"], images)

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

    # ======================== 收集图片 ========================

    async def _collect_images(self, games: List[str]) -> List[Dict]:
        """收集图片：{game, title, url, local_path}"""
        all_posts = []
        for game in games:
            cfg = GAMES.get(game)
            if not cfg:
                continue
            try:
                posts = await self._fetch_posts(cfg["forum_id"], POSTS_PER_GAME)
                for p in posts:
                    p["game"] = game
                    p["emoji"] = cfg["emoji"]
                all_posts.extend(posts)
            except Exception as e:
                logger.warning(f"[米游社COS] {game} 拉取失败: {e}")

        # 过滤有图的帖子 + 按图片数排序
        all_posts = [p for p in all_posts if p.get("images")]
        all_posts.sort(key=lambda p: len(p["images"]), reverse=True)

        # 下载图片
        result = []
        count = 0
        for post in all_posts:
            if count >= MAX_IMGS_TOTAL:
                break
            for img_url in post["images"]:
                if count >= MAX_IMGS_TOTAL:
                    break
                try:
                    local = await self._download_img(img_url)
                    if local:
                        result.append({
                            "game": post["game"],
                            "emoji": post["emoji"],
                            "title": post["subject"],
                            "url": img_url,
                            "local_path": local,
                        })
                        count += 1
                except Exception as e:
                    logger.warning(f"[米游社COS] 下载失败: {e}")

        logger.info(f"[米游社COS] 收集完成: {len(result)} 张图")
        return result

    async def _fetch_posts(self, forum_id: int, limit: int) -> List[Dict]:
        """拉取论坛帖子"""
        url = f"{MIYOUSHE_API}?forum_id={forum_id}&page_size={limit}&sort_type=1"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.miyoushe.com/",
        }
        async with aiohttp.ClientSession(
            headers=headers, connector=aiohttp.TCPConnector(verify_ssl=False)
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
                    if not subject:
                        continue
                    images = post.get("images", [])
                    if not images:
                        continue
                    # 补全图片 URL
                    full_imgs = []
                    for img in images:
                        if img and not img.startswith("http"):
                            img = f"https://upload-bbs.miyoushe.com/{img.lstrip('/')}"
                        if img:
                            full_imgs.append(img)
                    results.append({
                        "subject": subject,
                        "images": full_imgs,
                    })
                return results

    async def _download_img(self, url: str) -> str:
        """下载图片到临时文件，返回本地路径"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.miyoushe.com/",
        }
        async with aiohttp.ClientSession(
            headers=headers, connector=aiohttp.TCPConnector(verify_ssl=False)
        ) as session:
            async with session.get(url, timeout=30) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                data = await resp.read()
                suffix = ".jpg"
                if ".png" in url:
                    suffix = ".png"
                elif ".webp" in url:
                    suffix = ".webp"
                f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                try:
                    f.write(data)
                    f.flush()
                    return f.name
                finally:
                    f.close()

    async def _send_images(self, target_id: str, images: List[Dict]):
        """发送图片到目标"""
        # 按游戏分组发送
        by_game: Dict[str, List[Dict]] = {}
        for img in images:
            by_game.setdefault(img["game"], []).append(img)

        for game, imgs in by_game.items():
            emoji = GAMES.get(game, {}).get("emoji", "🎮")
            try:
                # 先发文字标题
                title = f"{emoji} {game} COS ({len(imgs)}张)"
                mc_title = MessageChain().message(title)
                await self.context.send_message(target_id, mc_title)
                await asyncio.sleep(1)

                # 再发图片
                for img in imgs:
                    mc_img = MessageChain().file_image(img["local_path"])
                    await self.context.send_message(target_id, mc_img)
                    await asyncio.sleep(1.5)
                    # 清理临时文件
                    try:
                        os.remove(img["local_path"])
                    except Exception:
                        pass

                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"[米游社COS] 发送失败 {target_id[:40]}: {e}")

    # ======================== 状态 ========================

    def _gen_status_text(self) -> str:
        sec = self._calc_sleep()
        h, m = int(sec / 3600), int((sec % 3600) / 60)
        lines = [
            "米游社 COS 收图插件 (v3.1.0)",
            f"推送时间: {self.push_time}",
            f"推送目标: {len(self.targets)} 个",
        ]
        for t in self.targets:
            cats = ", ".join(f"{GAMES.get(c,{}).get('emoji','')}{c}" for c in t["categories"])
            lines.append(f"  -> {t['id'][:40]}: {cats}")
        lines.append(f"距下次推送: {h}小时{m}分钟")
        return "\n".join(lines)

    # ======================== 命令 ========================

    @filter.command("收图")
    async def cmd_cos(self, event: AstrMessageEvent):
        try:
            yield event.plain_result("正在逛米游社收图...")
            default_cats = getattr(self.config, "categories", list(GAMES.keys()))
            if isinstance(default_cats, str):
                default_cats = [c.strip() for c in default_cats.split(",")]
            cats = [c for c in default_cats if c in GAMES] or list(GAMES.keys())[:3]
            images = await self._collect_images(cats)
            if images:
                await self._send_images(event.get_sender_id(), images)
            else:
                yield event.plain_result("没找到图，可能被限制了")
        except Exception as e:
            yield event.plain_result(f"收图失败: {e}")
            logger.error(traceback.format_exc())

    @filter.command("收图状态")
    async def cmd_status(self, event: AstrMessageEvent):
        yield event.plain_result(self._gen_status_text())

    @filter.command("收图诊断")
    async def cmd_diagnose(self, event: AstrMessageEvent):
        try:
            yield event.plain_result("正在诊断米游社图片源...")
            parts = []
            for game, cfg in GAMES.items():
                posts = await self._fetch_posts(cfg["forum_id"], 3)
                if posts:
                    total_imgs = sum(len(p["images"]) for p in posts)
                    parts.append(f"  {cfg['emoji']} {game}: {len(posts)}帖 {total_imgs}图")
                    for p in posts[:2]:
                        parts.append(f"    {p['subject'][:30]} ({len(p['images'])}图)")
                else:
                    parts.append(f"  {cfg['emoji']} {game}: 无数据")
            yield event.plain_result("\n".join(parts))
        except Exception as e:
            yield event.plain_result(f"诊断失败: {e}")
            logger.error(traceback.format_exc())

    @filter.command_group("收图管理")
    def admin_group(self):
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @admin_group.command("push")
    async def admin_push(self, event: AstrMessageEvent):
        if not self.targets:
            yield event.plain_result("未配置推送目标")
            return
        yield event.plain_result("正在收图推送...")
        try:
            needed = set()
            for t in self.targets:
                needed.update(t["categories"])
            images = await self._collect_images(list(needed))
            if images:
                for t in self.targets:
                    await self._send_images(t["id"], images)
                yield event.plain_result(f"已推送 {len(images)} 张图到 {len(self.targets)} 个目标")
            else:
                yield event.plain_result("没找到图")
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
