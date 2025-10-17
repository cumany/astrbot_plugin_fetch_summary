import asyncio
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp
@register(
    "fetch_url_summarizer",
    "Cuman",
    "自动获取 URL 内容摘要的插件",
    "1.0.0",
    "https://github.com/cumany/astrbot_plugin_fetch_url_summarizer",
)
class URLSummarizerPlugin(Star):
    """URL 摘要插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 单条消息中可能存在多个 URL，因此预先构建正则表达式复用。
        self.url_pattern = re.compile(
            r"https?://(?:[-\w.])+(?:[:\d]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.])*)?(?:#(?:[\w.])*)?)?",
            re.IGNORECASE,
        )

        # 用于判断摘要文本是否已经包含中文，避免重复翻译。
        self.chinese_pattern = re.compile(r"[\u4e00-\u9fff]+")

        logger.info("fetch_url_summarizer 已初始化")

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有群聊消息，自动处理其中的 URL。"""
        try:
            for component in event.message_obj.message:
                if isinstance(component, Comp.Forward) or isinstance(component, Comp.Reply):
                    return  # 直接返回，不处理转发或引用消息
            group_id = event.get_group_id()
            if group_id and group_id in self.config.get("blacklist_groups", []):
                logger.debug("命中黑名单群组，跳过处理")
                return
            # 新增：检查消息中是否包含reply和内容摘要
            message_text = event.message_str
            message_obj_str = str(event.message_obj)
            # 检查是否包含"内容摘要"
            if  re.search(r"内容摘要：", message_obj_str):
                logger.debug("消息包含内容摘要，不再解析，以免循环解析")
                return
            if not message_text:
                return

            trigger_keywords = self.config.get("trigger_keywords", [])
            if trigger_keywords and not any(keyword in message_text for keyword in trigger_keywords):
                logger.debug("消息未包含触发关键词，跳过处理")
                return

            urls = self._extract_urls(message_text)
            if not urls:
                return

            logger.info("在消息中检测到 %s 个 URL: %s", len(urls), urls)
            # 黑名单关键词过滤
            blacklist_keywords = self.config.get("blacklist_keywords", [])

            def is_blacklisted(url: str) -> bool:
                return any(keyword in url for keyword in blacklist_keywords)

            for url in urls:
                if is_blacklisted(url):
                    logger.debug(f"URL 命中黑名单关键词，跳过处理: {url}")
                    continue
                try:
                    summary = await self._get_url_summary(url)
                    if summary:
                        yield event.plain_result(f"📝内容摘要：\n{url}\n{summary}")
                except Exception as exc:  # noqa: BLE001 - 记录错误后继续处理其他 URL
                    logger.error("处理 URL %s 时发生异常: %s", url, exc, exc_info=True)
        except Exception as exc:  # noqa: BLE001 - 顶层兜底记录异常
            logger.error("处理消息时发生异常: %s", exc, exc_info=True)

    def _extract_urls(self, text: str) -> List[str]:
        """从文本中提取唯一且有效的 URL 列表。"""
        urls = self.url_pattern.findall(text)
        unique_urls: List[str] = []
        for url in urls:
            if url and url not in unique_urls and self._is_valid_url(url):
                unique_urls.append(url)
        return unique_urls

    def _is_valid_url(self, url: str) -> bool:
        """通过标准库解析结果判断 URL 是否有效。"""
        try:
            result = urlparse(url)
            return bool(result.scheme and result.netloc)
        except Exception:  # noqa: BLE001 - urlparse 理论上只抛 ValueError
            return False

    async def _get_url_summary(self, url: str) -> Optional[str]:
        """调用上游摘要服务获取 URL 摘要，必要时触发翻译。"""
        timeout = self.config.get("timeout", 30)
        max_retries = self.config.get("max_retries", 2)

        for attempt in range(max_retries):
            try:
                logger.info("正在获取 URL 摘要 (尝试 %s/%s): %s", attempt + 1, max_retries, url)
                content = await self._fetch_summary_from_service(url, timeout)
                if not content:
                    logger.warning("摘要接口返回了空内容: %s", url)
                    return None

                if self.config.get("enable_translation", True):
                    content = await self._translate_if_needed(content)
                return content
            except asyncio.TimeoutError:
                logger.error("摘要接口请求超时: %s", url)
            except Exception as exc:  # noqa: BLE001 - 记录后重试
                logger.error("摘要接口请求异常: %s", exc, exc_info=True)

            if attempt < max_retries - 1:
                await asyncio.sleep(2**attempt)

        logger.error("获取 URL 摘要失败，已达到最大重试次数: %s", url)
        return None

    async def _fetch_summary_from_service(self, url: str, timeout: int) -> Optional[str]:
        """直接调用 articlesummarizer.com 上游接口获取摘要。"""
        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "content-type": "application/json",
            "origin": "https://articlesummarizer.com",
            "referer": "https://articlesummarizer.com/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
            ),
        }
        payload: Dict[str, Any] = {"link": url, "website": "article-summarizer"}
        api_url = "https://pjfuothbq9.execute-api.us-east-1.amazonaws.com/upload-link"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(api_url, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise ValueError(f"摘要接口返回非 200 状态 ({response.status}): {error_text}")

                raw_body = await response.text()
                try:
                    outer_data = json.loads(raw_body)
                except json.JSONDecodeError as exc:
                    raise ValueError("上游响应不是有效的 JSON") from exc

                body_text = outer_data.get("result", {}).get("body")
                if not body_text or not isinstance(body_text, str):
                    raise ValueError("上游响应缺少 `result.body` 字段或类型错误")

                inner_data = json.loads(body_text)
                summary_text = inner_data.get("summary")
                if not summary_text or not isinstance(summary_text, str):
                    raise ValueError("解析后的摘要内容为空或类型错误")

                return summary_text.strip()

    async def _translate_if_needed(self, content: str) -> str:
        """摘要为英文时调用 LLM 进行翻译。"""
        try:
            if self.chinese_pattern.search(content):
                logger.debug("摘要包含中文，跳过翻译")
                return content

            logger.info("检测到英文摘要，触发翻译")
            translated = await self._translate_content(content)
            return translated or content
        except Exception as exc:  # noqa: BLE001 - 翻译失败时保留原文
            logger.error("翻译过程中发生异常: %s", exc, exc_info=True)
            return content

    async def _translate_content(self, content: str) -> Optional[str]:
        """通过 AstrBot 绑定的 LLM 提供商执行翻译。"""
        try:
            custom_provider = self.config.get("provider", "")
            provider = (
                self.context.get_provider_by_id(custom_provider)
                if custom_provider
                else self.context.get_using_provider()
            )
            if not provider:
                logger.warning("未找到可用的 LLM 提供商，跳过翻译")
                return None

            translation_prompt = (
                "请将以下英文内容翻译成中文，保持原有的格式和结构：\n\n"
                f"{content}"
            )
            response = await provider.text_chat(
                prompt=translation_prompt,
                session_id=None,
                contexts=[],
                image_urls=[],
                system_prompt="你是一名专业的翻译助手，请准确地将英文翻译成中文。",
            )

            if response and getattr(response, "completion_text", None):
                translated_text = response.completion_text.strip()
                logger.info("翻译完成")
                return translated_text

            logger.warning("翻译接口返回空内容")
            return None
        except Exception as exc:  # noqa: BLE001 - 记录失败原因
            logger.error("翻译请求失败: %s", exc, exc_info=True)
            return None

    async def terminate(self):
        """插件卸载时执行清理逻辑。"""
        logger.info("fetch_url_summarizer 插件已卸载")
