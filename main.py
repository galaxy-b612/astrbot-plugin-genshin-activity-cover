import asyncio
import json
import os
import traceback
from pathlib import Path

import aiohttp
from astrbot.api import logger
from astrbot.api.star import Star, StarTools, register
from astrbot.core.message.components import Image, Node, Plain


PLUGIN_NAME = "astrbot_plugin_genshin_activity_cover"


@register(
    name=PLUGIN_NAME,
    desc="实时搬运原神官网官方资讯和装扮图片",
    author="iris",
    version="6.6.0"
)
class GenshinActivityCoverPlugin(Star):

    def __init__(self, context, config=None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.posted_ids: set[str] = set()

        # 使用框架规范的持久化路径
        self._data_dir: Path = StarTools.get_data_dir(PLUGIN_NAME)
        self._posted_ids_file: Path = self._data_dir / "posted_ids.json"

        self.api_url = "https://api-takumi.mihoyo.com/post/wapi/getNewsList"
        self.gids = 2
        self.news_types = [1, 3]
        self._task: asyncio.Task | None = None
        self._platform_ready = False
        self._forward_sender_name = "米游社搬运工"
        self._forward_sender_uin: str | None = None

    # ── 配置辅助 ──────────────────────────────────────────

    def _cfg(self, key: str, default):
        val = self.config.get(key, default)
        return default if val is None else val

    def _target_groups(self) -> list[int]:
        groups = self._cfg("target_groups", [1085169520])
        if isinstance(groups, str):
            groups = [groups]
        return [int(g) for g in groups]

    # ── 生命周期 ──────────────────────────────────────────

    async def initialize(self):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._load_posted_ids()

        if self._cfg("enabled", True):
            self._task = asyncio.create_task(self._delayed_start())
            logger.info(f"[原神资讯] 插件已初始化，等待平台连接...")
            logger.info(f"[原神资讯] 已记录 {len(self.posted_ids)} 条已发送资讯")

    async def _delayed_start(self):
        max_wait = 120
        waited = 0
        while waited < max_wait:
            try:
                pm = self.context.platform_manager
                if pm:
                    for p in pm.get_insts():
                        if hasattr(p, "bot") and p.bot:
                            self._platform_ready = True
                            try:
                                self._forward_sender_uin = str(p.bot.self_id)
                                logger.info(f"[原神资讯] Bot QQ号: {self._forward_sender_uin}")
                            except Exception:
                                self._forward_sender_uin = "0"

                            logger.info(f"[原神资讯] 平台连接就绪，开始轮询...")
                            logger.info(f"[原神资讯] 目标群聊: {self._target_groups()}")
                            await self._poll_activity_covers()
                            return
                await asyncio.sleep(1)
                waited += 1
                if waited % 10 == 0:
                    logger.debug(f"[原神资讯] 等待平台连接... ({waited}s)")
            except Exception as e:
                logger.debug(f"[原神资讯] 检查平台状态时出错: {e}")
                await asyncio.sleep(1)
                waited += 1

        logger.warning("[原神资讯] 等待超时，尝试直接启动...")
        self._platform_ready = True
        await self._poll_activity_covers()

    async def terminate(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._save_posted_ids()

    # ── 去重持久化 ────────────────────────────────────────

    def _load_posted_ids(self):
        if self._posted_ids_file.exists():
            try:
                data = json.loads(self._posted_ids_file.read_text(encoding="utf-8"))
                self.posted_ids = {str(i) for i in data.get("ids", [])}
                logger.info(f"[原神资讯] 已加载 {len(self.posted_ids)} 条已发送记录")
            except Exception as e:
                logger.error(f"[原神资讯] 加载已发布ID失败: {e}")
        else:
            logger.info("[原神资讯] 记录文件不存在，将创建新文件")

    def _save_posted_ids(self):
        try:
            self._posted_ids_file.write_text(
                json.dumps({"ids": list(self.posted_ids)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"[原神资讯] 保存已发布ID失败: {e}")

    # ── 过滤逻辑 ──────────────────────────────────────────

    def _should_filter(self, title: str) -> bool:
        if "七圣召唤" in title:
            logger.info(f"[原神资讯] 过滤内容（包含'七圣召唤'）: {title}")
            return True
        if "装扮" in title:
            logger.info(f"[原神资讯] 过滤内容（包含'装扮'）: {title}")
            return True
        for kw in self._cfg("filter_keywords", ["千星奇域"]):
            if kw and kw in title:
                logger.info(f"[原神资讯] 过滤内容（包含'{kw}'）: {title}")
                return True
        return False

    def _is_activity_only_cover(self, title: str) -> bool:
        return "活动" in title

    # ── 轮询主循环 ────────────────────────────────────────

    async def _poll_activity_covers(self):
        await asyncio.sleep(30)
        while True:
            try:
                await self._check_all_types()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[原神资讯] 轮询失败: {e}")
            await asyncio.sleep(self._cfg("check_interval", 300))

    async def _check_all_types(self):
        for nt in self.news_types:
            try:
                await self._check_new_articles(nt)
            except Exception as e:
                logger.error(f"[原神资讯] 检查type={nt}失败: {e}")

    async def _check_new_articles(self, news_type: int):
        params = {"gids": self.gids, "type": news_type, "page_size": 5}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.miyoushe.com/",
        }
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    self.api_url, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"[原神资讯] API请求失败(type={news_type}): HTTP {resp.status}")
                        return
                    data = await resp.json()

            if "data" not in data or "list" not in data["data"]:
                logger.debug(f"[原神资讯] type={news_type} 返回数据为空")
                return

            articles = data["data"]["list"]
            logger.debug(f"[原神资讯] type={news_type} 获取到 {len(articles)} 条资讯")

            pending: list[dict] = []
            for article in articles:
                post = article.get("post", {})
                pid = post.get("post_id")
                title = post.get("subject", "未知资讯")

                if not pid:
                    continue
                pid_str = str(pid)
                if pid_str in self.posted_ids:
                    continue

                logger.info(f"[原神资讯] 发现新资讯: {pid_str} - {title[:40]}")

                if self._should_filter(title):
                    self.posted_ids.add(pid_str)
                    self._save_posted_ids()
                    continue

                images: set[str] = set()
                cover = post.get("cover")
                only_cover = self._is_activity_only_cover(title)

                if only_cover:
                    if cover:
                        images.add(cover)
                else:
                    if cover:
                        images.add(cover)
                    for u in post.get("images", []) or []:
                        if u:
                            images.add(u)

                img_list = list(images)
                if img_list:
                    pending.append({"post_id": pid_str, "title": title, "images": img_list})

            if not pending:
                return

            art_cnt = len(pending)
            fwd_img = self._cfg("forward_threshold_images", 4)
            fwd_art = self._cfg("forward_threshold_articles", 2)

            if art_cnt >= fwd_art:
                logger.info(f"[原神资讯] 批次有 {art_cnt} 篇新文章，使用合并转发")
                if not await self._send_batch_forward(pending):
                    logger.warning("[原神资讯] 批次合并转发失败，回退到逐条发送")
                    for a in pending:
                        await self._send_images_batch(a["images"], a["title"])
            elif len(pending[0]["images"]) >= fwd_img:
                a = pending[0]
                logger.info(f"[原神资讯] 单篇 {len(a['images'])} 张图，使用合并转发")
                if not await self._send_forward_message(a["title"], a["images"]):
                    logger.warning("[原神资讯] 单篇合并转发失败，回退到批量发送")
                    await self._send_images_batch(a["images"], a["title"])
            else:
                a = pending[0]
                logger.info(f"[原神资讯] 单篇 {len(a['images'])} 张图，批量发送")
                await self._send_images_batch(a["images"], a["title"])

            for a in pending:
                self.posted_ids.add(a["post_id"])
            self._save_posted_ids()
            logger.info(f"[原神资讯] 已记录发送 {len(pending)} 条资讯")

        except asyncio.TimeoutError:
            logger.error(f"[原神资讯] API请求超时(type={news_type})")
        except Exception as e:
            logger.error(f"[原神资讯] 检查新资讯失败(type={news_type}): {e}\n{traceback.format_exc()}")

    # ── DRY: 统一 Bot / 群组遍历 ──────────────────────────

    def _iter_bots_and_groups(self) -> list[tuple]:
        """遍历所有可用 bot 和目标群组，返回 [(bot, group_id), ...]"""
        result: list[tuple] = []
        pm = self.context.platform_manager
        if not pm:
            return result
        platforms = pm.get_insts()
        if not platforms:
            return result
        for p in platforms:
            if not hasattr(p, "bot") or not p.bot:
                continue
            for gid in self._target_groups():
                result.append((p.bot, gid))
        return result

    def _is_onebot_like(self, bot) -> bool:
        """判断 bot 是否为 OneBot 生态实例（支持合并转发 API）"""
        return hasattr(bot, "call_action")

    # ── 发送方法 ──────────────────────────────────────────

    async def _send_images_batch(self, image_list: list[str], title: str) -> bool:
        sent_any = False
        message = [{"type": "text", "data": {"text": f"\U0001F4E2 {title}"}}]
        for u in image_list:
            message.append({"type": "image", "data": {"file": u}})

        for bot, gid in self._iter_bots_and_groups():
            try:
                await bot.send_group_msg(group_id=gid, message=message)
                logger.info(f"[原神资讯] 已批量发送 {len(image_list)} 张图片到群 {gid}: {title[:40]}")
                sent_any = True
            except Exception as e:
                logger.error(f"[原神资讯] 批量发送到群 {gid} 失败: {e}\n{traceback.format_exc()}")
        return sent_any

    async def _send_forward_message(self, title: str, image_list: list[str]) -> bool:
        sent_any = False
        sn = self._forward_sender_name
        su = self._forward_sender_uin or "0"

        for bot, gid in self._iter_bots_and_groups():
            if not self._is_onebot_like(bot):
                logger.info(f"[原神资讯] 群 {gid} 非 OneBot 平台，跳过合并转发")
                continue
            try:
                nodes = [Node(
                    content=[Plain(text=f"\U0001F4E2 {title}")],
                    name=sn, uin=su,
                )]
                for i, u in enumerate(image_list, 1):
                    nodes.append(Node(
                        content=[Image(file=u), Plain(text=f"({i}/{len(image_list)})")],
                        name=sn, uin=su,
                    ))

                payload = {"group_id": gid, "messages": []}
                for nd in nodes:
                    payload["messages"].append(await nd.to_dict())

                await bot.call_action("send_group_forward_msg", **payload)
                logger.info(f"[原神资讯] 已发送合并转发到群 {gid}: {title[:40]} ({len(image_list)}张图片)")
                sent_any = True
            except Exception as e:
                logger.error(f"[原神资讯] 发送合并转发到群 {gid} 失败: {e}\n{traceback.format_exc()}")
        return sent_any

    async def _send_batch_forward(self, pending: list[dict]) -> bool:
        sent_any = False
        sn = self._forward_sender_name
        su = self._forward_sender_uin or "0"

        for bot, gid in self._iter_bots_and_groups():
            if not self._is_onebot_like(bot):
                logger.info(f"[原神资讯] 群 {gid} 非 OneBot 平台，跳过批次合并转发")
                continue
            try:
                nodes = []
                for a in pending:
                    content = [Plain(text=f"\U0001F4E2 {a['title']}")]
                    for u in a["images"]:
                        content.append(Image(file=u))
                    nodes.append(Node(content=content, name=sn, uin=su))

                payload = {"group_id": gid, "messages": []}
                for nd in nodes:
                    payload["messages"].append(await nd.to_dict())

                await bot.call_action("send_group_forward_msg", **payload)
                logger.info(f"[原神资讯] 已发送批次合并转发到群 {gid} ({len(pending)}篇文章)")
                sent_any = True
            except Exception as e:
                logger.error(f"[原神资讯] 发送批次合并转发到群 {gid} 失败: {e}\n{traceback.format_exc()}")
        return sent_any
