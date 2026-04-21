# RookieBlog

一个尽量轻量的静态博客框架：

- 只依赖 Python 标准库，不需要安装第三方包
- 文章直接写在 Markdown 里
- 图片、SVG、附件可以直接放仓库，不需要第三方图床
- 生成静态网页，适合部署到 GitHub Pages

## 目录结构

```text
RookieBlog/
├── content/
│   ├── assets/         # 图片、SVG、附件
│   ├── pages/          # 独立页面
│   └── posts/          # 博客文章
├── static/             # 样式、图标等静态资源
├── .github/workflows/  # GitHub Pages 自动部署
├── rookieblog.py       # 生成器脚本
└── site.json           # 站点配置
```

## 快速开始

```bash
python3 rookieblog.py build
```

生成结果会输出到 `dist/`。

如果你想本地预览：

```bash
python3 rookieblog.py serve --port 8000
```

## 新建文章

```bash
python3 rookieblog.py new "我的第一篇文章"
```

它会在 `content/posts/` 下生成一篇带 front matter 的 Markdown 文件。

## Markdown 元数据

每篇文章顶部都可以写简单的 front matter：

```md
---
title: 我的第一篇文章
date: 2026-04-21
summary: 这是文章摘要。
tags: [Markdown, GitHub Pages, Python]
cover: ../assets/images/local-demo.svg
draft: false
---
```

## 使用本地图片

把图片放进仓库里，然后像普通 Markdown 一样引用即可。

例如：

```md
![封面图](../assets/images/local-demo.svg)
```

构建时会把 `content/` 里的非 Markdown 文件一起复制到 `dist/`，所以：

- `content/assets/` 里的图片会被一起带到输出目录
- 放在文章旁边的本地图片也能一起带过去
- 不需要使用第三方图片托管服务

## GitHub Pages 部署

仓库里已经附带了 `.github/workflows/pages.yml`，推送到 GitHub 后可以直接用 Actions 部署。

推荐步骤：

1. 把这个项目推到 GitHub。
2. 在仓库的 `Settings -> Pages` 中启用 `GitHub Actions` 作为部署来源。
3. 推送到 `main` 分支后，GitHub 会自动构建并发布 `dist/`。

## 文章与页面

- 文章放在 `content/posts/*.md`
- 独立页面放在 `content/pages/*.md`
- 首页会自动按日期倒序展示文章

## 适合这个框架的场景

- 想要一个比重量级 SSG 更简单的个人博客
- 更看重“容易安装和使用”，而不是海量插件生态
- 希望博客内容和图片都直接跟着仓库走

如果你后面想继续扩展，我们也可以把它再往下做成：

- 标签页
- 归档页
- RSS
- 搜索
- 评论系统对接
- 主题切换
