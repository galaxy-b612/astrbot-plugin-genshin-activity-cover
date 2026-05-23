import asyncio
import json
import traceback
from pathlib import Path

import aiohttp
from astrbot.api import logger
from astrbot.api.star import Star, StarTools, register
from astrbot.core.message.components import Image, Node, Plain


PLUGIN_NAME = "astrbot_plugin_genshin_activity_cover"

# ── 米游社 API 常量 ──────────────────────────────────────
# gids=2: 原神社区；news_type 1=官方资讯, 3=装扮皮肤
_GAME_ID    = 2
_NEWS_TYPES = [1, 3]
_PAGE_SIZE  = 5
_API_TIMEOUT = aiohttp.ClientTimeout(total=30)


@register(
    name=PLUGIN_NAME,
    desc="实时搬运原神官网官方资讯和装扮图片",
    author="iris",
    version="7.1.0"
)
class GenshinActivityCoverPlugin(Star):

    def __init__(self, context, config=None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.posted_ids: set[str] = set()

        self._data_dir: Path = StarTools.get_data_dir(PLUGIN_NAME)
        self._posted_ids_file: Path = self._data_dir / "posted_ids.json"

        self.api_url = "https://api-takumi.mihoyo.com/post/wapi/getNewsList"
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._dirty = False
        self._shutdown = False
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
        return sorted({int(g) for g in groups})

    def _build_headers(self) -> dict[str, str]:
        ua = self._cfg("user_agent", "")
        return {
            "User-Agent": ua or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.miyoushe.com/",
        }

    # ── 生命周期 ──────────────────────────────────────────

    async def initialize(self):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._load_posted_ids()
        if self._session is None:
            self._session = aiohttp.ClientSession()

        if self._cfg("enabled", True):
            self._task = asyncio.create_task(self._delayed_start())
            logger.info("[原神资讯] 插件已初始化，等待平台连接...")
            logger.info(f"[原神资讯] 已记录 {len(self.posted_ids)} 条已发送资讯")

    async def _delayed_start(self):
        """等待平台就绪后进入轮询，超时可配置"""
        timeout = self._cfg("platform_wait_timeout", 120)
        waited = 0
        while waited < timeout and not self._shutdown:
            try:
                pm = self.context.platform_manager
                if pm:
                    for p in pm.get_insts():
                        if hasattr(p, "bot") and p.bot:
                            self._resolve_forward_uin(p)
                            logger.info("[原神资讯] 平台连接就绪，开始轮询...")
                            logger.info(f"[原神资讯] 目标群聊: {self._target_groups()}")
                            await self._poll_activity_covers()
                            return
                await asyncio.sleep(1)
                waited += 1
                if waited % 10 == 0:
                    logger.debug(f"[原神资讯] 等待平台连接... ({waited}s)")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"[原神资讯] 检查平台状态时出错: {e}")
                await asyncio.sleep(1)
                waited += 1

        if not self._shutdown:
            logger.warning("[原神资讯] 等待超时，尝试直接启动...")
            await self._poll_activity_covers()

    def _resolve_forward_uin(self, platform) -> None:
        try:
            self._forward_sender_uin = str(platform.bot.self_id)
            logger.info(f"[原神资讯] Bot QQ号: {self._forward_sender_uin}")
        except Exception:
            logger.warning("[原神资讯] 无法获取 Bot QQ 号，合并转发将使用兜底值")

    async def terminate(self):
        self._shutdown = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._save_posted_ids()
        if self._session:
            await self._session.close()

    # ── 去重持久化 ────────────────────────────────────────

    def _load_posted_ids(self):
        if self._posted_ids_file.exists():
            try:
                data = json.loads(self._posted_ids_file.read_text(encoding="utf-8"))
                self.posted_ids = {str(i) for i in data.get("ids", [])}
                logger.info(f"[原神资讯] 已加载 {len(self.posted_ids)} 条已发送记录")
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"[原神资讯] 加载已发布ID失败: {e}")
        else:
            logger.info("[原神资讯] 记录文件不存在，将创建新文件")

    def _save_posted_ids(self):
        if not self._dirty:
            return
        try:
            self._posted_ids_file.write_text(
                json.dumps({"ids": list(self.posted_ids)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._dirty = False
        except OSError as e:
            logger.error(f"[原神资讯] 保存已发布ID失败: {e}")

    # ── 过滤逻辑 ──────────────────────────────────────────

    def _should_filter(self, title: str) -> bool:
        if "七圣召唤" in title:
            logger.info(f"[原神资讯] 过滤（七圣召唤）: {title}")
            return True
        if "装扮" in title:
            logger.info(f"[原神资讯] 过滤（装扮）: {title}")
            return True
        for kw in self._cfg("filter_keywords", ["千星奇域"]):
            if kw and kw in title:
                logger.info(f"[原神资讯] 过滤（{kw}）: {title}")
                return True
        return False

    # ── 轮询主循环 ────────────────────────────────────────

    async def _poll_activity_covers(self):
        """主轮询循环，启动延迟和间隔均可通过 WebUI 配置"""
        startup_delay = self._cfg("startup_delay", 30)
        await asyncio.sleep(startup_delay)
        while not self._shutdown:
            try:
                await self._check_all_types()
            except asyncio.CancelledError:
                raise
            except aiohttp.ClientError as e:
                logger.error(f"[原神资讯] 网络请求失败: {e}")
            except Exception as e:
                logger.error(f"[原神资讯] 轮询未知错误: {e}")
            await asyncio.sleep(self._cfg("check_interval", 300))

    async def _check_all_types(self):
        for nt in _NEWS_TYPES:
            try:
                await self._process_news_type(nt)
            except aiohttp.ClientError as e:
                logger.error(f"[原神资讯] 检查 type={nt} 网络错误: {e}")
            except Exception as e:
                logger.error(f"[原神资讯] 检查 type={nt} 失败: {e}")

    # ── 核心业务 ──────────────────────────────────────────

    async def _process_news_type(self, news_type: int):
        articles = await self._fetch_articles(news_type)
        if not articles:
            return

        pending: list[dict] = []
        for article in articles:
            parsed = self._parse_one_article(article)
            if parsed is None:
                continue
            pending.append(parsed)

        if not pending:
            self._save_posted_ids()
            return

        art_cnt = len(pending)
        fwd_img = self._cfg("forward_threshold_images", 4)
        fwd_art = self._cfg("forward_threshold_articles", 2)
        use_forward = (art_cnt >= fwd_art) or (art_cnt == 1 and len(pending[0]["images"]) >= fwd_img)

        await self._send_per_platform(pending, use_forward)

        for a in pending:
            self.posted_ids.add(a["post_id"])
        self._dirty = True
        self._save_posted_ids()

    async def _fetch_articles(self, news_type: int) -> list[dict] | None:
        params = {"gids": _GAME_ID, "type": news_type, "page_size": _PAGE_SIZE}
        try:
            sess = self._session
            if sess is None:
                return None
            async with sess.get(
                self.api_url, params=params, headers=self._build_headers(),
                timeout=_API_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    logger.error(f"[原神资讯] API HTTP {resp.status} (type={news_type})")
                    return None
                data = await resp.json()
            if "data" not in data or "list" not in data["data"]:
                return None
            return data["data"]["list"]
        except aiohttp.ClientError as e:
            logger.error(f"[原神资讯] 拉取 type={news_type} 网络异常: {e}")
            return None

    def _parse_one_article(self, article: dict) -> dict | None:
        post = article.get("post", {})
        pid = post.get("post_id")
        title = post.get("subject", "未知资讯")
        if not pid:
            return None
        pid_str = str(pid)
        if pid_str in self.posted_ids:
            return None
        logger.info(f"[原神资讯] 发现新资讯: {pid_str} - {title[:40]}")
        if self._should_filter(title):
            self.posted_ids.add(pid_str)
            self._dirty = True
            return None
        images: set[str] = self._extract_images(post, title)
        if not images:
            return None
        return {"post_id": pid_str, "title": title, "images": list(images)}

    def _extract_images(self, post: dict, title: str) -> set[str]:
        cover = post.get("cover")
        images: set[str] = set()
        if "活动" in title:
            if cover:
                images.add(cover)
        else:
            if cover:
                images.add(cover)
            for u in post.get("images") or []:
                if u:
                    images.add(u)
        return images

    # ── 分平台发送 + 重试 ────────────────────────────────

    async def _retry_send(self, action, gid: int, label: str) -> bool:
        """执行发送动作，失败后等 3 秒重试一次（对抗 NapCat NT 内核超时）"""
        for attempt in range(2):
            try:
                await action()
                if attempt > 0:
                    logger.info(f"[原神资讯] 群 {gid} 重试成功: {label}")
                return True
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"[原神资讯] 群 {gid} 发送超时，3秒后重试: {label}")
                    await asyncio.sleep(3)
                else:
                    logger.error(f"[原神资讯] 群 {gid} 发送失败（已重试）: {label}\n{e}\n{traceback.format_exc()}")
        return False


    async def _send_per_platform(self, pending: list[dict], use_forward: bool):
        pm = self.context.platform_manager
        if not pm:
            return
        for p in pm.get_insts():
            if not hasattr(p, "bot") or not p.bot:
                continue
            bot = p.bot
            for gid in self._target_groups():
                if use_forward and self._is_onebot_like(bot):
                    ok = await self._try_forward_to_group(bot, gid, pending)
                    if not ok:
                        logger.warning(f"[原神资讯] 群 {gid} 合并转发失败，降级批量发送")
                        await self._do_batch_to_group(bot, gid, pending)
                else:
                    await self._do_batch_to_group(bot, gid, pending)

    async def _do_batch_to_group(self, bot, gid: int, pending: list[dict]):
        for a in pending:
            await self._send_images_batch_to(bot, gid, a["images"], a["title"])

    async def _send_images_batch_to(self, bot, gid: int, image_list: list[str], title: str):
        msg = [{"type": "text", "data": {"text": f"\U0001F4E2 {title}"}}]
        for u in image_list:
            msg.append({"type": "image", "data": {"file": u}})
        await self._retry_send(
            lambda: self._do_send_batch(bot, gid, msg),
            gid, f"批量发送 {len(image_list)} 张图片: {title[:40]}",
        )

    async def _do_send_batch(self, bot, gid: int, msg: list[dict]):
        if hasattr(bot, "send_group_msg"):
            await bot.send_group_msg(group_id=gid, message=msg)
        else:
            logger.warning(f"[原神资讯] 群 {gid} 平台不支持 send_group_msg，尝试通用消息接口")
            await self._send_via_platform(bot, gid, msg)
        logger.info(f"[原神资讯] 已批量发送到群 {gid}")

    async def _send_via_platform(self, bot, gid: int, msg: list[dict]):
        """非 OneBot 平台的兜底发送（尝试框架通用能力）"""
        if hasattr(bot, "send"):
            await bot.send(group_id=gid, message=msg)
        else:
            logger.error(f"[原神资讯] 群 {gid} 平台无任何可用发送接口，消息丢失")

    async def _try_forward_to_group(self, bot, gid: int, pending: list[dict]) -> bool:
        async def _do():
            nodes = self._build_forward_nodes(pending)
            payload = {"group_id": gid, "messages": []}
            for nd in nodes:
                payload["messages"].append(await nd.to_dict())
            await bot.call_action("send_group_forward_msg", **payload)
            logger.info(f"[原神资讯] 已发送合并转发到群 {gid} ({len(pending)}篇)")

        return await self._retry_send(_do, gid, f"合并转发 {len(pending)}篇")

    def _build_forward_nodes(self, pending: list[dict]) -> list[Node]:
        sn = self._forward_sender_name
        su = self._sender_uin
        nodes: list[Node] = []
        for a in pending:
            content = [Plain(text=f"\U0001F4E2 {a['title']}")]
            for u in a["images"]:
                content.append(Image(file=u))
            nodes.append(Node(content=content, name=sn, uin=su))
        return nodes

    def _is_onebot_like(self, bot) -> bool:
        return hasattr(bot, "call_action")

    @property
    def _sender_uin(self) -> str:
        return self._forward_sender_uin or ""
