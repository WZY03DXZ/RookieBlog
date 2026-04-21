#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONTENT_DIR = ROOT / "content"
POSTS_DIR = CONTENT_DIR / "posts"
PAGES_DIR = CONTENT_DIR / "pages"
STATIC_DIR = ROOT / "static"
DIST_DIR = ROOT / "dist"
CONFIG_PATH = ROOT / "site.json"

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:", "#")
CODE_RE = re.compile(r"`([^`]+)`")
IMAGE_RE = re.compile(r"!\[(.*?)\]\((\S+?)(?:\s+\"(.*?)\")?\)")
LINK_RE = re.compile(r"\[(.*?)\]\((\S+?)(?:\s+\"(.*?)\")?\)")
STRONG_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
EMPHASIS_RE = re.compile(r"(?<!\*)\*(?=\S)(.+?)(?<=\S)\*(?!\*)|(?<!_)_(?=\S)(.+?)(?<=\S)_(?!_)")
INLINE_PLACEHOLDER_RE = re.compile(r"@@INLINE(\d+)@@")


@dataclass
class SiteConfig:
    title: str = "RookieBlog"
    description: str = "A tiny static blog powered by Markdown."
    tagline: str = "Write with Markdown, publish to GitHub Pages, host images in the repo."
    author: str = "Your Name"
    language: str = "zh-CN"
    site_url: str = ""
    footer: str = "Built with RookieBlog."


@dataclass
class ContentEntry:
    kind: str
    source_path: Path
    title: str
    slug: str
    body_markdown: str
    summary: str
    tags: list[str]
    date: datetime | None = None
    draft: bool = False
    cover: str = ""
    nav: bool = True
    html_body: str = ""
    output_path: Path = Path()
    url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    slug = re.sub(r"[^\w\s-]", "", normalized, flags=re.UNICODE)
    slug = re.sub(r"[-\s]+", "-", slug, flags=re.UNICODE).strip("-_")
    return slug or "post"


def parse_front_matter(raw_text: str) -> tuple[dict[str, Any], str]:
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw_text

    metadata_lines: list[str] = []
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            body = "\n".join(lines[index + 1 :]).lstrip("\n")
            return parse_metadata_block(metadata_lines), body
        metadata_lines.append(lines[index])

    return {}, raw_text


def parse_metadata_block(lines: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue

        key, raw_value = line.split(":", 1)
        metadata[key.strip().lower()] = parse_metadata_value(raw_value.strip())
    return metadata


def parse_metadata_value(value: str) -> Any:
    if not value:
        return ""
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else value
        except json.JSONDecodeError:
            inner = value[1:-1]
            return [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return value


def parse_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return fallback
    text = str(value).strip()
    for parser in (datetime.fromisoformat,):
        try:
            return parser(text)
        except ValueError:
            continue
    for fmt in ("%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return fallback


def ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value).strip()]


def text_excerpt(markdown_text: str, limit: int = 180) -> str:
    cleaned = markdown_text
    cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.S)
    cleaned = re.sub(r"!\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(r"^[#>\-\*\d\.\s]+", "", cleaned, flags=re.M)
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def relative_href(from_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path, from_dir)).as_posix()


def load_config() -> SiteConfig:
    if not CONFIG_PATH.exists():
        return SiteConfig()
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    defaults = SiteConfig()
    values = {
        "title": data.get("title", defaults.title),
        "description": data.get("description", defaults.description),
        "tagline": data.get("tagline", defaults.tagline),
        "author": data.get("author", defaults.author),
        "language": data.get("language", defaults.language),
        "site_url": data.get("site_url", defaults.site_url),
        "footer": data.get("footer", defaults.footer),
    }
    return SiteConfig(**values)


def collect_markdown_entries() -> tuple[list[ContentEntry], list[ContentEntry]]:
    posts = build_entries_from_directory(POSTS_DIR, "post")
    pages = build_entries_from_directory(PAGES_DIR, "page")
    return posts, pages


def build_entries_from_directory(directory: Path, kind: str) -> list[ContentEntry]:
    if not directory.exists():
        return []

    entries: list[ContentEntry] = []
    used_slugs: set[str] = set()

    for source_path in sorted(directory.rglob("*.md")):
        raw_text = source_path.read_text(encoding="utf-8")
        metadata, body_markdown = parse_front_matter(raw_text)
        fallback_date = datetime.fromtimestamp(source_path.stat().st_mtime)
        title = str(metadata.get("title") or source_path.stem.replace("-", " ").replace("_", " ").title())
        slug_seed = str(metadata.get("slug") or source_path.stem)
        slug = make_unique_slug(slugify(slug_seed), used_slugs)
        summary = str(metadata.get("summary") or text_excerpt(body_markdown))
        tags = ensure_list(metadata.get("tags"))
        draft = bool(metadata.get("draft", False))
        cover = str(metadata.get("cover", ""))
        nav = bool(metadata.get("nav", kind == "page"))
        date = parse_datetime(metadata.get("date"), fallback_date) if kind == "post" else None

        entries.append(
            ContentEntry(
                kind=kind,
                source_path=source_path.resolve(),
                title=title,
                slug=slug,
                body_markdown=body_markdown,
                summary=summary,
                tags=tags,
                date=date,
                draft=draft,
                cover=cover,
                nav=nav,
                metadata=metadata,
            )
        )
    return entries


def make_unique_slug(base_slug: str, used_slugs: set[str]) -> str:
    slug = base_slug or "post"
    if slug not in used_slugs:
        used_slugs.add(slug)
        return slug
    index = 2
    while f"{slug}-{index}" in used_slugs:
        index += 1
    final_slug = f"{slug}-{index}"
    used_slugs.add(final_slug)
    return final_slug


def assign_output_paths(posts: list[ContentEntry], pages: list[ContentEntry]) -> None:
    for post in posts:
        post.output_path = DIST_DIR / "posts" / post.slug / "index.html"
        post.url = f"posts/{post.slug}/"
    for page in pages:
        page.output_path = DIST_DIR / page.slug / "index.html"
        page.url = f"{page.slug}/"


def build_markdown_manifest(posts: list[ContentEntry], pages: list[ContentEntry]) -> dict[Path, Path]:
    manifest: dict[Path, Path] = {}
    for entry in posts + pages:
        manifest[entry.source_path] = entry.output_path
    return manifest


class MarkdownRenderer:
    def __init__(self, markdown_manifest: dict[Path, Path]):
        self.markdown_manifest = markdown_manifest
        self.placeholders: list[str] = []

    def render(self, markdown_text: str, source_path: Path, output_path: Path) -> str:
        self.placeholders = []
        page_dir = output_path.parent
        lines = markdown_text.splitlines()
        blocks: list[str] = []
        paragraph: list[str] = []
        index = 0

        def flush_paragraph() -> None:
            if paragraph:
                text = " ".join(item.strip() for item in paragraph if item.strip())
                blocks.append(f"<p>{self.render_inlines(text, source_path, page_dir)}</p>")
                paragraph.clear()

        while index < len(lines):
            line = lines[index].rstrip()
            stripped = line.strip()

            if not stripped:
                flush_paragraph()
                index += 1
                continue

            if stripped.startswith("```"):
                flush_paragraph()
                language = stripped[3:].strip()
                code_lines: list[str] = []
                index += 1
                while index < len(lines) and not lines[index].strip().startswith("```"):
                    code_lines.append(lines[index])
                    index += 1
                if index < len(lines):
                    index += 1
                class_attr = f' class="language-{html.escape(language, quote=True)}"' if language else ""
                code_html = html.escape("\n".join(code_lines))
                blocks.append(f"<pre><code{class_attr}>{code_html}</code></pre>")
                continue

            heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if heading_match:
                flush_paragraph()
                level = len(heading_match.group(1))
                title = self.render_inlines(heading_match.group(2).strip(), source_path, page_dir)
                blocks.append(f"<h{level}>{title}</h{level}>")
                index += 1
                continue

            if re.fullmatch(r"[-*_]{3,}", stripped):
                flush_paragraph()
                blocks.append("<hr />")
                index += 1
                continue

            if stripped.startswith(">"):
                flush_paragraph()
                quote_lines: list[str] = []
                while index < len(lines) and lines[index].strip().startswith(">"):
                    quote_lines.append(lines[index].strip()[1:].lstrip())
                    index += 1
                inner_html = self.render("\n".join(quote_lines), source_path, output_path)
                blocks.append(f"<blockquote>{inner_html}</blockquote>")
                continue

            unordered_match = re.match(r"^\s*[-*+]\s+(.*)$", line)
            ordered_match = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
            if unordered_match or ordered_match:
                flush_paragraph()
                ordered = ordered_match is not None
                blocks.append(self.render_list(lines, index, ordered, source_path, page_dir))
                index = self.next_index
                continue

            paragraph.append(stripped)
            index += 1

        flush_paragraph()
        return "\n".join(blocks)

    def render_list(self, lines: list[str], start_index: int, ordered: bool, source_path: Path, page_dir: Path) -> str:
        index = start_index
        items: list[str] = []
        pattern = r"^\s*\d+\.\s+(.*)$" if ordered else r"^\s*[-*+]\s+(.*)$"

        while index < len(lines):
            line = lines[index]
            match = re.match(pattern, line)
            if not match:
                break

            item_lines = [match.group(1).strip()]
            index += 1
            while index < len(lines):
                continuation = lines[index]
                if not continuation.strip():
                    break
                if re.match(r"^\s{2,}.+", continuation):
                    item_lines.append(continuation.strip())
                    index += 1
                    continue
                if re.match(pattern, continuation):
                    break
                break
            items.append(f"<li>{self.render_inlines(' '.join(item_lines), source_path, page_dir)}</li>")

            if index < len(lines) and not lines[index].strip():
                index += 1
                break

        self.next_index = index
        tag = "ol" if ordered else "ul"
        return f"<{tag}>\n{''.join(items)}\n</{tag}>"

    next_index: int = 0

    def render_inlines(self, text: str, source_path: Path, page_dir: Path) -> str:
        working = CODE_RE.sub(lambda match: self.stash(f"<code>{html.escape(match.group(1))}</code>"), text)
        working = IMAGE_RE.sub(lambda match: self.render_image(match, source_path, page_dir), working)
        working = LINK_RE.sub(lambda match: self.render_link(match, source_path, page_dir), working)
        working = html.escape(working, quote=False)
        working = STRONG_RE.sub(lambda match: f"<strong>{match.group(2)}</strong>", working)
        working = EMPHASIS_RE.sub(lambda match: f"<em>{match.group(1) or match.group(2)}</em>", working)
        return self.restore(working)

    def render_image(self, match: re.Match[str], source_path: Path, page_dir: Path) -> str:
        alt_text = html.escape(match.group(1), quote=True)
        destination = html.escape(self.resolve_url(match.group(2), source_path, page_dir), quote=True)
        title = match.group(3)
        title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
        return self.stash(f'<img src="{destination}" alt="{alt_text}" loading="lazy"{title_attr} />')

    def render_link(self, match: re.Match[str], source_path: Path, page_dir: Path) -> str:
        label = self.restore(html.escape(match.group(1), quote=False))
        destination = html.escape(self.resolve_url(match.group(2), source_path, page_dir), quote=True)
        title = match.group(3)
        title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
        return self.stash(f'<a href="{destination}"{title_attr}>{label}</a>')

    def stash(self, rendered_html: str) -> str:
        token = f"@@INLINE{len(self.placeholders)}@@"
        self.placeholders.append(rendered_html)
        return token

    def restore(self, text: str) -> str:
        def replacer(match: re.Match[str]) -> str:
            index = int(match.group(1))
            return self.placeholders[index]

        return INLINE_PLACEHOLDER_RE.sub(replacer, text)

    def resolve_url(self, raw_url: str, source_path: Path, page_dir: Path) -> str:
        if raw_url.startswith(EXTERNAL_PREFIXES):
            return raw_url

        fragment = ""
        clean_url = raw_url
        if "#" in raw_url:
            clean_url, fragment = raw_url.split("#", 1)
            fragment = f"#{fragment}"

        query = ""
        if "?" in clean_url:
            clean_url, query = clean_url.split("?", 1)
            query = f"?{query}"

        resolved = self.resolve_source_path(clean_url, source_path)
        if not resolved:
            return raw_url

        if resolved.suffix.lower() == ".md" and resolved in self.markdown_manifest:
            target_path = self.markdown_manifest[resolved]
            return relative_href(page_dir, target_path) + query + fragment

        content_target = target_path_for_content_asset(resolved)
        if content_target:
            return relative_href(page_dir, content_target) + query + fragment

        static_target = target_path_for_static_asset(resolved)
        if static_target:
            return relative_href(page_dir, static_target) + query + fragment

        return raw_url

    def resolve_source_path(self, raw_url: str, source_path: Path) -> Path | None:
        if not raw_url:
            return None

        if raw_url.startswith("/"):
            candidates = [
                CONTENT_DIR / raw_url.lstrip("/"),
                STATIC_DIR / raw_url.lstrip("/"),
                ROOT / raw_url.lstrip("/"),
            ]
            for candidate in candidates:
                if candidate.exists():
                    return candidate.resolve()
            return None

        candidate = (source_path.parent / raw_url).resolve()
        if candidate.exists():
            return candidate
        return None


def target_path_for_content_asset(source_path: Path) -> Path | None:
    try:
        relative = source_path.relative_to(CONTENT_DIR.resolve())
    except ValueError:
        return None
    return DIST_DIR / relative


def target_path_for_static_asset(source_path: Path) -> Path | None:
    try:
        relative = source_path.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return None
    return DIST_DIR / relative


def copy_tree_contents(source: Path, target: Path, ignore_markdown: bool = False) -> None:
    if not source.exists():
        return

    for path in source.rglob("*"):
        if path.is_dir():
            continue
        if ignore_markdown and path.suffix.lower() == ".md":
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def render_navigation(config: SiteConfig, pages: list[ContentEntry], page_dir: Path) -> str:
    links = [f'<a href="{relative_href(page_dir, DIST_DIR / "index.html")}">首页</a>']
    visible_pages = [page for page in pages if page.nav and not page.draft]
    for page in visible_pages:
        links.append(f'<a href="{relative_href(page_dir, page.output_path)}">{html.escape(page.title)}</a>')
    return "\n".join(links)


def wrap_layout(
    *,
    config: SiteConfig,
    title: str,
    description: str,
    content_html: str,
    pages: list[ContentEntry],
    page_dir: Path,
    page_class: str,
) -> str:
    stylesheet_href = relative_href(page_dir, DIST_DIR / "style.css")
    favicon_href = relative_href(page_dir, DIST_DIR / "favicon.svg")
    home_href = relative_href(page_dir, DIST_DIR / "index.html")
    navigation = render_navigation(config, pages, page_dir)
    page_title = f"{title} | {config.title}" if title != config.title else config.title
    return f"""<!DOCTYPE html>
<html lang="{html.escape(config.language, quote=True)}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(page_title)}</title>
  <meta name="description" content="{html.escape(description, quote=True)}" />
  <link rel="icon" href="{favicon_href}" type="image/svg+xml" />
  <link rel="stylesheet" href="{stylesheet_href}" />
</head>
<body class="{html.escape(page_class, quote=True)}">
  <header class="site-header">
    <div class="shell site-header__inner">
      <a class="brand" href="{home_href}">{html.escape(config.title)}</a>
      <nav class="nav-links">
        {navigation}
      </nav>
    </div>
  </header>
  <main class="shell page-shell">
    {content_html}
  </main>
  <footer class="site-footer">
    <div class="shell site-footer__inner">
      <p>{html.escape(config.footer)}</p>
    </div>
  </footer>
</body>
</html>
"""


def render_post_page(post: ContentEntry, config: SiteConfig, pages: list[ContentEntry]) -> str:
    page_dir = post.output_path.parent
    tag_html = "".join(f'<li class="tag-chip">{html.escape(tag)}</li>' for tag in post.tags)
    cover_html = ""
    if post.cover:
        renderer = MarkdownRenderer({})
        cover_url = renderer.resolve_url(post.cover, post.source_path, page_dir)
        cover_html = (
            '<figure class="post-cover">'
            f'<img src="{html.escape(cover_url, quote=True)}" alt="{html.escape(post.title, quote=True)}" loading="lazy" />'
            "</figure>"
        )
    post_content = f"""
<article class="article-card">
  <div class="eyebrow">博客文章</div>
  <h1>{html.escape(post.title)}</h1>
  <p class="article-lead">{html.escape(post.summary)}</p>
  <div class="meta-row">
    <span>{post.date.strftime("%Y-%m-%d") if post.date else ""}</span>
    <span>{html.escape(config.author)}</span>
  </div>
  {cover_html}
  <div class="markdown-content">
    {post.html_body}
  </div>
  <ul class="tag-list">{tag_html}</ul>
</article>
"""
    return wrap_layout(
        config=config,
        title=post.title,
        description=post.summary or config.description,
        content_html=post_content,
        pages=pages,
        page_dir=page_dir,
        page_class="post-page",
    )


def render_page_page(page: ContentEntry, config: SiteConfig, pages: list[ContentEntry]) -> str:
    page_dir = page.output_path.parent
    content_html = f"""
<article class="article-card">
  <div class="eyebrow">独立页面</div>
  <h1>{html.escape(page.title)}</h1>
  <p class="article-lead">{html.escape(page.summary or config.description)}</p>
  <div class="markdown-content">
    {page.html_body}
  </div>
</article>
"""
    return wrap_layout(
        config=config,
        title=page.title,
        description=page.summary or config.description,
        content_html=content_html,
        pages=pages,
        page_dir=page_dir,
        page_class="page-page",
    )


def render_home_page(posts: list[ContentEntry], config: SiteConfig, pages: list[ContentEntry]) -> str:
    page_dir = DIST_DIR
    cards = []
    for post in posts:
        if post.draft:
            continue
        cover = ""
        if post.cover:
            renderer = MarkdownRenderer({})
            cover_url = renderer.resolve_url(post.cover, post.source_path, page_dir)
            cover = f'<img class="post-card__cover" src="{html.escape(cover_url, quote=True)}" alt="{html.escape(post.title, quote=True)}" loading="lazy" />'
        tags = "".join(f'<li class="tag-chip">{html.escape(tag)}</li>' for tag in post.tags)
        cards.append(
            f"""
<article class="post-card">
  {cover}
  <div class="post-card__body">
    <div class="eyebrow">{post.date.strftime("%Y-%m-%d") if post.date else "文章"}</div>
    <h2><a href="{html.escape(post.url, quote=True)}">{html.escape(post.title)}</a></h2>
    <p>{html.escape(post.summary)}</p>
    <ul class="tag-list">{tags}</ul>
  </div>
</article>
"""
        )

    cards_html = "\n".join(cards) if cards else '<p class="empty-state">还没有文章，先执行一次 `python3 rookieblog.py new "第一篇文章"` 吧。</p>'
    content_html = f"""
<section class="hero-card">
  <div class="hero-copy">
    <div class="eyebrow">轻量静态博客</div>
    <h1>{html.escape(config.title)}</h1>
    <p class="hero-lead">{html.escape(config.tagline)}</p>
    <p class="hero-text">{html.escape(config.description)}</p>
  </div>
  <div class="hero-note">
    <p>无第三方依赖</p>
    <p>Markdown 原生写作</p>
    <p>图片直接放仓库</p>
    <p>GitHub Pages 可直接部署</p>
  </div>
</section>
<section class="section-heading">
  <div>
    <div class="eyebrow">最新内容</div>
    <h2>文章列表</h2>
  </div>
</section>
<section class="post-grid">
  {cards_html}
</section>
"""
    return wrap_layout(
        config=config,
        title=config.title,
        description=config.description,
        content_html=content_html,
        pages=pages,
        page_dir=page_dir,
        page_class="home-page",
    )


def build_site() -> None:
    config = load_config()
    posts, pages = collect_markdown_entries()
    assign_output_paths(posts, pages)
    markdown_manifest = build_markdown_manifest(posts, pages)
    renderer = MarkdownRenderer(markdown_manifest)

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    copy_tree_contents(STATIC_DIR, DIST_DIR)
    copy_tree_contents(CONTENT_DIR, DIST_DIR, ignore_markdown=True)

    for entry in posts + pages:
        if entry.draft:
            continue
        entry.html_body = renderer.render(entry.body_markdown, entry.source_path, entry.output_path)
        entry.output_path.parent.mkdir(parents=True, exist_ok=True)

    published_posts = sorted([post for post in posts if not post.draft], key=lambda item: item.date or datetime.min, reverse=True)
    published_pages = [page for page in pages if not page.draft]

    for post in published_posts:
        post.output_path.write_text(render_post_page(post, config, published_pages), encoding="utf-8")

    for page in published_pages:
        page.output_path.write_text(render_page_page(page, config, published_pages), encoding="utf-8")

    (DIST_DIR / "index.html").write_text(render_home_page(published_posts, config, published_pages), encoding="utf-8")
    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")


def create_new_post(title: str) -> Path:
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(title)
    target = POSTS_DIR / f"{slug}.md"
    if target.exists():
        timestamp = datetime.now().strftime("%H%M%S")
        target = POSTS_DIR / f"{slug}-{timestamp}.md"
    today = datetime.now().strftime("%Y-%m-%d")
    template = f"""---
title: {title}
date: {today}
summary: 用一句话介绍这篇文章。
tags: [Markdown, GitHub Pages]
cover: ../assets/images/local-demo.svg
---

# {title}

在这里开始写正文。

## 可以直接插入本地图片

![示例图片](../assets/images/local-demo.svg)

## 也可以放代码块

```bash
python3 rookieblog.py build
```
"""
    target.write_text(template, encoding="utf-8")
    return target


def serve_site(port: int) -> None:
    build_site()
    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(*args, directory=str(DIST_DIR), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Serving RookieBlog at http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A tiny dependency-free static blog generator.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("build", help="Generate the static site into dist/.")

    new_parser = subparsers.add_parser("new", help="Create a new Markdown post.")
    new_parser.add_argument("title", help="Title of the new post.")

    serve_parser = subparsers.add_parser("serve", help="Build and serve the site locally.")
    serve_parser.add_argument("--port", type=int, default=8000, help="Local port. Default: 8000.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "build":
        build_site()
        print(f"Site generated in {DIST_DIR}")
        return

    if args.command == "new":
        created = create_new_post(args.title)
        print(f"Created {created}")
        return

    if args.command == "serve":
        serve_site(args.port)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
