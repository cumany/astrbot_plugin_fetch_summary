# URL 摘要提取器 (fetch_url_summarizer)

## 功能概述

`fetch_url_summarizer` 是一个面向 AstrBot 的插件，可以在群聊中自动捕获消息内的 URL，并通过远端摘要服务提取页面要点后返回给群成员。你可以通过配置开关控制是否发送摘要，并自定义提醒前缀来避免与其他机器人互相触发。

### 主要特性
- 自动识别群消息中的 URL，并进行基础的包裹符清理
- 远程调用摘要服务，提取文章核心内容
- 提供开关以决定是否发送摘要，遵循 YAGNI，默认开启
- 支持自定义摘要提示词，便于与其他机器人区分
- 可配置黑名单群组、黑名单关键词和触发关键词，控制摘要范围

## 安装步骤

1. 确认 AstrBot 环境已经部署完成。
2. 将本插件克隆或复制到 AstrBot 的插件目录。
3. 重启 AstrBot 服务，使配置生效。

## 配置项

通过 `config.yaml` 或 AstrBot 的可视化界面可以调整以下配置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable_summary` | 是否向群聊返回摘要 | True |
| `summary_prefix` | 摘要提示词，用于生成的消息前缀 | `📝内容摘要：` |
| `enable_llm_postprocess` | 是否调用大模型对摘要做二次处理 | False |
| `llm_prompt_template` | 大模型提示词模板，支持 `{summary}` 占位符 | `请将以下摘要润色成自然的中文群聊用语，保持原意：

{summary}` |
| `provider` | 指定的大模型提供商 ID，留空使用默认 |  |
| `timeout` | 请求摘要接口的超时时间（秒） | 30 |
| `max_retries` | 请求失败时的最大重试次数 | 2 |
| `blacklist_groups` | 屏蔽的群组 ID 列表 | [] |
| `blacklist_keywords` | URL 黑名单关键词列表 | `['baidu.com']` |
| `trigger_keywords` | 触发关键词列表，留空则对所有 URL 生效 | [] |

## 使用方式

1. 根据需要配置黑名单、触发关键词、摘要提示词以及 LLM 开关相关配置。
2. 机器人在群聊中侦测到 URL 后，如果摘要功能开启且命中触发条件，会自动调用远程接口获取摘要。
3. 如启用 `enable_llm_postprocess`，摘要会先交给所选大模型按提示词生成最终文本。
4. 最终消息按照 `summary_prefix`、URL 与整理后的摘要依次发送到群聊。

### 示例

输入：
```
大家看看这篇文章 https://example.com/article
```

机器人输出：
```
📝内容摘要：
https://example.com/article
（自动生成的摘要内容）
```

## 注意事项

1. 若不同机器人运行同一插件，可通过自定义 `summary_prefix` 避免互相触发。
2. 接口失败或超过最大重试次数时，插件会在日志中记录错误，不会重复发送。
3. 启用 `enable_llm_postprocess` 后需确保 `provider` 可用，否则会回退到原始摘要并记录警告。
4. 若 `enable_summary` 为 False，插件将仅做 URL 过滤，不会尝试调用摘要服务。

## 版本信息

- 当前版本：1.0.0
- 作者：Cuman
- 项目地址：https://github.com/cumany/astrbot_plugin_fetch_summary
