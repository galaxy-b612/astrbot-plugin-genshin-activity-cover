<div align="center">

# 🎮 原神资讯搬运

<i>📡 自动搬运米游社官方资讯，实时掌握提瓦特最新动态</i>

![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white)
![AstrBot](https://img.shields.io/badge/framework-AstrBot-ff6b6b?style=flat-square)
![Version](https://img.shields.io/badge/version-6.5.1-green?style=flat-square)

</div>

## ✨ 简介

一款为 [**AstrBot**](https://github.com/AstrBotDevs/AstrBot) 设计的原神官方资讯搬运插件。自动轮询米游社 API，实时抓取原神官方资讯（活动公告、版本预告、角色介绍、装扮皮肤等）并推送到指定 QQ 群聊。

---

## ✨ 功能特性

- **🔄 自动轮询**：定时拉取米游社官方资讯，无需手动操作
- **📸 智能发送策略**：
  - 单篇 ≤3 张图 → 一条消息内嵌标题+全部图片
  - 单篇 ≥4 张图 → 合并转发消息（用户体验更佳）
  - 批次 ≥2 篇新文章 → 合并转发打包发送
- **🖥️ WebUI 配置**：所有参数可在 AstrBot 管理面板中直接修改
- **🎯 多群推送**：支持配置多个目标群聊，一次轮询推送到全部群
- **🔇 关键词过滤**：可配置过滤词，自动跳过不感兴趣的资讯类型
- **📝 活动识别**：自动识别"活动"类标题，仅发送封面图
- **🚫 去重机制**：基于 post_id 的已发送记录持久化，不会重复推送
- **🛡️ 异常容错**：发送失败自动回退到备选方案，不影响后续轮询

---

## ⚙️ 配置说明

所有配置项均可在 **WebUI → 插件管理 → 原神资讯搬运** 中可视化修改。

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | true | 是否启用插件 |
| `check_interval` | int | 300 | API 轮询间隔（秒） |
| `target_groups` | list | [1085169520] | 目标群聊白名单（群号列表） |
| `filter_keywords` | list | ["千星奇域"] | 过滤关键词（标题包含则跳过） |
| `forward_threshold_images` | int | 4 | 单篇图片数达标时触发合并转发 |
| `forward_threshold_articles` | int | 2 | 批次文章数达标时触发合并转发 |

---

## 📊 发送效果

| 场景 | 条件 | 用户看到的 |
|---|---|---|
| 普通推送 | 1篇新文章，≤3张图 | 一条消息：`📢 标题` + 图片内嵌 |
| 多图推送 | 1篇新文章，≥4张图 | 合并转发卡片：展开后每条图独立展示 |
| 批量推送 | ≥2篇新文章 | 合并转发卡片：每篇一个节点，含标题+图片 |

---

## 📦 安装

在 AstrBot 插件市场中搜索 `astrbot_plugin_genshin_activity_cover` 安装，或将本仓库克隆到 `data/plugins/` 目录下。

```bash
cd data/plugins
git clone https://github.com/galaxy-b612/astrbot-plugin-genshin-activity-cover.git
```

---

## 🔧 技术细节

- **API 来源**：`https://api-takumi.mihoyo.com/post/wapi/getNewsList`
- **资讯类型**：官方资讯 (type=1) + 装扮皮肤 (type=3)
- **发送协议**：OneBot v11 JSON 数组 + 合并转发 (`send_group_forward_msg`)
- **发送者昵称**：米游社搬运工

---

## 📄 License

MIT
