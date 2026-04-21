# Themes

`RookieBlog` 的主题从这里读取。

每个主题目录可以包含：

```text
themes/<theme-name>/
├── assets/
│   ├── theme.css
│   └── search.js
└── templates/
    ├── base.html
    ├── home.html
    ├── page.html
    ├── post.html
    ├── post_card.html
    └── search.html
```

使用方式：

1. 复制 `themes/default` 到一个新目录。
2. 修改模板和样式。
3. 在 `site.json` 里把 `"theme"` 改成你的主题名。

模板使用简单占位符，例如 `{{site_title}}`、`{{content_html}}`。
