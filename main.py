import asyncio
import json
import os
import traceback
from typing import List, Set

import aiohttp
from astrbot.api.star import Star, register
from astrbot.core.message.components import Image, Node, Plain
from astrbot.core import logger


@register(
    name="astrbot_plugin_genshin_activity_cover",
    desc="实时搬运原神官网官方资讯和装扮图片",
    author="iris",
    version="6.4.0"
)
class GenshinActivityCoverPlugin(Star):
    def __init__(self, context):
        super().__init__(context)
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.plugin_dir, "data")
        self.posted_ids_file = os.path.join(self.data_dir, "posted_ids.json")
        self.config_file = os.path.join(self.data_dir, "config.json")
        # 默认配置
        self.config = {
            "check_interval": 300,
            "target_groups": [1085169520],
            "enabled": True,
            "filter_keywords": ["千星奇域"],
            "forward_threshold_images": 4,  # 单篇文章图片数 >= 此值时合并转发
            "forward_threshold_articles": 2 # 批次文章数 >= 此值时合并转发
        }
        self.posted_ids = set()
        self.api_url = "https://api-takumi.mihoyo.com/post/wapi/getNewsList"
        self.gids = 2
        self.news_types = [1, 3]
        self._task = None
        self._platform_ready = False
        self._forward_sender_name = "米游社搬运工"
        self._forward_sender_uin = None  # 运行时获取 bot QQ 号

    async def initialize(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self._load_config()
        self._load_posted_ids()
        
        if self.config.get("enabled", True):
            self._task = asyncio.create_task(self._delayed_start())
            logger.info(f"[原神资讯] 插件已初始化，等待平台连接...")
            logger.info(f"[原神资讯] 已记录 {len(self.posted_ids)} 条已发送资讯")

    async def _delayed_start(self):
        max_wait = 120
        waited = 0
        
        while waited < max_wait:
            try:
                platform_manager = self.context.platform_manager
                if platform_manager:
                    platforms = platform_manager.get_insts()
                    for platform in platforms:
                        if hasattr(platform, 'bot') and platform.bot:
                            self._platform_ready = True
                            # 获取 bot 自身的 QQ 号
                            try:
                                self._forward_sender_uin = str(platform.bot.self_id)
                                logger.info(f"[原神资讯] Bot QQ号: {self._forward_sender_uin}")
                            except Exception:
                                self._forward_sender_uin = "0"
                                logger.warning(f"[原神资讯] 无法获取 Bot QQ号，使用默认值 0")
                            
                            logger.info(f"[原神资讯] 平台连接就绪，开始轮询...")
                            logger.info(f"[原神资讯] 目标群聊: {self.config.get('target_groups', [])}")
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
        
        logger.warning(f"[原神资讯] 等待超时，尝试直接启动...")
        self._platform_ready = True
        await self._poll_activity_covers()

    async def terminate(self):
        if self._task:
            self._task.cancel()
        self._save_posted_ids()

    def _load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)
                    self.config.update(loaded_config)
                    logger.info(f"[原神资讯] 配置已加载: {self.config}")
            except Exception as e:
                logger.error(f"[原神资讯] 加载配置失败: {e}")
        else:
            logger.warning(f"[原神资讯] 配置文件不存在，使用默认配置")

    def _load_posted_ids(self):
        if os.path.exists(self.posted_ids_file):
            try:
                with open(self.posted_ids_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.posted_ids = set(str(id) for id in data.get("ids", []))
                    logger.info(f"[原神资讯] 已加载 {len(self.posted_ids)} 条已发送记录")
            except Exception as e:
                logger.error(f"[原神资讯] 加载已发布ID失败: {e}")
                self.posted_ids = set()
        else:
            logger.warning(f"[原神资讯] 记录文件不存在，将创建新文件")
            self.posted_ids = set()

    def _save_posted_ids(self):
        try:
            with open(self.posted_ids_file, "w", encoding="utf-8") as f:
                json.dump({"ids": list(self.posted_ids)}, f, ensure_ascii=False, indent=2)
            logger.debug(f"[原神资讯] 已保存 {len(self.posted_ids)} 条记录")
        except Exception as e:
            logger.error(f"[原神资讯] 保存已发布ID失败: {e}")

    def _should_filter(self, title: str) -> bool:
        """检查标题是否需要过滤：七圣召唤、带'装扮'两个字"""
        if "七圣召唤" in title:
            logger.info(f"[原神资讯] 过滤内容（包含'七圣召唤'）: {title}")
            return True
        
        if "装扮" in title:
            logger.info(f"[原神资讯] 过滤内容（包含'装扮'）: {title}")
            return True
        
        filter_keywords = self.config.get("filter_keywords", ["千星奇域"])
        for keyword in filter_keywords:
            if keyword in title:
                logger.info(f"[原神资讯] 过滤内容（包含'{keyword}'）: {title}")
                return True
        
        return False

    def _is_activity_only_cover(self, title: str) -> bool:
        """检查是否只需要发送封面（标题含'活动'）"""
        return "活动" in title

    async def _poll_activity_covers(self):
        await asyncio.sleep(30)
        
        while True:
            try:
                await self._check_all_types()
            except Exception as e:
                logger.error(f"[原神资讯] 轮询失败: {e}")
            
            await asyncio.sleep(self.config.get("check_interval", 300))

    async def _check_all_types(self):
        for news_type in self.news_types:
            try:
                await self._check_new_articles(news_type)
            except Exception as e:
                logger.error(f"[原神资讯] 检查type={news_type}失败: {e}")

    async def _check_new_articles(self, news_type: int):
        params = {
            "gids": self.gids, 
            "type": news_type,
            "page_size": 5
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.miyoushe.com/"
        }
        
        type_name = {1: "官方资讯", 2: "有奖活动", 3: "装扮皮肤"}.get(news_type, f"type{news_type}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.api_url, 
                    params=params, 
                    headers=headers, 
                    timeout=aiohttp.ClientTimeout(total=30)
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
            
            # 收集所有新文章
            pending_articles = []
            
            for article in articles:
                post = article.get("post", {})
                post_id = post.get("post_id")
                title = post.get("subject", "未知资讯")
                
                # 关键检查：是否已发送
                if not post_id:
                    logger.debug(f"[原神资讯] 跳过无ID文章: {title[:30]}")
                    continue
                    
                post_id_str = str(post_id)
                if post_id_str in self.posted_ids:
                    logger.debug(f"[原神资讯] 跳过已发送: {post_id_str} - {title[:30]}")
                    continue
                
                logger.info(f"[原神资讯] 发现新资讯: {post_id_str} - {title[:40]}")
                
                # 过滤检查
                if self._should_filter(title):
                    self.posted_ids.add(post_id_str)
                    self._save_posted_ids()
                    continue
                
                # 收集图片
                all_images: Set[str] = set()
                cover_url = post.get("cover")
                
                # 判断是否只发封面
                only_cover = self._is_activity_only_cover(title)
                
                if only_cover:
                    if cover_url:
                        all_images.add(cover_url)
                        logger.info(f"[原神资讯] 标题含'活动'，仅发送封面: {title[:40]}")
                else:
                    if cover_url:
                        all_images.add(cover_url)
                    
                    images = post.get("images", [])
                    for img_url in images:
                        if img_url:
                            all_images.add(img_url)
                    
                    if all_images:
                        logger.info(f"[原神资讯] 发送全部图片 ({len(all_images)}张): {title[:40]}")
                
                image_list = list(all_images)
                
                if image_list:
                    pending_articles.append({
                        "post_id": post_id_str,
                        "title": title,
                        "images": image_list
                    })
            
            # 统一判断发送方式
            if not pending_articles:
                return
                
            article_count = len(pending_articles)
            forward_threshold_images = self.config.get("forward_threshold_images", 4)
            forward_threshold_articles = self.config.get("forward_threshold_articles", 2)
            
            if article_count >= forward_threshold_articles:
                # 多篇新文章 → 合并转发
                logger.info(f"[原神资讯] 批次有 {article_count} 篇新文章，使用合并转发")
                success = await self._send_batch_forward(pending_articles)
                if success:
                    logger.info(f"[原神资讯] 批次合并转发成功")
                else:
                    logger.warning(f"[原神资讯] 批次合并转发失败，回退到逐条发送")
                    for art in pending_articles:
                        await self._send_images_batch(art["images"], art["title"])
            elif len(pending_articles[0]["images"]) >= forward_threshold_images:
                # 单篇但图片 >= 4张 → 合并转发
                art = pending_articles[0]
                logger.info(f"[原神资讯] 单篇 {len(art['images'])} 张图，使用合并转发")
                success = await self._send_forward_message(art["title"], art["images"])
                if success:
                    logger.info(f"[原神资讯] 单篇合并转发成功")
                else:
                    logger.warning(f"[原神资讯] 单篇合并转发失败，回退到批量发送")
                    await self._send_images_batch(art["images"], art["title"])
            else:
                # 单篇且图片 <= 3张 → 一条消息
                art = pending_articles[0]
                logger.info(f"[原神资讯] 单篇 {len(art['images'])} 张图，批量发送")
                await self._send_images_batch(art["images"], art["title"])
            
            # 发送完成后记录所有 ID
            for art in pending_articles:
                self.posted_ids.add(art["post_id"])
            self._save_posted_ids()
            logger.info(f"[原神资讯] 已记录发送 {len(pending_articles)} 条资讯")
            
        except asyncio.TimeoutError:
            logger.error(f"[原神资讯] API请求超时(type={news_type})")
        except Exception as e:
            logger.error(f"[原神资讯] 检查新资讯失败(type={news_type}): {e}\n{traceback.format_exc()}")

    async def _send_images_batch(self, image_list: List[str], title: str) -> bool:
        """使用一条消息发送多张图片（OneBot JSON 数组格式）
        
        Args:
            image_list: 图片 URL 列表
            title: 资讯标题（用于日志）
            
        Returns:
            bool: 是否发送成功
        """
        try:
            platform_manager = self.context.platform_manager
            if not platform_manager:
                logger.error("[原神资讯] 无法获取平台管理器")
                return False
            
            platforms = platform_manager.get_insts()
            if not platforms:
                logger.error("[原神资讯] 没有可用的平台实例")
                return False
            
            target_groups = self.config.get("target_groups", [])
            
            # 构建 OneBot JSON 数组格式的消息
            # 第一条是标题文字，后面跟图片
            message = [
                {"type": "text", "data": {"text": f"📢 {title}"}}
            ]
            for img_url in image_list:
                message.append({
                    "type": "image",
                    "data": {"file": img_url}
                })
            
            for platform in platforms:
                if not hasattr(platform, 'bot') or not platform.bot:
                    continue
                
                bot = platform.bot
                
                for group_id in target_groups:
                    try:
                        await bot.send_group_msg(
                            group_id=int(group_id),
                            message=message
                        )
                        logger.info(f"[原神资讯] 已批量发送 {len(image_list)} 张图片到群 {group_id}: {title[:40]}")
                        return True
                        
                    except Exception as e:
                        logger.error(f"[原神资讯] 批量发送到群 {group_id} 失败: {e}\n{traceback.format_exc()}")
            
            return False
            
        except Exception as e:
            logger.error(f"[原神资讯] 批量发送图片失败: {e}\n{traceback.format_exc()}")
            return False

    async def _send_forward_message(self, title: str, image_list: List[str]) -> bool:
        """使用合并转发消息发送单篇多图
        
        Args:
            title: 资讯标题
            image_list: 图片 URL 列表
            
        Returns:
            bool: 是否发送成功
        """
        try:
            platform_manager = self.context.platform_manager
            if not platform_manager:
                logger.error("[原神资讯] 无法获取平台管理器")
                return False
            
            platforms = platform_manager.get_insts()
            if not platforms:
                logger.error("[原神资讯] 没有可用的平台实例")
                return False
            
            target_groups = self.config.get("target_groups", [])
            sender_name = self._forward_sender_name
            sender_uin = self._forward_sender_uin or "0"
            
            for platform in platforms:
                if not hasattr(platform, 'bot') or not platform.bot:
                    continue
                
                bot = platform.bot
                
                for group_id in target_groups:
                    try:
                        # 构建转发消息节点
                        nodes = []
                        
                        # 第一个节点：资讯标题
                        nodes.append(Node(
                            content=[Plain(text=f"📢 {title}")],
                            name=sender_name,
                            uin=sender_uin
                        ))
                        
                        # 后续节点：每张图一个节点
                        for idx, img_url in enumerate(image_list, 1):
                            nodes.append(Node(
                                content=[
                                    Image(file=img_url),
                                    Plain(text=f"({idx}/{len(image_list)})")
                                ],
                                name=sender_name,
                                uin=sender_uin
                            ))
                        
                        # 构建 payload
                        payload = {
                            "group_id": int(group_id),
                            "messages": []
                        }
                        
                        # 将 Node 转换为 dict
                        for node in nodes:
                            node_dict = await node.to_dict()
                            payload["messages"].append(node_dict)
                        
                        # 调用 OneBot API 发送合并转发消息
                        await bot.call_action("send_group_forward_msg", **payload)
                        
                        logger.info(f"[原神资讯] 已发送合并转发到群 {group_id}: {title[:40]} ({len(image_list)}张图片)")
                        return True
                        
                    except Exception as e:
                        logger.error(f"[原神资讯] 发送合并转发到群 {group_id} 失败: {e}\n{traceback.format_exc()}")
            
            return False
            
        except Exception as e:
            logger.error(f"[原神资讯] 发送合并转发失败: {e}\n{traceback.format_exc()}")
            return False

    async def _send_batch_forward(self, pending_articles: List[dict]) -> bool:
        """使用合并转发消息发送多篇资讯
        
        Args:
            pending_articles: 文章列表，每项包含 title, images
            
        Returns:
            bool: 是否发送成功
        """
        try:
            platform_manager = self.context.platform_manager
            if not platform_manager:
                logger.error("[原神资讯] 无法获取平台管理器")
                return False
            
            platforms = platform_manager.get_insts()
            if not platforms:
                logger.error("[原神资讯] 没有可用的平台实例")
                return False
            
            target_groups = self.config.get("target_groups", [])
            sender_name = self._forward_sender_name
            sender_uin = self._forward_sender_uin or "0"
            
            for platform in platforms:
                if not hasattr(platform, 'bot') or not platform.bot:
                    continue
                
                bot = platform.bot
                
                for group_id in target_groups:
                    try:
                        nodes = []
                        
                        for art in pending_articles:
                            title = art["title"]
                            images = art["images"]
                            
                            # 每篇文章一个 Node
                            node_content = [Plain(text=f"📢 {title}")]
                            for img_url in images:
                                node_content.append(Image(file=img_url))
                            
                            nodes.append(Node(
                                content=node_content,
                                name=sender_name,
                                uin=sender_uin
                            ))
                        
                        # 构建 payload
                        payload = {
                            "group_id": int(group_id),
                            "messages": []
                        }
                        
                        for node in nodes:
                            node_dict = await node.to_dict()
                            payload["messages"].append(node_dict)
                        
                        await bot.call_action("send_group_forward_msg", **payload)
                        
                        logger.info(f"[原神资讯] 已发送批次合并转发到群 {group_id} ({len(pending_articles)}篇文章)")
                        return True
                        
                    except Exception as e:
                        logger.error(f"[原神资讯] 发送批次合并转发到群 {group_id} 失败: {e}\n{traceback.format_exc()}")
            
            return False
            
        except Exception as e:
            logger.error(f"[原神资讯] 发送批次合并转发失败: {e}\n{traceback.format_exc()}")
            return False
