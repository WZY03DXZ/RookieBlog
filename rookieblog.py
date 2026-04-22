#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONTENT_DIR = ROOT / "content"
POSTS_DIR = CONTENT_DIR / "posts"
PAGES_DIR = CONTENT_DIR / "pages"
STATIC_DIR = ROOT / "static"
THEMES_DIR = ROOT / "themes"
DIST_DIR = ROOT / "dist"
THEME_DIST_DIR = DIST_DIR / "_theme"
DOCS_DIR = ROOT / "docs"
CONFIG_PATH = ROOT / "site.json"

DEFAULT_THEME = "default"
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:", "#")
CODE_RE = re.compile(r"`([^`]+)`")
IMAGE_RE = re.compile(r"!\[(.*?)\]\((\S+?)(?:\s+\"(.*?)\")?\)")
LINK_RE = re.compile(r"\[(.*?)\]\((\S+?)(?:\s+\"(.*?)\")?\)")
STRONG_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
EMPHASIS_RE = re.compile(r"(?<!\*)\*(?=\S)(.+?)(?<=\S)\*(?!\*)|(?<!_)_(?=\S)(.+?)(?<=\S)_(?!_)")
INLINE_PLACEHOLDER_RE = re.compile(r"@@INLINE(\d+)@@")
TEMPLATE_TOKEN_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")
WORD_NAMESPACE = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass
class SiteConfig:
    title: str = "RookieBlog"
    description: str = "A tiny static blog powered by Markdown."
    tagline: str = "Write with Markdown, publish to GitHub Pages, host images in the repo."
    author: str = "Your Name"
    language: str = "zh-CN"
    site_url: str = ""
    footer: str = "Built with RookieBlog."
    theme: str = DEFAULT_THEME
    search_enabled: bool = True
    comments: dict[str, Any] = field(default_factory=dict)


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
    plain_text: str = ""
    output_path: Path = Path()
    url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportedDocument:
    title: str
    markdown_body: str
    asset_paths: list[Path] = field(default_factory=list)


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


def strip_front_matter(raw_text: str) -> str:
    _, body = parse_front_matter(raw_text)
    return body


def markdown_to_plain_text(markdown_text: str) -> str:
    cleaned = markdown_text
    cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.S)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"!\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(r"^[#>\-\*\d\.\s]+", "", cleaned, flags=re.M)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def text_excerpt(markdown_text: str, limit: int = 180) -> str:
    cleaned = markdown_to_plain_text(markdown_text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def relative_href(from_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path, from_dir)).as_posix()


def normalize_paragraphs(text: str) -> str:
    paragraphs = [re.sub(r"\s+", " ", block).strip() for block in re.split(r"\n\s*\n", text)]
    return "\n\n".join(block for block in paragraphs if block)


def normalize_content_subdir(folder: str | None) -> Path:
    if not folder:
        return Path()
    raw = folder.replace("\\", "/").strip().strip("/")
    if not raw:
        return Path()
    subdir = Path(raw)
    if subdir.is_absolute() or ".." in subdir.parts:
        raise ValueError("folder 只能是内容目录下的相对路径，不能使用绝对路径或 `..`。")
    return subdir


def relative_markdown_asset_path(markdown_dir: Path, asset_path: Path) -> str:
    return Path(os.path.relpath(asset_path, markdown_dir)).as_posix()


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
        "theme": data.get("theme", defaults.theme),
        "search_enabled": bool(data.get("search_enabled", defaults.search_enabled)),
        "comments": data.get("comments", {}),
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
                plain_text=markdown_to_plain_text(body_markdown),
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
            return self.placeholders[int(match.group(1))]

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
            candidates = [CONTENT_DIR / raw_url.lstrip("/"), STATIC_DIR / raw_url.lstrip("/"), ROOT / raw_url.lstrip("/")]
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


def get_theme_dir(theme_name: str) -> Path:
    theme_dir = THEMES_DIR / theme_name
    if (theme_dir / "templates").exists():
        return theme_dir
    return THEMES_DIR / DEFAULT_THEME


def get_theme_template(theme_name: str, template_name: str) -> str:
    theme_dir = get_theme_dir(theme_name)
    candidates = [theme_dir / "templates" / template_name]
    if theme_dir.name != DEFAULT_THEME:
        candidates.append(THEMES_DIR / DEFAULT_THEME / "templates" / template_name)
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Missing template: {template_name}")


def render_template(template_text: str, context: dict[str, Any]) -> str:
    return TEMPLATE_TOKEN_RE.sub(lambda match: str(context.get(match.group(1), "")), template_text)


def copy_theme_assets(theme_name: str) -> None:
    default_assets = THEMES_DIR / DEFAULT_THEME / "assets"
    copy_tree_contents(default_assets, THEME_DIST_DIR)
    if theme_name != DEFAULT_THEME:
        custom_assets = THEMES_DIR / theme_name / "assets"
        copy_tree_contents(custom_assets, THEME_DIST_DIR)


def theme_asset_href(page_dir: Path, asset_name: str) -> str:
    return relative_href(page_dir, THEME_DIST_DIR / asset_name)


def render_navigation(config: SiteConfig, pages: list[ContentEntry], page_dir: Path, current_nav: str = "") -> str:
    def nav_link(label: str, href: str, nav_key: str) -> str:
        active = ' class="is-active" aria-current="page"' if current_nav == nav_key else ""
        return f'<a href="{href}"{active}>{label}</a>'

    links = [
        nav_link("首页", relative_href(page_dir, DIST_DIR / "index.html"), "home"),
        nav_link("文章", relative_href(page_dir, DIST_DIR / "articles" / "index.html"), "articles"),
    ]
    visible_pages = [page for page in pages if page.nav and not page.draft]
    for page in visible_pages:
        links.append(
            nav_link(html.escape(page.title), relative_href(page_dir, page.output_path), f"page:{page.slug}")
        )
    return "\n".join(links)


def render_header_search(config: SiteConfig, page_dir: Path) -> str:
    if not config.search_enabled:
        return ""
    search_action = html.escape(relative_href(page_dir, DIST_DIR / "search" / "index.html"), quote=True)
    return (
        f'<form class="header-search" action="{search_action}" method="get">'
        '<label class="header-search__label" for="global-search-input">全站搜索</label>'
        '<input id="global-search-input" class="header-search__input" type="search" name="q" '
        'placeholder="搜索文章..." />'
        '<button class="header-search__submit" type="submit">搜索</button>'
        "</form>"
    )


def render_theme_init_script() -> str:
    return (
        "<script>"
        "(function(){"
        "var theme='light';"
        "try{"
        "var saved=window.localStorage.getItem('rookieblog-theme');"
        "if(saved==='light'||saved==='dark'){theme=saved;}"
        "else if(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches){theme='dark';}"
        "}catch(error){"
        "if(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches){theme='dark';}"
        "}"
        "document.documentElement.dataset.theme=theme;"
        "})();"
        "</script>"
    )


def render_comments_block(config: SiteConfig, post: ContentEntry) -> str:
    comments = config.comments or {}
    provider = str(comments.get("provider", "")).strip().lower()
    if not provider:
        return ""

    wrapper_start = '<section class="comments-card"><div class="eyebrow">评论</div><h2>交流与反馈</h2>'
    wrapper_end = "</section>"

    if provider == "giscus":
        required = ["repo", "repo_id", "category", "category_id"]
        if not all(comments.get(key) for key in required):
            return wrapper_start + "<p>已启用 Giscus，但站点配置里的评论字段还没有填写完整。</p>" + wrapper_end
        attrs = {
            "src": "https://giscus.app/client.js",
            "data-repo": comments["repo"],
            "data-repo-id": comments["repo_id"],
            "data-category": comments["category"],
            "data-category-id": comments["category_id"],
            "data-mapping": comments.get("mapping", "pathname"),
            "data-strict": str(comments.get("strict", "0")),
            "data-reactions-enabled": str(comments.get("reactions_enabled", "1")),
            "data-emit-metadata": comments.get("emit_metadata", "0"),
            "data-input-position": comments.get("input_position", "top"),
            "data-theme": comments.get("theme", "preferred_color_scheme"),
            "data-lang": comments.get("lang", config.language),
            "crossorigin": "anonymous",
            "async": "async",
        }
        attr_html = " ".join(f'{key}="{html.escape(str(value), quote=True)}"' for key, value in attrs.items())
        return wrapper_start + f'<div class="comments-embed"><script {attr_html}></script></div>' + wrapper_end

    if provider == "utterances":
        if not comments.get("repo"):
            return wrapper_start + "<p>已启用 Utterances，但 `comments.repo` 还没有配置。</p>" + wrapper_end
        attrs = {
            "src": "https://utteranc.es/client.js",
            "repo": comments["repo"],
            "issue-term": comments.get("issue_term", "pathname"),
            "theme": comments.get("theme", "github-light"),
            "label": comments.get("label", "comment"),
            "crossorigin": "anonymous",
            "async": "async",
        }
        attr_html = " ".join(f'{key}="{html.escape(str(value), quote=True)}"' for key, value in attrs.items())
        return wrapper_start + f'<div class="comments-embed"><script {attr_html}></script></div>' + wrapper_end

    return wrapper_start + f"<p>暂不支持的评论提供方：{html.escape(provider)}</p>" + wrapper_end


def wrap_layout(
    *,
    config: SiteConfig,
    title: str,
    description: str,
    content_html: str,
    pages: list[ContentEntry],
    page_dir: Path,
    page_class: str,
    current_nav: str = "",
    extra_head_html: str = "",
    extra_body_html: str = "",
) -> str:
    template_text = get_theme_template(config.theme, "base.html")
    page_title = f"{title} | {config.title}" if title != config.title else config.title
    context = {
        "language": html.escape(config.language, quote=True),
        "page_title": html.escape(page_title),
        "meta_description": html.escape(description or config.description, quote=True),
        "favicon_href": relative_href(page_dir, DIST_DIR / "favicon.svg"),
        "theme_init_html": render_theme_init_script(),
        "theme_css_href": theme_asset_href(page_dir, "theme.css"),
        "theme_toggle_js_href": theme_asset_href(page_dir, "theme-toggle.js"),
        "home_href": relative_href(page_dir, DIST_DIR / "index.html"),
        "site_title": html.escape(config.title),
        "navigation_html": render_navigation(config, pages, page_dir, current_nav),
        "header_search_html": render_header_search(config, page_dir),
        "content_html": content_html,
        "footer_html": html.escape(config.footer),
        "page_class": html.escape(page_class, quote=True),
        "extra_head_html": extra_head_html,
        "extra_body_html": extra_body_html,
    }
    return render_template(template_text, context)


def render_tags(tags: list[str]) -> str:
    if not tags:
        return ""
    tag_html = "".join(f'<li class="tag-chip">{html.escape(tag)}</li>' for tag in tags)
    return f'<ul class="tag-list">{tag_html}</ul>'


def render_cover(post: ContentEntry, page_dir: Path) -> str:
    if not post.cover:
        return ""
    renderer = MarkdownRenderer({})
    cover_url = renderer.resolve_url(post.cover, post.source_path, page_dir)
    return (
        '<figure class="post-cover">'
        f'<img src="{html.escape(cover_url, quote=True)}" alt="{html.escape(post.title, quote=True)}" loading="lazy" />'
        "</figure>"
    )


def extract_heading_outline(markdown_text: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for raw_line in markdown_text.splitlines():
        stripped = raw_line.strip()
        match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if not match:
            continue
        title = markdown_to_plain_text(match.group(2).strip())
        if not title:
            continue
        headings.append(
            {
                "level": len(match.group(1)),
                "title": title,
                "id": make_unique_slug(slugify(title), used_ids),
            }
        )
    return headings


def inject_heading_ids(html_body: str, headings: list[dict[str, Any]]) -> str:
    index = 0

    def replacer(match: re.Match[str]) -> str:
        nonlocal index
        if index >= len(headings):
            return match.group(0)
        heading = headings[index]
        index += 1
        level = match.group(1)
        return f'<h{level} id="{html.escape(str(heading["id"]), quote=True)}">{match.group(2)}</h{level}>'

    return re.sub(r"<h([1-6])>(.*?)</h\1>", replacer, html_body, flags=re.S)


def render_post_outline(post: ContentEntry, headings: list[dict[str, Any]]) -> str:
    outline_headings = [item for item in headings if item["level"] >= 2]
    if not outline_headings and headings:
        outline_headings = [
            item for item in headings if not (item["level"] == 1 and item["title"].strip() == post.title.strip())
        ] or headings[:1]

    if not outline_headings:
        return (
            '<section class="sidebar-card">'
            '<div class="eyebrow">文章大纲</div>'
            "<h2>目录</h2>"
            '<p class="sidebar-empty">这篇文章还没有可展示的小节标题。</p>'
            "</section>"
        )

    base_level = min(int(item["level"]) for item in outline_headings)
    items: list[str] = []
    for item in outline_headings:
        offset = max(0, min(int(item["level"]) - base_level, 3))
        items.append(
            '<li class="toc-item toc-item--level-{level}">'
            '<a href="#{anchor}">{title}</a>'
            "</li>".format(
                level=offset,
                anchor=html.escape(str(item["id"]), quote=True),
                title=html.escape(str(item["title"])),
            )
        )
    return (
        '<section class="sidebar-card">'
        '<div class="eyebrow">文章大纲</div>'
        "<h2>目录</h2>"
        f'<ul class="toc-list">{"".join(items)}</ul>'
        "</section>"
    )


def entry_topics(entry: ContentEntry) -> list[str]:
    categories = ensure_list(entry.metadata.get("categories"))
    if not categories and entry.metadata.get("category"):
        categories = ensure_list(entry.metadata.get("category"))
    return categories or entry.tags


def entry_folder_group(entry: ContentEntry) -> str:
    try:
        relative_parent = entry.source_path.relative_to(POSTS_DIR.resolve()).parent
    except ValueError:
        return ""
    if str(relative_parent) == ".":
        return ""
    return relative_parent.as_posix()


def entry_primary_category(entry: ContentEntry) -> str:
    folder_group = entry_folder_group(entry)
    if folder_group:
        return folder_group
    categories = ensure_list(entry.metadata.get("categories"))
    if not categories and entry.metadata.get("category"):
        categories = ensure_list(entry.metadata.get("category"))
    return categories[0] if categories else ""


def category_label(category_key: str) -> str:
    if not category_key:
        return "未分类"
    normalized = category_key.replace("\\", "/").strip("/")
    return " / ".join(part for part in normalized.split("/") if part) or "未分类"


def category_slug(category_key: str) -> str:
    if not category_key:
        return "uncategorized"
    normalized = category_key.replace("\\", "/").strip("/")
    parts = [slugify(part) or "item" for part in normalized.split("/") if part]
    return "--".join(parts) or "uncategorized"


def category_output_path(category_key: str) -> Path:
    return DIST_DIR / "categories" / category_slug(category_key) / "index.html"


def collect_post_categories(posts: list[ContentEntry]) -> list[dict[str, Any]]:
    grouped: dict[str, list[ContentEntry]] = {}
    for post in posts:
        if post.draft:
            continue
        grouped.setdefault(entry_primary_category(post), []).append(post)

    categories: list[dict[str, Any]] = []
    for key, items in grouped.items():
        sorted_items = sorted(items, key=lambda item: item.date or datetime.min, reverse=True)
        categories.append(
            {
                "key": key,
                "label": category_label(key),
                "output_path": category_output_path(key),
                "posts": sorted_items,
            }
        )

    categories.sort(key=lambda item: (item["key"] == "", str(item["label"]).casefold()))
    return categories


def find_related_posts(current_post: ContentEntry, posts: list[ContentEntry], limit: int = 4) -> list[ContentEntry]:
    current_category = entry_primary_category(current_post)
    related_posts = [
        candidate
        for candidate in posts
        if not candidate.draft
        and candidate.slug != current_post.slug
        and entry_primary_category(candidate) == current_category
    ]
    related_posts.sort(key=lambda item: item.date or datetime.min, reverse=True)
    return related_posts[:limit]


def render_related_posts(post: ContentEntry, posts: list[ContentEntry], page_dir: Path) -> str:
    category_key = entry_primary_category(post)
    category_name = category_label(category_key)
    category_href = html.escape(relative_href(page_dir, category_output_path(category_key)), quote=True)
    related_posts = find_related_posts(post, posts)
    if not related_posts:
        return (
            '<section class="sidebar-card">'
            '<div class="eyebrow">同分类文章</div>'
            '<div class="sidebar-card__head">'
            f"<h2>{html.escape(category_name)}</h2>"
            f'<a class="sidebar-card__action" href="{category_href}">查看分类</a>'
            "</div>"
            '<p class="sidebar-empty">这个分类下暂时只有这一篇文章。</p>'
            "</section>"
        )

    items: list[str] = []
    for candidate in related_posts:
        date_text = candidate.date.strftime("%Y-%m-%d") if candidate.date else "文章"
        items.append(
            '<li class="related-item">'
            '<a class="related-item__title" href="{href}">{title}</a>'
            '<p class="related-item__summary">{summary}</p>'
            '<div class="related-item__meta">'
            '<span>{date}</span>'
            '<span class="related-item__tag">{category}</span>'
            "</div>"
            "</li>".format(
                href=html.escape(relative_href(page_dir, candidate.output_path), quote=True),
                title=html.escape(candidate.title),
                summary=html.escape(candidate.summary),
                date=html.escape(date_text),
                category=html.escape(category_name),
            )
        )
    return (
        '<section class="sidebar-card">'
        '<div class="eyebrow">同分类文章</div>'
        '<div class="sidebar-card__head">'
        f"<h2>{html.escape(category_name)}</h2>"
        f'<a class="sidebar-card__action" href="{category_href}">查看分类</a>'
        "</div>"
        f'<ul class="related-list">{"".join(items)}</ul>'
        "</section>"
    )


def render_post_page(post: ContentEntry, config: SiteConfig, posts: list[ContentEntry], pages: list[ContentEntry]) -> str:
    page_dir = post.output_path.parent
    headings = extract_heading_outline(post.body_markdown)
    body_html = inject_heading_ids(post.html_body, headings)
    content_template = get_theme_template(config.theme, "post.html")
    content_html = render_template(
        content_template,
        {
            "body_html": body_html,
            "post_outline_html": render_post_outline(post, headings),
            "related_posts_html": render_related_posts(post, posts, page_dir),
            "comments_html": render_comments_block(config, post),
        },
    )
    return wrap_layout(
        config=config,
        title=post.title,
        description=post.summary or config.description,
        content_html=content_html,
        pages=pages,
        page_dir=page_dir,
        page_class="post-page",
        current_nav="articles",
    )


def render_page_page(page: ContentEntry, config: SiteConfig, pages: list[ContentEntry]) -> str:
    page_dir = page.output_path.parent
    content_template = get_theme_template(config.theme, "page.html")
    has_markdown_h1 = bool(re.match(r"\s*<h1\b", page.html_body))
    page_header_html = ""
    if not has_markdown_h1:
        page_header_html = (
            '<div class="eyebrow">独立页面</div>'
            f"<h1>{html.escape(page.title)}</h1>"
            f'<p class="article-lead">{html.escape(page.summary or config.description)}</p>'
        )
    content_html = render_template(
        content_template,
        {
            "page_header_html": page_header_html,
            "body_html": page.html_body,
        },
    )
    return wrap_layout(
        config=config,
        title=page.title,
        description=page.summary or config.description,
        content_html=content_html,
        pages=pages,
        page_dir=page_dir,
        page_class="page-page",
        current_nav=f"page:{page.slug}",
    )


def render_post_cards(posts: list[ContentEntry], page_dir: Path, theme_name: str) -> str:
    cards: list[str] = []
    for post in posts:
        if post.draft:
            continue
        cover = ""
        if post.cover:
            renderer = MarkdownRenderer({})
            cover_url = renderer.resolve_url(post.cover, post.source_path, page_dir)
            cover = f'<img class="post-card__cover" src="{html.escape(cover_url, quote=True)}" alt="{html.escape(post.title, quote=True)}" loading="lazy" />'
        cards.append(
            render_template(
                get_theme_template(theme_name, "post_card.html"),
                {
                    "card_cover_html": cover,
                    "card_date": post.date.strftime("%Y-%m-%d") if post.date else "文章",
                    "card_href": html.escape(relative_href(page_dir, post.output_path), quote=True),
                    "card_title": html.escape(post.title),
                    "card_summary": html.escape(post.summary),
                    "card_tags_html": render_tags(post.tags),
                },
            )
        )
    return "\n".join(cards) if cards else '<p class="empty-state">还没有文章，先执行一次 <code>python3 rookieblog.py new "第一篇文章"</code> 吧。</p>'


def render_category_nav(categories: list[dict[str, Any]], page_dir: Path, current_key: str | None = None) -> str:
    items = [
        '<li><a class="category-nav__pill{active}" href="{href}">'
        '<span>全部文章</span>'
        "</a></li>".format(
            active=" is-active" if current_key is None else "",
            href=html.escape(relative_href(page_dir, DIST_DIR / "articles" / "index.html"), quote=True),
        )
    ]
    for category in categories:
        items.append(
            '<li><a class="category-nav__pill{active}" href="{href}">'
            "<span>{label}</span>"
            '<span class="category-nav__count">{count}</span>'
            "</a></li>".format(
                active=" is-active" if current_key is not None and category["key"] == current_key else "",
                href=html.escape(relative_href(page_dir, category["output_path"]), quote=True),
                label=html.escape(str(category["label"])),
                count=len(category["posts"]),
            )
        )
    return (
        '<section class="category-nav">'
        '<div class="eyebrow">文章导航</div>'
        '<ul class="category-nav__list">'
        f'{"".join(items)}'
        "</ul>"
        "</section>"
    )


def render_home_page(posts: list[ContentEntry], config: SiteConfig, pages: list[ContentEntry], categories: list[dict[str, Any]]) -> str:
    page_dir = DIST_DIR
    home_posts = [
        post
        for post in posts
        if entry_primary_category(post).strip().casefold() in {"others", "other"}
    ]
    cards_html = render_post_cards(home_posts, page_dir, config.theme)
    content_html = render_template(
        get_theme_template(config.theme, "home.html"),
        {
            "site_title": html.escape(config.title),
            "site_tagline": html.escape(config.tagline),
            "site_description": html.escape(config.description),
            "post_cards_html": cards_html,
        },
    )
    return wrap_layout(
        config=config,
        title=config.title,
        description=config.description,
        content_html=content_html,
        pages=pages,
        page_dir=page_dir,
        page_class="home-page",
        current_nav="home",
    )


def render_articles_page(categories: list[dict[str, Any]], posts: list[ContentEntry], config: SiteConfig, pages: list[ContentEntry]) -> str:
    page_dir = DIST_DIR / "articles"
    cards_html = render_post_cards(posts, page_dir, config.theme)
    content_html = render_template(
        get_theme_template(config.theme, "articles.html"),
        {
            "articles_title": "全部文章",
            "articles_description": html.escape("按分类浏览全部文章，点击卡片即可进入具体内容。"),
            "category_nav_html": render_category_nav(categories, page_dir),
            "post_cards_html": cards_html,
        },
    )
    return wrap_layout(
        config=config,
        title="文章",
        description=f"浏览 {config.title} 的全部文章。",
        content_html=content_html,
        pages=pages,
        page_dir=page_dir,
        page_class="articles-page",
        current_nav="articles",
    )


def render_category_page(
    category: dict[str, Any], categories: list[dict[str, Any]], config: SiteConfig, pages: list[ContentEntry]
) -> str:
    page_dir = Path(category["output_path"]).parent
    cards_html = render_post_cards(list(category["posts"]), page_dir, config.theme)
    content_html = render_template(
        get_theme_template(config.theme, "category.html"),
        {
            "category_title": html.escape(str(category["label"])),
            "category_description": html.escape(f"查看 {category['label']} 分类下的全部文章。"),
            "category_nav_html": render_category_nav(categories, page_dir, str(category["key"])),
            "post_cards_html": cards_html,
        },
    )
    return wrap_layout(
        config=config,
        title=f"{category['label']} 分类",
        description=f"查看 {category['label']} 分类下的全部文章。",
        content_html=content_html,
        pages=pages,
        page_dir=page_dir,
        page_class="category-page",
        current_nav="articles",
    )


def render_search_page(config: SiteConfig, pages: list[ContentEntry]) -> str:
    page_dir = DIST_DIR / "search"
    content_html = render_template(
        get_theme_template(config.theme, "search.html"),
        {
            "search_index_href": html.escape(relative_href(page_dir, DIST_DIR / "search-index.json"), quote=True),
            "site_title": html.escape(config.title),
        },
    )
    extra_body_html = f'<script src="{theme_asset_href(page_dir, "search.js")}" defer></script>'
    return wrap_layout(
        config=config,
        title="搜索",
        description=f"搜索 {config.title} 的全部文章与页面内容。",
        content_html=content_html,
        pages=pages,
        page_dir=page_dir,
        page_class="search-page",
        current_nav="articles",
        extra_body_html=extra_body_html,
    )


def build_search_index(entries: list[ContentEntry]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in entries:
        items.append(
            {
                "title": entry.title,
                "url": entry.url,
                "summary": entry.summary,
                "content": entry.plain_text,
                "tags": entry.tags,
                "kind": entry.kind,
                "date": entry.date.strftime("%Y-%m-%d") if entry.date else "",
            }
        )
    return items


def build_site() -> None:
    config = load_config()
    posts, pages = collect_markdown_entries()
    assign_output_paths(posts, pages)
    markdown_manifest = build_markdown_manifest(posts, pages)
    renderer = MarkdownRenderer(markdown_manifest)

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    THEME_DIST_DIR.mkdir(parents=True, exist_ok=True)

    copy_tree_contents(STATIC_DIR, DIST_DIR)
    copy_tree_contents(CONTENT_DIR, DIST_DIR, ignore_markdown=True)
    copy_theme_assets(config.theme)

    for entry in posts + pages:
        if entry.draft:
            continue
        entry.html_body = renderer.render(entry.body_markdown, entry.source_path, entry.output_path)
        entry.output_path.parent.mkdir(parents=True, exist_ok=True)

    published_posts = sorted([post for post in posts if not post.draft], key=lambda item: item.date or datetime.min, reverse=True)
    published_pages = [page for page in pages if not page.draft]
    post_categories = collect_post_categories(published_posts)

    for post in published_posts:
        post.output_path.write_text(render_post_page(post, config, published_posts, published_pages), encoding="utf-8")

    for page in published_pages:
        page.output_path.write_text(render_page_page(page, config, published_pages), encoding="utf-8")

    (DIST_DIR / "index.html").write_text(
        render_home_page(published_posts, config, published_pages, post_categories), encoding="utf-8"
    )

    articles_dir = DIST_DIR / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    (articles_dir / "index.html").write_text(
        render_articles_page(post_categories, published_posts, config, published_pages), encoding="utf-8"
    )

    for category in post_categories:
        category_path = Path(category["output_path"])
        category_path.parent.mkdir(parents=True, exist_ok=True)
        category_path.write_text(render_category_page(category, post_categories, config, published_pages), encoding="utf-8")

    if config.search_enabled:
        search_dir = DIST_DIR / "search"
        search_dir.mkdir(parents=True, exist_ok=True)
        (search_dir / "index.html").write_text(render_search_page(config, published_pages), encoding="utf-8")
        search_items = build_search_index(published_posts + published_pages)
        (DIST_DIR / "search-index.json").write_text(
            json.dumps(search_items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")


def sync_docs_output() -> None:
    if DOCS_DIR.exists():
        shutil.rmtree(DOCS_DIR)
    shutil.copytree(DIST_DIR, DOCS_DIR)


def create_new_post(title: str, folder: str | None = None) -> Path:
    subdir = normalize_content_subdir(folder)
    target_dir = POSTS_DIR / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(title)
    target = target_dir / f"{slug}.md"
    if target.exists():
        timestamp = datetime.now().strftime("%H%M%S")
        target = target_dir / f"{slug}-{timestamp}.md"
    today = datetime.now().strftime("%Y-%m-%d")
    demo_image = "/assets/images/local-demo.svg"
    template = f"""---
title: {title}
date: {today}
summary: 用一句话介绍这篇文章。
tags: [Markdown, GitHub Pages]
cover: {demo_image}
---

# {title}

在这里开始写正文。

## 可以直接插入本地图片

![示例图片]({demo_image})

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


class HTMLToMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.current_tag: str | None = None
        self.list_depth = 0
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre"}:
            self.flush()
            self.current_tag = tag
        elif tag == "br":
            self.current.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == self.current_tag:
            self.flush()
            self.current_tag = None

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if data:
            self.current.append(data)

    def flush(self) -> None:
        text = html.unescape("".join(self.current))
        text = re.sub(r"\s+", " ", text).strip()
        self.current.clear()
        if not text:
            return
        if self.current_tag and self.current_tag.startswith("h"):
            level = int(self.current_tag[1])
            self.blocks.append(f'{"#" * level} {text}')
            return
        if self.current_tag == "li":
            self.blocks.append(f"- {text}")
            return
        if self.current_tag == "blockquote":
            self.blocks.append(f"> {text}")
            return
        self.blocks.append(text)

    def to_markdown(self) -> str:
        self.flush()
        return "\n\n".join(block for block in self.blocks if block)


def import_txt_document(source_path: Path) -> ImportedDocument:
    raw_text = source_path.read_text(encoding="utf-8")
    return ImportedDocument(title=source_path.stem.replace("-", " ").strip(), markdown_body=normalize_paragraphs(raw_text))


def import_markdown_document(source_path: Path) -> ImportedDocument:
    raw_text = source_path.read_text(encoding="utf-8")
    metadata, body = parse_front_matter(raw_text)
    title = str(metadata.get("title") or source_path.stem.replace("-", " ").strip())
    return ImportedDocument(title=title, markdown_body=body.strip())


def import_html_document(source_path: Path) -> ImportedDocument:
    parser = HTMLToMarkdownParser()
    parser.feed(source_path.read_text(encoding="utf-8"))
    return ImportedDocument(title=source_path.stem.replace("-", " ").strip(), markdown_body=parser.to_markdown())


def import_docx_document(source_path: Path, slug: str, markdown_dir: Path) -> ImportedDocument:
    markdown_blocks: list[str] = []
    imported_assets: list[Path] = []
    title = source_path.stem.replace("-", " ").strip()

    with zipfile.ZipFile(source_path) as archive:
        document_xml = archive.read("word/document.xml")
        root = ET.fromstring(document_xml)

        for paragraph in root.findall(".//w:body/w:p", WORD_NAMESPACE):
            text_parts = [node.text for node in paragraph.findall(".//w:t", WORD_NAMESPACE) if node.text]
            text = "".join(text_parts).strip()
            if not text:
                continue
            style_node = paragraph.find("./w:pPr/w:pStyle", WORD_NAMESPACE)
            style = style_node.attrib.get(f'{{{WORD_NAMESPACE["w"]}}}val', "") if style_node is not None else ""
            if style.lower().startswith("title") and title == source_path.stem.replace("-", " ").strip():
                title = text
                continue
            if style.lower().startswith("heading"):
                level_match = re.search(r"(\d+)", style)
                level = max(1, min(int(level_match.group(1)) if level_match else 2, 6))
                markdown_blocks.append(f'{"#" * level} {text}')
            else:
                markdown_blocks.append(text)

        media_dir = CONTENT_DIR / "assets" / "imports" / slug
        for info in archive.infolist():
            if not info.filename.startswith("word/media/"):
                continue
            target = media_dir / Path(info.filename).name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info.filename))
            imported_assets.append(target)

    markdown_body = "\n\n".join(markdown_blocks).strip()
    if imported_assets:
        image_blocks = [f"![导入图片](/assets/imports/{slug}/{asset.name})" for asset in imported_assets]
        markdown_body += "\n\n## 文档图片\n\n" + "\n\n".join(image_blocks)

    return ImportedDocument(title=title, markdown_body=markdown_body, asset_paths=imported_assets)


def import_pdf_document(source_path: Path) -> ImportedDocument:
    try:
        result = subprocess.run(
            ["pdftotext", str(source_path), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("导入 PDF 需要本机安装 `pdftotext` 命令。") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"PDF 转文本失败：{exc.stderr.strip() or exc}") from exc

    markdown_body = normalize_paragraphs(result.stdout)
    return ImportedDocument(title=source_path.stem.replace("-", " ").strip(), markdown_body=markdown_body)


def import_source_document(source_path: Path, slug: str, markdown_dir: Path) -> ImportedDocument:
    suffix = source_path.suffix.lower()
    if suffix == ".txt":
        return import_txt_document(source_path)
    if suffix == ".md":
        return import_markdown_document(source_path)
    if suffix in {".html", ".htm"}:
        return import_html_document(source_path)
    if suffix == ".docx":
        return import_docx_document(source_path, slug, markdown_dir)
    if suffix == ".pdf":
        return import_pdf_document(source_path)
    if suffix == ".doc":
        raise RuntimeError("`.doc` 是旧版二进制格式，建议先另存为 `.docx` 再导入。")
    raise RuntimeError(f"暂不支持导入 `{suffix or '无扩展名'}` 文件。")


def create_imported_entry(
    source: str,
    *,
    title: str | None,
    slug: str | None,
    folder: str | None,
    tags: str,
    date: str | None,
    summary: str | None,
    as_page: bool,
) -> Path:
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    entry_slug = slugify(slug or title or source_path.stem)
    subdir = normalize_content_subdir(folder)
    base_dir = PAGES_DIR if as_page else POSTS_DIR
    target_dir = base_dir / subdir
    imported = import_source_document(source_path, entry_slug, target_dir)
    final_title = title or imported.title or source_path.stem.replace("-", " ").strip()
    final_date = date or datetime.now().strftime("%Y-%m-%d")
    final_summary = summary or text_excerpt(imported.markdown_body)
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
    tag_line = f"[{', '.join(tag_list)}]" if tag_list else "[]"

    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{entry_slug}.md"
    if target.exists():
        target = target_dir / f"{entry_slug}-{datetime.now().strftime('%H%M%S')}.md"

    metadata_lines = [
        "---",
        f"title: {final_title}",
        f"slug: {entry_slug}",
        f"summary: {final_summary}",
        f"tags: {tag_line}",
    ]
    if not as_page:
        metadata_lines.append(f"date: {final_date}")
    metadata_lines.append("---")
    metadata_lines.append("")
    metadata_lines.append(imported.markdown_body.strip())
    target.write_text("\n".join(metadata_lines).strip() + "\n", encoding="utf-8")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A tiny dependency-light static blog generator.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("build", help="Generate the static site into dist/.")
    subparsers.add_parser("build-docs", help="Build dist/ and sync the result into docs/.")

    new_parser = subparsers.add_parser("new", help="Create a new Markdown post.")
    new_parser.add_argument("title", help="Title of the new post.")
    new_parser.add_argument("--folder", help="Optional posts subfolder, e.g. LLM or notes/ai.")

    serve_parser = subparsers.add_parser("serve", help="Build and serve the site locally.")
    serve_parser.add_argument("--port", type=int, default=8000, help="Local port. Default: 8000.")

    import_parser = subparsers.add_parser("import", help="Import txt/md/html/docx/pdf into a blog entry.")
    import_parser.add_argument("source", help="Source document path.")
    import_parser.add_argument("--title", help="Override imported title.")
    import_parser.add_argument("--slug", help="Override output slug.")
    import_parser.add_argument("--folder", help="Optional target subfolder, e.g. LLM or notes/ai.")
    import_parser.add_argument("--tags", default="", help="Comma-separated tags, e.g. Python,Notes.")
    import_parser.add_argument("--date", help="Post date, e.g. 2026-04-21.")
    import_parser.add_argument("--summary", help="Override summary text.")
    import_parser.add_argument("--page", action="store_true", help="Import as a standalone page instead of a post.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "build":
        build_site()
        print(f"Site generated in {DIST_DIR}")
        return

    if args.command == "build-docs":
        build_site()
        sync_docs_output()
        print(f"Site generated in {DOCS_DIR}")
        return

    if args.command == "new":
        created = create_new_post(args.title, folder=args.folder)
        print(f"Created {created}")
        return

    if args.command == "serve":
        serve_site(args.port)
        return

    if args.command == "import":
        created = create_imported_entry(
            args.source,
            title=args.title,
            slug=args.slug,
            folder=args.folder,
            tags=args.tags,
            date=args.date,
            summary=args.summary,
            as_page=args.page,
        )
        print(f"Imported document to {created}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
