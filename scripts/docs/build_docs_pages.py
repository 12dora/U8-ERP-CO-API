#!/usr/bin/env python3
"""把公开的 Markdown 文档渲染成站点静态页，并生成首页、404.html、sitemap.xml、robots.txt 和 llms.txt。

用法（由 scripts/docs/build-api-site.sh 调用）：
  uv run --no-project --with markdown==3.7 python scripts/docs/build_docs_pages.py --out DIR \
      [--site-url URL] [--repo-url URL]

--site-url / --repo-url 缺省取环境变量 SITE_URL / REPO_URL，由 CI 在构建时给出（Pages 地址、仓库地址），
文件里不写任何固定地址。没有站点地址时省略 canonical、og:url、sitemap.xml 和 robots.txt 的 Sitemap 行；
没有仓库地址时，指向站点之外文件（LICENSE、CONTRIBUTING.md 等）的链接只保留文字。

链接改写、标题与描述提取、sitemap 等纯函数只用标准库；只有渲染 Markdown 时才导入 markdown。
"""

from __future__ import annotations

import argparse
import html
import os
import posixpath
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE_NAME = "U8-ERP-CO-API"
SITE_TAGLINE = "用友 U8+ 业务组件 HTTP API"
SITE_TOKEN = "__SITE_URL__"
REPO_TOKEN = "__REPO_URL__"
DESC_LIMIT = 120

# (仓库内路径, 站点页面名, 导航文字)。导航按此顺序。
PAGES = (
    ("README.md", "overview", "概览"),
    ("docs/getting-started.md", "getting-started", "快速开始"),
    ("docs/api-reference.md", "api-reference", "接口参考"),
    ("docs/mcp.md", "mcp", "MCP 服务"),
    ("docs/events.md", "events", "单据事件"),
    ("docs/architecture.md", "architecture", "架构"),
    ("docs/configuration.md", "configuration", "配置参考"),
    ("docs/limitations.md", "limitations", "已知限制"),
    ("docs/u8-notes.md", "u8-notes", "U8 行为说明"),
    ("docs/testing.md", "testing", "测试"),
    ("docs/faq.md", "faq", "常见问题"),
)
PAGE_BY_SOURCE = {source: slug for source, slug, _ in PAGES}
# 第一段不是全文摘要的页面，单独给描述。
DESCRIPTIONS = {
    "testing": "U8-ERP-CO-API 的测试分三层：离线单元测试、桥的自检，以及在你自己的 U8 测试账套上的验证方法。",
    "faq": "U8-ERP-CO-API 常见问题：是否为用友官方项目、许可、支持的 U8 版本、口令与权限，以及常见错误码的含义和处理。",
}

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; object-src 'none'"
)
DISCLAIMER = (
    "非官方项目，与用友网络科技股份有限公司没有关联，也未获其认可或赞助。"
    "「用友」「U8」「U8+」是用友网络科技股份有限公司的商标，本站提及仅为说明兼容对象。"
)
SITE_URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
LINK_RE = re.compile(r"<a href=\"([^\"]*)\"([^>]*)>(.*?)</a>", re.DOTALL)
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")
LIST_INDENT_RE = re.compile(r"^ {1,3}(?=(?:[-*+]|\d+\.) )")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SKIP_BLOCK_RE = re.compile(r"^(#|>|\||[-*+] |\d+\. |```|~~~|\[|!\[|<)")


# ---------- 地址 ----------

def normalize_url(value: str | None) -> str | None:
    """站点或仓库地址：去空白，只接受 http(s)，站点地址统一以 / 结尾由调用方决定。不合法时视为没有。"""
    value = (value or "").strip()
    if not value:
        return None
    if not SITE_URL_RE.match(value):
        print(f"忽略不合法的地址：{value!r}", file=sys.stderr)
        return None
    return value


def site_base(site_url: str | None) -> str | None:
    url = normalize_url(site_url)
    return url.rstrip("/") + "/" if url else None


def apply_tokens(text: str, site_url: str | None, repo_url: str | None) -> str:
    """替换占位符；没有对应地址时删掉含该占位符的整行（模板保证这些行可整行删除）。

    地址已由 normalize_url 排除引号、尖括号和空白，可直接放进属性值和 JSON-LD。
    """
    base = site_base(site_url)
    repo = normalize_url(repo_url)
    lines = []
    for line in text.splitlines(keepends=True):
        if SITE_TOKEN in line:
            if base is None:
                continue
            line = line.replace(SITE_TOKEN, base)
        if REPO_TOKEN in line:
            if repo is None:
                continue
            line = line.replace(REPO_TOKEN, repo.rstrip("/"))
        lines.append(line)
    return "".join(lines)


# ---------- 文本提取 ----------

def inline_text(text: str) -> str:
    """去掉行内 Markdown：链接留文字，去反引号、粗体、HTML 标签，合并空白。"""
    text = MD_LINK_RE.sub(r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "").replace("**", "")
    return re.sub(r"\s+", " ", text).strip()


def extract_title(markdown_text: str) -> str:
    for line in markdown_text.splitlines():
        if line.startswith("# "):
            return inline_text(line[2:])
    return ""


def _paragraphs(markdown_text: str):
    block: list[str] = []
    in_fence = False
    for line in markdown_text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            block = []
            continue
        if in_fence:
            continue
        if line.strip():
            block.append(line.strip())
            continue
        if block:
            yield block
        block = []
    if block and not in_fence:
        yield block


def truncate(text: str, limit: int = DESC_LIMIT) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    pos = cut.rfind("。")
    if pos >= limit // 2:
        return cut[: pos + 1]
    for mark in ("；", "，"):
        pos = cut.rfind(mark)
        if pos >= limit // 2:
            return cut[:pos] + "…"
    return cut.rstrip() + "…"


def extract_description(markdown_text: str, limit: int = DESC_LIMIT) -> str:
    """第一段正文：跳过标题、引用、列表、表格、代码块、纯链接行和 HTML 行。"""
    for block in _paragraphs(markdown_text):
        if SKIP_BLOCK_RE.match(block[0]):
            continue
        text = inline_text(" ".join(block))
        if text:
            return truncate(text, limit)
    return ""


# ---------- 链接 ----------

def rewrite_href(href: str, source: str, repo_url: str | None, ext: str = ".html", prefix: str = "") -> str | None:
    """改写一个相对链接。

    source 是当前文件的仓库内路径。指向站点页面的改成 prefix + 页面名 + ext（保留锚点）；
    指向仓库里其他文件的改成仓库地址下的 blob 链接；没有仓库地址时返回 None（调用方只保留文字）。
    绝对地址、mailto、纯锚点原样返回。
    """
    if not href or href.startswith(("#", "//")) or SCHEME_RE.match(href):
        return href
    path, sep, fragment = href.partition("#")
    target = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
    anchor = sep + fragment
    slug = PAGE_BY_SOURCE.get(target)
    if slug is not None:
        return f"{prefix}{slug}{ext}{anchor}"
    if target == "llms.txt" and ext == ".html":
        return f"../llms.txt{anchor}"
    repo = normalize_url(repo_url)
    if repo is None or target.startswith(".."):
        return None
    return f"{repo.rstrip('/')}/blob/HEAD/{target}{anchor}"


def rewrite_links(body_html: str, source: str, repo_url: str | None) -> str:
    """改写渲染后 HTML 里的 <a href>。"""

    def repl(match: re.Match[str]) -> str:
        href = html.unescape(match.group(1))
        new = rewrite_href(href, source, repo_url)
        if new is None:
            return match.group(3)
        return f'<a href="{html.escape(new)}"{match.group(2)}>{match.group(3)}</a>'

    return LINK_RE.sub(repl, body_html)


def rewrite_markdown_links(text: str, source: str, repo_url: str | None, prefix: str) -> str:
    """改写 Markdown 文本（llms.txt）里的 [文字](链接)，页面指向站点上的 .md 原文。"""

    def repl(match: re.Match[str]) -> str:
        new = rewrite_href(match.group(2), source, repo_url, ".md", prefix)
        return match.group(1) if new is None else f"[{match.group(1)}]({new})"

    return MD_LINK_RE.sub(repl, text)


# ---------- 站点文件 ----------

def page_paths() -> list[str]:
    """站点内可索引的页面（相对站点根目录）。"""
    return [""] + [f"docs/{slug}.html" for _, slug, _ in PAGES]


def build_sitemap(site_url: str | None, paths: list[str]) -> str | None:
    """sitemap 协议只认绝对地址；没有站点地址时不生成。"""
    base = site_base(site_url)
    if base is None:
        return None
    urls = "".join(f"  <url><loc>{html.escape(base + path)}</loc></url>\n" for path in paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n"
    )


def build_robots(site_url: str | None) -> str:
    base = site_base(site_url)
    text = "User-agent: *\nAllow: /\n"
    if base is not None:
        text += f"Sitemap: {base}sitemap.xml\n"
    return text


def build_llms(llms_text: str, site_url: str | None, repo_url: str | None) -> str:
    """站点版 llms.txt：页面链接指向站点上的 Markdown 原文，并在「接口」一节开头加上接口文档本身。"""
    base = site_base(site_url) or ""
    text = rewrite_markdown_links(llms_text, "llms.txt", repo_url, base + "docs/")
    extra = (
        f"- [交互式接口文档]({base}index.html): 全部路由的请求与响应结构，可在页面上试调用\n"
        f"- [OpenAPI 3.1 文档]({base}openapi.json): 离线导出的机器可读接口描述\n"
    )
    marker = "## 接口\n\n"
    if marker in text:
        return text.replace(marker, marker + extra, 1)
    return text.rstrip("\n") + "\n\n## 接口\n\n" + extra


# ---------- 渲染 ----------

def github_slug(value: str, separator: str = "-") -> str:
    """与 GitHub 的标题锚点一致：小写，去掉标点（保留汉字、字母、数字、- 和 _），空格换成 -。"""
    value = re.sub(r"<[^>]+>", "", value).strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", separator)


def normalize_list_indent(markdown_text: str) -> str:
    """GitHub 允许 2～3 个空格缩进的子列表，Python-Markdown 要 4 个；代码块内不动。"""
    out = []
    in_fence = False
    for line in markdown_text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            line = LIST_INDENT_RE.sub("    ", line)
        out.append(line)
    return "\n".join(out) + "\n"


def strip_badges(markdown_text: str) -> str:
    """去掉徽章行（以图片链接开头）：徽章是外站图片或仓库相对地址，站点的 CSP 和目录结构都用不了。"""
    lines = [line for line in markdown_text.splitlines() if not line.startswith(("[![", "!["))]
    return "\n".join(lines) + "\n"


def render_markdown(markdown_text: str) -> str:
    import markdown  # 只在构建时需要：uv run --with markdown==3.7

    converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        extension_configs={"toc": {"slugify": github_slug, "permalink": "#", "permalink_title": ""}},
        output_format="html",
    )
    return converter.convert(normalize_list_indent(markdown_text))


STYLE = """
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--border:#d1d9e0;--code:#f6f8fa;--link:#0969da;--note:#fff8e6;--note-fg:#5c4400}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8eb;--muted:#9aa4af;--border:#30363d;--code:#161b22;--link:#58a6ff;--note:#2b2412;--note-fg:#f3e2b3}}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--fg)}
body{font:16px/1.65 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--link)}
.top{border-bottom:1px solid var(--border);padding:12px 16px}
.top .brand{font-weight:600;text-decoration:none;color:var(--fg);margin-right:8px}
.top nav{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:6px;font-size:14px}
.top nav a[aria-current]{font-weight:600;color:var(--fg);text-decoration:none}
main{max-width:960px;margin:0 auto;padding:8px 16px 32px;overflow-wrap:anywhere}
h1,h2,h3,h4{line-height:1.3}
.headerlink{margin-left:6px;text-decoration:none;color:var(--muted);visibility:hidden}
h1:hover .headerlink,h2:hover .headerlink,h3:hover .headerlink,h4:hover .headerlink{visibility:visible}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em}
code{background:var(--code);padding:.1em .3em;border-radius:4px}
pre{background:var(--code);padding:12px;border-radius:6px;overflow-x:auto}
pre code{padding:0;background:none}
table{display:block;max-width:100%;overflow-x:auto;border-collapse:collapse;margin:12px 0}
th,td{border:1px solid var(--border);padding:6px 10px;vertical-align:top}
blockquote{margin:12px 0;padding:4px 14px;border-left:4px solid var(--border);color:var(--muted)}
footer{border-top:1px solid var(--border);padding:16px;font-size:13px;color:var(--muted);text-align:center}
.note{background:var(--note);color:var(--note-fg);padding:8px 16px;font-size:13px}
"""


def _head(title: str, description: str, canonical: str | None, noindex: bool = False) -> str:
    title_e, desc_e = html.escape(title), html.escape(description)
    lines = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
        '<meta name="referrer" content="no-referrer">',
        f"<title>{title_e}</title>",
        f'<meta name="description" content="{desc_e}">',
        '<meta property="og:type" content="article">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:title" content="{title_e}">',
        f'<meta property="og:description" content="{desc_e}">',
        '<meta property="og:locale" content="zh_CN">',
        '<meta name="twitter:card" content="summary">',
    ]
    if noindex:
        lines.append('<meta name="robots" content="noindex">')
    if canonical:
        canonical_e = html.escape(canonical)
        lines += [f'<link rel="canonical" href="{canonical_e}">', f'<meta property="og:url" content="{canonical_e}">']
    lines.append(f"<style>{STYLE}</style>")
    return "\n".join(lines)


def _nav(current: str | None, root: str) -> str:
    links = [f'<a href="{root}index.html">交互式接口文档</a>']
    for _, slug, label in PAGES:
        mark = ' aria-current="page"' if slug == current else ""
        links.append(f'<a href="{root}docs/{slug}.html"{mark}>{html.escape(label)}</a>')
    return (
        f'<header class="top"><a class="brand" href="{root}docs/overview.html">{SITE_NAME}</a>'
        f'<span>{SITE_TAGLINE}</span><nav aria-label="文档">{"".join(links)}</nav></header>'
    )


def _footer(repo_url: str | None, source: str | None) -> str:
    repo = normalize_url(repo_url)
    parts = [html.escape(DISCLAIMER), "Apache License 2.0。"]
    if repo and source:
        parts.append(f'<a href="{html.escape(repo.rstrip("/"))}/blob/HEAD/{source}">在仓库中查看本页原文</a>')
    return f"<footer>{' '.join(parts)}</footer>"


def page_document(source: str, markdown_text: str, body_html: str, site_url: str | None, repo_url: str | None) -> str:
    slug = PAGE_BY_SOURCE[source]
    title = extract_title(markdown_text)
    # 概览页（README）的一级标题本身已含项目名和说明。
    full_title = title if slug == "overview" else f"{title} · {SITE_NAME} · {SITE_TAGLINE}"
    base = site_base(site_url)
    canonical = f"{base}docs/{slug}.html" if base else None
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        + _head(full_title, DESCRIPTIONS.get(slug) or extract_description(markdown_text), canonical)
        + "\n</head>\n<body>\n"
        + _nav(slug, "../")
        + f"\n<main>\n{rewrite_links(body_html, source, repo_url)}\n</main>\n"
        + _footer(repo_url, source)
        + "\n</body>\n</html>\n"
    )


def not_found_document(site_url: str | None, repo_url: str | None) -> str:
    # 404 页会出现在任意路径下，相对链接会错位；有站点地址时用绝对地址。
    root = site_base(site_url) or "./"
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        + _head(f"页面不存在 · {SITE_NAME}", "找不到请求的页面。", None, noindex=True)
        + "\n</head>\n<body>\n"
        + _nav(None, root)
        + f'\n<main>\n<h1>页面不存在</h1>\n<p>找不到请求的页面。请从上方导航进入，或返回 <a href="{root}docs/overview.html">概览</a>。</p>\n</main>\n'
        + _footer(repo_url, None)
        + "\n</body>\n</html>\n"
    )


# ---------- 入口 ----------

def build(out: Path, site_url: str | None, repo_url: str | None) -> list[Path]:
    docs_out = out / "docs"
    if docs_out.exists():
        shutil.rmtree(docs_out)
    docs_out.mkdir(parents=True)
    written: list[Path] = []
    for source, slug, _ in PAGES:
        text = strip_badges((ROOT / source).read_text(encoding="utf-8"))
        page = page_document(source, text, render_markdown(text), site_url, repo_url)
        written.append(_write(docs_out / f"{slug}.html", page))
        # Markdown 原文与页面同目录，供 llms.txt 和 AI 代理直接读取。
        written.append(_write(docs_out / f"{slug}.md", text))
    index = (ROOT / "docs/site/index.html").read_text(encoding="utf-8")
    written.append(_write(out / "index.html", apply_tokens(index, site_url, repo_url)))
    written.append(_write(out / "404.html", not_found_document(site_url, repo_url)))
    written.append(_write(out / "robots.txt", build_robots(site_url)))
    llms = (ROOT / "llms.txt").read_text(encoding="utf-8")
    written.append(_write(out / "llms.txt", build_llms(llms, site_url, repo_url)))
    sitemap_path = out / "sitemap.xml"
    sitemap = build_sitemap(site_url, page_paths())
    if sitemap is None:
        sitemap_path.unlink(missing_ok=True)
    else:
        written.append(_write(sitemap_path, sitemap))
    return written


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲染文档静态页并生成站点文件")
    parser.add_argument("--out", required=True, type=Path, help="站点输出目录")
    parser.add_argument("--site-url", default=os.environ.get("SITE_URL"), help="站点根地址（缺省取 SITE_URL）")
    parser.add_argument("--repo-url", default=os.environ.get("REPO_URL"), help="仓库地址（缺省取 REPO_URL）")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    written = build(args.out, args.site_url, args.repo_url)
    print(f"站点地址：{site_base(args.site_url) or '未提供（省略绝对地址与 sitemap.xml）'}")
    print(f"生成 {len(written)} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
