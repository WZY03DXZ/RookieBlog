---
title: 用 Markdown 和本地图片开始写博客
date: 2026-04-21
summary: RookieBlog 的目标是尽量轻量、开箱可用，并且让文章和图片都能直接跟着仓库走。
tags: [Markdown, GitHub Pages, Static Site]
cover: /assets/images/local-demo.svg
---

# 用 Markdown 和本地图片开始写博客

如果你想要一个依赖少、容易安装、适合托管到 GitHub 的博客框架，最重要的是把写作流程压缩到足够简单：

- 文章就是 `Markdown`
- 图片直接放仓库
- 构建后输出纯静态页面

## 本地图片不需要图床

下面这张图就是直接放在仓库里的本地 SVG：

![本地示例图](/assets/images/local-demo.svg)

这样有几个好处：

1. 文章和图片一起版本管理。
2. 仓库迁移时内容不会丢。
3. 不依赖第三方图片服务的可用性。

## 构建命令

```bash
python3 rookieblog.py build
```

构建完成后，生成的静态文件会出现在 `dist/` 目录里。

## 项目信息

这个项目目前由 `WZY03DXZ` 持续维护，当前重点放在博客框架功能打磨、写作体验优化和页面细节完善上。

后续会随着功能逐步稳定，按照模块和能力分阶段开放源码，让整个博客框架更适合长期使用和继续扩展。
