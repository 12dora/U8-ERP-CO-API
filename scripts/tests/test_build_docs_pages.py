"""scripts/docs/build_docs_pages.py 的纯函数：链接改写、标题与描述提取、站点地址占位、sitemap、robots、llms.txt。

只用标准库；渲染 Markdown 的部分需要 markdown 包，不在这里测。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "docs" / "build_docs_pages.py"
_SPEC = importlib.util.spec_from_file_location("build_docs_pages", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
pages = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pages)

SITE = "https://docs.example.com/u8co"
REPO = "https://git.example.com/org/u8co"


class RewriteHrefTest(unittest.TestCase):
    def test_sibling_doc(self) -> None:
        self.assertEqual(pages.rewrite_href("limitations.md", "docs/mcp.md", None), "limitations.html")

    def test_keeps_fragment(self) -> None:
        got = pages.rewrite_href("api-reference.md#4-单据类型", "docs/faq.md", None)
        self.assertEqual(got, "api-reference.html#4-单据类型")

    def test_readme_to_docs(self) -> None:
        self.assertEqual(pages.rewrite_href("docs/getting-started.md", "README.md", None), "getting-started.html")

    def test_doc_to_readme(self) -> None:
        self.assertEqual(pages.rewrite_href("../README.md", "docs/faq.md", None), "overview.html")

    def test_untouched(self) -> None:
        for href in ("#english", "https://example.com/a.md", "mailto:a@example.com", "//example.com/x"):
            self.assertEqual(pages.rewrite_href(href, "README.md", REPO), href)

    def test_repo_file_with_repo_url(self) -> None:
        got = pages.rewrite_href("../CONTRIBUTING.md", "docs/testing.md", REPO)
        self.assertEqual(got, REPO + "/blob/HEAD/CONTRIBUTING.md")
        self.assertEqual(pages.rewrite_href("LICENSE", "README.md", REPO + "/"), REPO + "/blob/HEAD/LICENSE")

    def test_repo_file_without_repo_url(self) -> None:
        self.assertIsNone(pages.rewrite_href("../CONTRIBUTING.md", "docs/testing.md", None))
        self.assertIsNone(pages.rewrite_href("LICENSE", "README.md", "not a url"))

    def test_outside_repo(self) -> None:
        self.assertIsNone(pages.rewrite_href("../../x.md", "docs/faq.md", REPO))

    def test_llms_link(self) -> None:
        self.assertEqual(pages.rewrite_href("llms.txt", "README.md", None), "../llms.txt")

    def test_markdown_ext_and_prefix(self) -> None:
        got = pages.rewrite_href("docs/mcp.md", "llms.txt", None, ".md", SITE + "/docs/")
        self.assertEqual(got, SITE + "/docs/mcp.md")


class RewriteLinksTest(unittest.TestCase):
    def test_html(self) -> None:
        body = '<p><a href="configuration.md#defaults">配置</a> <a href="../LICENSE">许可</a></p>'
        got = pages.rewrite_links(body, "docs/limitations.md", None)
        self.assertEqual(got, '<p><a href="configuration.html#defaults">配置</a> 许可</p>')

    def test_html_keeps_attributes(self) -> None:
        body = '<a href="../NOTICE" title="t">NOTICE</a>'
        got = pages.rewrite_links(body, "docs/faq.md", REPO)
        self.assertEqual(got, f'<a href="{REPO}/blob/HEAD/NOTICE" title="t">NOTICE</a>')

    def test_markdown(self) -> None:
        text = "- [快速开始](docs/getting-started.md): 安装\n- [安全](SECURITY.md)\n"
        got = pages.rewrite_markdown_links(text, "llms.txt", None, "docs/")
        self.assertEqual(got, "- [快速开始](docs/getting-started.md): 安装\n- 安全\n")
        got = pages.rewrite_markdown_links("[README](README.md)", "llms.txt", None, "docs/")
        self.assertEqual(got, "[README](docs/overview.md)")


class ExtractTest(unittest.TestCase):
    SAMPLE = (
        "# 接口参考\n\n"
        "[![CI](ci.svg)](ci)\n\n"
        "> 免责声明。\n\n"
        "```\n不是正文\n```\n\n"
        "| a | b |\n| --- | --- |\n\n"
        "本文列出[全部路由](api.md)、`字段`和\n**错误码**。\n\n"
        "第二段。\n"
    )

    def test_title(self) -> None:
        self.assertEqual(pages.extract_title(self.SAMPLE), "接口参考")
        self.assertEqual(pages.extract_title("# `meta` 字段\n"), "meta 字段")
        self.assertEqual(pages.extract_title("没有标题\n"), "")

    def test_description_first_paragraph(self) -> None:
        self.assertEqual(pages.extract_description(self.SAMPLE), "本文列出全部路由、字段和 错误码。")

    def test_description_skips_fenced_blank_lines(self) -> None:
        text = "# T\n\n```\n\n伪段落\n\n```\n\n正文。\n"
        self.assertEqual(pages.extract_description(text), "正文。")

    def test_description_truncated(self) -> None:
        text = "# T\n\n" + "甲" * 100 + "，" + "乙" * 100 + "。\n"
        got = pages.extract_description(text, 150)
        self.assertEqual(got, "甲" * 100 + "…")
        text = "# T\n\n" + "甲" * 100 + "。" + "乙" * 100 + "\n"
        self.assertEqual(pages.extract_description(text, 150), "甲" * 100 + "。")
        got = pages.extract_description("# T\n\n" + "丙" * 300 + "\n", 150)
        self.assertEqual(len(got), 150)
        self.assertTrue(got.endswith("…"))

    def test_strip_badges(self) -> None:
        text = "# T\n\n[![CI](a.svg)](b) [![L](c)](d)\n\n正文 [![x]] 不动。\n"
        self.assertEqual(pages.strip_badges(text), "# T\n\n\n正文 [![x]] 不动。\n")

    def test_github_slug(self) -> None:
        self.assertEqual(pages.github_slug("9. 删除 `vouchers/delete`"), "9-删除-vouchersdelete")
        self.assertEqual(pages.github_slug("English"), "english")

    def test_list_indent(self) -> None:
        text = "1. 一\n   - 子\n  - 子2\n```\n  - 代码\n```\n"
        self.assertEqual(pages.normalize_list_indent(text), "1. 一\n    - 子\n    - 子2\n```\n  - 代码\n```\n")


class SiteFilesTest(unittest.TestCase):
    def test_sitemap_with_site_url(self) -> None:
        got = pages.build_sitemap(SITE, ["", "docs/faq.html"])
        assert got is not None
        self.assertIn(f"<loc>{SITE}/</loc>", got)
        self.assertIn(f"<loc>{SITE}/docs/faq.html</loc>", got)
        self.assertTrue(got.startswith('<?xml version="1.0" encoding="UTF-8"?>'))

    def test_sitemap_trailing_slash_normalized(self) -> None:
        got = pages.build_sitemap(SITE + "/", ["docs/faq.html"])
        assert got is not None
        self.assertIn(f"<loc>{SITE}/docs/faq.html</loc>", got)

    def test_sitemap_without_site_url(self) -> None:
        for value in (None, "", "  ", "ftp://x", "relative/path"):
            self.assertIsNone(pages.build_sitemap(value, ["docs/faq.html"]), value)

    def test_page_paths_cover_all_pages(self) -> None:
        paths = pages.page_paths()
        self.assertEqual(paths[0], "")
        self.assertEqual(len(paths), len(pages.PAGES) + 1)
        self.assertIn("docs/overview.html", paths)

    def test_robots(self) -> None:
        self.assertEqual(pages.build_robots(None), "User-agent: *\nAllow: /\n")
        self.assertTrue(pages.build_robots(SITE).endswith(f"Sitemap: {SITE}/sitemap.xml\n"))

    def test_tokens_with_urls(self) -> None:
        text = f'a\n<link href="{pages.SITE_TOKEN}">\n"r": "{pages.REPO_TOKEN}",\nb\n'
        got = pages.apply_tokens(text, SITE, REPO)
        self.assertEqual(got, f'a\n<link href="{SITE}/">\n"r": "{REPO}",\nb\n')

    def test_tokens_without_urls_drop_lines(self) -> None:
        text = f'a\n<link href="{pages.SITE_TOKEN}">\n"r": "{pages.REPO_TOKEN}",\nb\n'
        self.assertEqual(pages.apply_tokens(text, None, None), "a\nb\n")

    def test_llms(self) -> None:
        text = "# X\n\n## 接口\n\n- [接口参考](docs/api-reference.md): 路由\n"
        got = pages.build_llms(text, SITE, None)
        self.assertIn(f"- [交互式接口文档]({SITE}/index.html)", got)
        self.assertIn(f"- [接口参考]({SITE}/docs/api-reference.md): 路由", got)
        self.assertLess(got.index("交互式接口文档"), got.index("[接口参考]"))
        got = pages.build_llms(text, None, None)
        self.assertIn("- [OpenAPI 3.1 文档](openapi.json)", got)
        self.assertIn("(docs/api-reference.md)", got)

    def test_site_template_tokens_are_droppable(self) -> None:
        """首页模板去掉占位行后 JSON-LD 仍是合法 JSON。"""
        import json
        import re

        template = (Path(__file__).resolve().parents[2] / "docs" / "site" / "index.html").read_text(encoding="utf-8")
        for site, repo in ((None, None), (SITE, REPO)):
            out = pages.apply_tokens(template, site, repo)
            self.assertNotIn(pages.SITE_TOKEN, out)
            self.assertNotIn(pages.REPO_TOKEN, out)
            block = re.search(r'<script type="application/ld\+json">(.*?)</script>', out, re.DOTALL)
            assert block is not None
            data = json.loads(block.group(1))
            self.assertEqual(data["@graph"][0]["name"], "U8-ERP-CO-API")


if __name__ == "__main__":
    unittest.main()
