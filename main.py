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

DEFAULT_LLM_PROMPT = (
    "请将以下摘要润色成自然的中文群聊用语，保持原意：\n\n{summary}"
)

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
            r'''
            (?<![A-Za-z0-9._~%!$&'*+,;=:@/?#-])   # 左边界（不含括号）
            https?://
            (?:[A-Za-z0-9-]+\.)+[A-Za-z0-9-]+     # 域名
            (?::\d{2,5})?                         # 端口
            (?:/[^\s<>"\]]*)?                     # 路径/查询/片段（排除 ] 防 Markdown）
            (?![A-Za-z0-9._~%!$&'*+,;=:@/?#-])    # 右边界（不含括号）
            ''',
            re.IGNORECASE | re.VERBOSE,
        )

        # 自定义摘要提示词，区分不同机器人的回复。
        self.summary_prefix = self.config.get("summary_prefix", "📝内容摘要：")

        logger.info("fetch_url_summarizer 已初始化")

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有群聊消息，自动处理其中的 URL。"""
        try:
            # ---------- 1. 跳过机器人自身消息 ----------
            if event.get_sender_id() == event.get_self_id():
                logger.debug("收到自身消息，忽略")
                return

            # ---------- 2. 跳过转发/引用 ----------
            for comp in event.message_obj.message:
                if isinstance(comp, (Comp.Forward, Comp.Reply)):
                    return

            # ---------- 3. 摘要开关 ----------
            if not self.config.get("enable_summary", True):
                logger.debug("摘要功能已关闭，跳过处理")
                return

            # ---------- 4. 黑名单群组 ----------
            group_id = event.get_group_id()
            if group_id:
                blacklist_keywords = self.config.get("blacklist_groups", [])
                # 只要 group_id 中命中任意关键字就跳过
                if any(kw in group_id for kw in blacklist_keywords):
                    logger.debug("群组 ID 命中黑名单关键字，跳过处理: %s", group_id)
                    return

            # ---------- 5. 空消息 / 关键词过滤 ----------
            message_text = event.message_str or ""
            if not message_text:
                return

            if self._is_summary_message(message_text):
                logger.debug("消息已包含内容摘要，避免循环解析")
                return
            trigger_keywords = self.config.get("trigger_keywords", [])
            if trigger_keywords and not any(k in message_text for k in trigger_keywords):
                logger.debug("消息未包含触发关键词，跳过处理")
                return

            # ---------- 6. 提取并过滤 URL ----------
            urls = self._extract_urls(message_text)
            if not urls:
                return
            logger.info("检测到 %s 个URL: %s", len(urls), urls)

            blacklist_keywords = self.config.get("blacklist_keywords", [])

            def is_blacklisted(url: str) -> bool:
                return any(k in url for k in blacklist_keywords)

            for url in urls:
                if is_blacklisted(url):
                    logger.debug("URL命中黑名单关键词，跳过: %s", url)
                    continue
                try:
                    summary = await self._get_url_summary(url)
                    if summary:
                        parts = [url, summary]
                        if self.summary_prefix:
                            parts.insert(0, self.summary_prefix)
                        yield event.plain_result("\n".join(parts))
                except Exception as exc:
                    logger.error("处理URL %s 异常: %s", url, exc, exc_info=True)

        except Exception as exc:
            logger.error("on_message 顶层异常: %s", exc, exc_info=True)


    def _is_summary_message(self, text: str) -> bool:
        """根据摘要前缀判断是否为已处理的摘要消息。"""
        prefixes = [self.summary_prefix, "📝内容摘要：", "内容摘要："]
        return any(prefix and prefix in text for prefix in prefixes)

    def _extract_urls(self, text: str) -> List[str]:
        match = self.url_pattern.search(text)
        if not match:
            return []

        url = match.group(0).rstrip('.,;:!?，。！？：；、)]】》’”\'"')  # 去尾部标点

        for left, right in (('(', ')'), ('（', '）')):
            lack = url.count(right) - url.count(left)
            if lack > 0:
                url = url[:-lack]

        url = url.lstrip('<([（【《')  # 去掉常见左包裹符
        return [url] if self._is_valid_url(url) else []

    def _is_valid_url(self, url: str) -> bool:
        """通过标准库解析结果判断 URL 是否有效。"""
        try:
            result = urlparse(url)
            return bool(result.scheme and result.netloc)
        except Exception:  # noqa: BLE001 - urlparse 理论上只抛 ValueError
            return False

    async def _postprocess_with_llm(self, summary: str) -> str:
        """可选地调用大模型，根据提示词对摘要进行润色或翻译。"""
        if not self.config.get("enable_llm_postprocess", False):
            return summary

        provider_id = self.config.get("provider", "")
        provider = (
            self.context.get_provider_by_id(provider_id)
            if provider_id
            else self.context.get_using_provider()
        )
        if not provider:
            logger.warning("未找到可用的大模型提供商，跳过 LLM 处理")
            return summary

        prompt_template = self.config.get("llm_prompt_template") or DEFAULT_LLM_PROMPT
        try:
            prompt = prompt_template.replace("{summary}", summary)
            if "{summary}" not in prompt_template:
                prompt = f"{prompt_template}\n\n{summary}"

            response = await provider.text_chat(
                prompt=prompt,
                session_id=None,
                contexts=[],
                image_urls=[],
                system_prompt="你是一位善于润色文本的助手，保持内容完整准确。",
            )
            if response and getattr(response, "completion_text", None):
                return response.completion_text.strip() or summary
            logger.warning("大模型未返回文本内容，保留原始摘要")
            return summary
        except Exception as exc:  # noqa: BLE001 - 捕获大模型调用异常
            logger.error("大模型处理摘要失败: %s", exc, exc_info=True)
            return summary


    async def _get_url_summary(self, url: str) -> Optional[str]:
        """调用上游摘要服务获取 URL 摘要。"""
        timeout = self.config.get("timeout", 30)
        max_retries = self.config.get("max_retries", 2)

        for attempt in range(max_retries):
            try:
                logger.info("正在获取 URL 摘要 (尝试 %s/%s): %s", attempt + 1, max_retries, url)
                content = await self._fetch_summary_from_service(url, timeout)
                if not content:
                    logger.warning("摘要接口返回了空内容: %s", url)
                    return None

                content = await self._postprocess_with_llm(content)
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

    async def terminate(self):
        """插件卸载时执行清理逻辑。"""
        logger.info("fetch_url_summarizer 插件已卸载")


