#!/usr/bin/env bash
# 构建文档站点：dist/api-site/（可用第一个参数换目录）。
#   openapi.json          离线导出（python -m u8co_api.openapi_export），不连桥、不连身份提供方
#   index.html            docs/site/index.html，站点地址占位符在构建时替换
#   scalar.standalone.js  固定版本的 @scalar/api-reference 浏览器包，校验 sha512 后放进来，页面不走 CDN
#   docs/*.html, docs/*.md  README.md 与 docs/*.md 渲染的静态页和原文（scripts/docs/build_docs_pages.py）
#   404.html、robots.txt、llms.txt，以及有站点地址时的 sitemap.xml
#
# 环境变量（都可不设，CI 在构建时给出，文件里不写固定地址）：
#   SITE_URL  站点根地址（GitHub Pages 地址）。不设时省略 canonical、og:url 和 sitemap.xml。
#   REPO_URL  仓库地址。不设时指向仓库文件的链接只保留文字。
#
# 需要：uv、curl、tar、openssl。只从 npm 官方仓库下载 Scalar 包和 PyPI 的 markdown，遵守 https_proxy。
# 升级 Scalar：改 SCALAR_VERSION，再把 SCALAR_INTEGRITY 换成
#   curl -s https://registry.npmjs.org/@scalar/api-reference/<版本> 里的 dist.integrity。
set -euo pipefail

SCALAR_VERSION="1.72.1"
SCALAR_INTEGRITY="sha512-XpEJ0rfc/cXbCrR+iC4kK0o/WwX2dTtGQbT049OlPKAHD6IMIFdTeGz2r+M27P2EDWKyUITRm/NzlP8z2pw/Ow=="
MARKDOWN_VERSION="3.7"
SCALAR_TARBALL="https://registry.npmjs.org/@scalar/api-reference/-/api-reference-${SCALAR_VERSION}.tgz"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-${ROOT}/dist/api-site}"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

sha512_b64() {
  openssl dgst -sha512 -binary "$1" | openssl base64 -A
}

fetch_scalar() {
  local tarball="${WORK}/scalar.tgz"
  echo "下载 @scalar/api-reference@${SCALAR_VERSION}"
  curl --fail --silent --show-error --location --retry 3 --max-time 300 \
    --output "${tarball}" "${SCALAR_TARBALL}"
  local actual
  actual="sha512-$(sha512_b64 "${tarball}")"
  if [[ "${actual}" != "${SCALAR_INTEGRITY}" ]]; then
    echo "Scalar 包校验失败，拒绝继续" >&2
    echo "  期望 ${SCALAR_INTEGRITY}" >&2
    echo "  实际 ${actual}" >&2
    exit 1
  fi
  tar -xzf "${tarball}" -C "${WORK}" package/dist/browser/standalone.js
  cp "${WORK}/package/dist/browser/standalone.js" "${OUT}/scalar.standalone.js"
}

export_openapi() {
  echo "导出 openapi.json"
  # uv run --project 不改变当前目录，所以 --out 用绝对路径。
  uv run --frozen --project "${ROOT}/api" python -m u8co_api.openapi_export --out "${OUT}/openapi.json"
}

mkdir -p "${OUT}"
OUT="$(cd "${OUT}" && pwd)"
rm -rf "${OUT}/docs"
rm -f "${OUT}/index.html" "${OUT}/openapi.json" "${OUT}/scalar.standalone.js" \
  "${OUT}/404.html" "${OUT}/robots.txt" "${OUT}/sitemap.xml" "${OUT}/llms.txt"

build_pages() {
  echo "渲染文档页"
  uv run --no-project --with "markdown==${MARKDOWN_VERSION}" \
    python "${ROOT}/scripts/docs/build_docs_pages.py" --out "${OUT}"
}

fetch_scalar
export_openapi
build_pages

echo "完成：${OUT}"
ls -l "${OUT}" "${OUT}/docs"
