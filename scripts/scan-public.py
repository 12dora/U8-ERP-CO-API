#!/usr/bin/env python3
"""公开仓库泄露扫描（不带任何私有词表，只用标准库）。

扫描 git 已跟踪的全部文本文件，发现以下内容即以 1 退出：
  rfc1918   内网 IPv4（10/8、172.16/12、192.168/16）；文档网段 192.0.2/24、198.51.100/24、203.0.113/24 不算
  cgnat     运营商级 NAT 地址（100.64/10，组网软件的覆盖网常用）
  hostname  Windows 默认主机名（WIN-、DESKTOP-、LAPTOP- 加大写字母数字）与 mDNS 主机名（单段名加 .local）
  path      个人目录（/home、/Users、C:\\Users 下的用户目录，管理员主目录）与 /data 下的开发、临时目录
  email     邮箱地址；只放过 example.com、example.org（含子域名）与 users.noreply.github.com
  mobile    中国大陆手机号（可带 +86 / 86 前缀，可用空格或连字符按 3-4-4 分组）；占位号 13800000000 除外
  ufdata    UFDATA_<账套> / UFMeta_<账套>（不分大小写）里的账套号不在示例账套 801/802/803/998/999 之内
  round     内部轮次标记（R 后接 10 到 29、7.5 或 9b，以及用汉字数字写的「第N批」「第N期」）；R0 到 R9 不算
  roundname 文件路径里的开发轮次与阶段标记（test_ 后接 r 与数字、p 与单个数字、phase 与数字一类的文件名或目录名），行号记 0
  prose     描述内部环境的用语（内部账套称谓、内部应用名、特定身份提供方的令牌路径、示例账套实测、年度库、内部放量节奏）
  operator  操作员、制单人、审核人等字段赋值为 U8 自带的演示操作员，SQL 里的同名 Unicode 字面量，
            以及客户端标识取演示值（本身或以其加连字符开头）
  licence   U8 加密服务与许可协议的内部接口名、报文占位符与加密狗字段
  port      内部部署使用的服务端口（1809 后接 5、6、7 的五位端口号）
  casename  正文里内部端到端用例的连字符名称
  private   不在公开仓库里的目录路径（含桥接层的私有目录）
  pem       PEM 块标记（私钥、证书等）
  tokenfile 私有词表的文件名
  encoding  不是 UTF-8 的文本文件（GBK 等），无法扫描，须先转成 UTF-8；带 BOM 的 UTF-16 照常扫描
  unreadable 命令行指定的文件不存在或读不出

不排除任何目录：仓库里的每个已跟踪文件都扫。
某行确需保留时，在该行写上「scan-public: allow」整行跳过；每一处都要在评审里说明理由并由评审人确认。
输出 文件:行号:类别: 片段（片段最多 80 个字符）；encoding、unreadable 是整个文件的问题，行号记 0。
用法：python3 scripts/scan-public.py [文件 ...]   不带参数时扫描 git ls-files 列出的全部文件；
指定的文件按当前目录解析，位于仓库内时按仓库相对路径输出。
"""

from __future__ import annotations

import codecs
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW_MARK = "scan-public: allow"
SNIPPET = 80
ALLOWED_ACCOUNTS = {"801", "802", "803", "998", "999"}
ALLOWED_MOBILES = {"13800000000"}
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org")
ALLOWED_EMAIL_HOSTS = ("users.noreply.github.com",)
# 含 NUL 时仍按文本对待（应是 UTF-16 之类）的扩展名；不是 UTF-8 时仍按二进制跳过的扩展名。
TEXT_SUFFIXES = frozenset(
    ".cs .py .md .sql .ps1 .psm1 .bat .cmd .sh .yml .yaml .json .toml .txt .xml .config .html .js .css .ini "
    ".csproj .sln .example .cfg".split()
)
BINARY_SUFFIXES = frozenset(".png .jpg .jpeg .gif .ico .pdf .zip .gz .dll .exe .snk .pfx .woff .woff2 .ttf".split())

IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d]|\.\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")
# 前后是字母数字的（哈希等）不算；前缀 86 / +86 与分组空格、连字符可有可无。
MOBILE = re.compile(r"(?<![0-9A-Za-z+])(?:\+?86[- ]?)?(1[3-9]\d)[- ]?(\d{4})[- ]?(\d{4})(?![0-9A-Za-z])")
UFDATA = re.compile(r"(?<![A-Za-z0-9])UF(?:DATA|Meta)_(\d{3})(?!\d)", re.IGNORECASE)
# 边界只用 ASCII 断言（不用 \b），轮次标记前后直接接汉字时也能命中；路径字面量写成 tm[p] 之类，免得命中本文件。
SIMPLE = (
    ("hostname", re.compile(r"(?<![A-Za-z0-9_-])(?:WIN|DESKTOP|LAPTOP)-[A-Z0-9]+(?![A-Za-z0-9_-])")),
    ("hostname", re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z0-9][A-Za-z0-9-]*\.loca[l](?![A-Za-z0-9_.(-])")),
    ("path", re.compile(r"/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+")),
    ("path", re.compile(r"(?<![A-Za-z0-9_.~/-])/(?:roo[t]|data/de[v]|data/tm[p])(?![A-Za-z0-9_.-])")),
    ("path", re.compile(r"\b[A-Za-z]:\\{1,2}Users\\{1,2}[A-Za-z0-9._-]+", re.IGNORECASE)),
    ("pem", re.compile(r"-----BEGIN [A-Z0-9 ]+-----")),
    ("tokenfile", re.compile(r"leak-tokens\.txt|oss-replace\.txt")),
    ("round", re.compile(r"(?<![A-Za-z0-9_])R(?:[12]\d|7\.5|9b)(?![0-9A-Za-z_]|\.\d)")),
    ("round", re.compile(r"第[一二三四五六七八九十]+[批期]")),
    ("prose", re.compile(r"参考账[套]|生产账[套]|三个账[套]|U8助[手]|/applicatio[n]/o/")),
    ("prose", re.compile(r"u8assistan[t]", re.IGNORECASE)),
    ("prose", re.compile(r"80[123]\s*实测|\d{4}\s*年度库")),
    ("operator", re.compile(
        r"(?<![A-Za-z0-9])(?:operator(?:_?(?:id|code|name))?|c?maker|c?verifier|c?auditor|c?handler|操作员|制单人|审核人)"
        r"[\"']?\s*(?::|：|={1,2})\s*[\"']?dem[o](?![A-Za-z0-9_])|--operator(?:=|\s+)[\"']?dem[o](?![A-Za-z0-9_])",
        re.IGNORECASE,
    )),
    ("operator", re.compile(
        r"(?<![A-Za-z0-9_])N'dem[o]'"
        r"|(?<![A-Za-z0-9_])client_?id[\"']?\s*(?::|：|={1,2})\s*[\"']?dem[o](?:-[A-Za-z0-9_-]+)?(?![A-Za-z0-9_-])",
        re.IGNORECASE,
    )),
    ("licence", re.compile(
        r"ListAllSubSy[s]|UnlockInfo[2]|GetSubsysGrou[p]|RPCCal[l]|getalllicense[s]|BFDispatc[h]|CreditIdentit[y]"
        r"|#EMPT[Y]#|#ERRO[R]#|(?<![A-Za-z])Dog(?:Cu[s]|I[D])",
        re.IGNORECASE,
    )),
    ("port", re.compile(r"\b1809[5-7]\b")),
    ("prose", re.compile(r"隔天再开[放]")),
    # x- 开头的是 OpenAPI 扩展字段名，不是用例名。
    ("casename", re.compile(r"用例\s*`?(?!x-)[a-z][a-z0-9]*(?:-[a-z0-9]+)+`?|（(?!x-)[a-z][a-z0-9]*(?:-[a-z0-9]+){2,}）")),
    ("private", re.compile(r"co[/.]e2[e]|co/prob[e]|comprob[e]|scripts/lan[e]|scripts/accoun[t]|co/bridge/privat[e]")),
)
# 路径里的轮次与阶段标记：r 后接一位或两位轮次号、7.5、9b，p 后接一位数字，前后是路径分隔、下划线、连字符、点或首尾；
# phase 后接数字不论位置。不分大小写。
ROUND_NAME = re.compile(r"(?:^|[/_.-])(?:r(?:\d|[12]\d|7\.?5|9b)|p\d)(?=[/_.-]|$)|phase\d+", re.IGNORECASE)


def _ip_class(match: re.Match[str]) -> str | None:
    a, b, c, d = (int(g) for g in match.groups())
    if max(a, b, c, d) > 255:
        return None
    # 按首段判断，不在本文件里写出网段字面量（否则会命中自己）。
    if a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168):
        return "rfc1918"
    if a == 100 and 64 <= b <= 127:
        return "cgnat"
    return None


def _bad_email(match: re.Match[str]) -> bool:
    domain = match.group(1).lower()
    if domain in ALLOWED_EMAIL_HOSTS:
        return False
    return not any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)


def _bad_mobile(match: re.Match[str]) -> bool:
    return "".join(match.groups()) not in ALLOWED_MOBILES


def _line_hits(line: str) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for m in IPV4.finditer(line):
        name = _ip_class(m)
        if name:
            hits.append((name, m.start()))
    hits += [("email", m.start()) for m in EMAIL.finditer(line) if _bad_email(m)]
    hits += [("mobile", m.start()) for m in MOBILE.finditer(line) if _bad_mobile(m)]
    hits += [("ufdata", m.start()) for m in UFDATA.finditer(line) if m.group(1) not in ALLOWED_ACCOUNTS]
    for name, rx in SIMPLE:
        hits += [(name, m.start()) for m in rx.finditer(line)]
    return hits


def _snippet(line: str, start: int) -> str:
    begin = max(0, min(start - SNIPPET // 4, len(line) - SNIPPET))
    return line[begin : begin + SNIPPET].strip()


def scan_text(path: str, text: str) -> list[str]:
    """返回「文件:行号:类别: 片段」列表；同一行同一类只报一次。"""
    found: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        seen: set[str] = set()
        for name, start in sorted(_line_hits(line), key=lambda h: h[1]):
            if name not in seen:
                seen.add(name)
                found.append(f"{path}:{lineno}:{name}: {_snippet(line, start)}")
    return found


def name_hits(rel: str) -> list[str]:
    """路径里的轮次标记，行号记 0。仓库外的文件（绝对路径）只看文件名。"""
    target = Path(rel).name if Path(rel).is_absolute() else rel
    return [f"{rel}:0:roundname: {target[-SNIPPET:]}"] if ROUND_NAME.search(target) else []


class NotUtf8Error(ValueError):
    """文本文件不是 UTF-8，无法扫描。"""


def read_text(path: Path) -> str | None:
    """文本文件读出为字符串；二进制返回 None；不是 UTF-8 的文本抛 NotUtf8Error；读不出抛 OSError。"""
    raw = path.read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    suffix = path.suffix.lower()
    if b"\0" in raw[:8192]:
        if suffix in TEXT_SUFFIXES:
            raise NotUtf8Error(str(path))
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        if suffix in BINARY_SUFFIXES:
            return None
        raise NotUtf8Error(str(path)) from None


def tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    )
    return [p for p in proc.stdout.decode("utf-8").split("\0") if p]


def _targets(argv: list[str], cwd: Path) -> list[tuple[str, Path, bool]]:
    """(输出用路径, 实际路径, 是否命令行指定)。命令行路径按当前目录解析，在仓库内时换成仓库相对路径。"""
    if not argv:
        return [(rel, ROOT / rel, False) for rel in tracked_files()]
    targets: list[tuple[str, Path, bool]] = []
    for arg in argv:
        path = (cwd / arg).resolve()
        try:
            rel = path.relative_to(ROOT).as_posix()
        except ValueError:
            rel = arg.replace("\\", "/")
        targets.append((rel, path, True))
    return targets


def scan_file(rel: str, path: Path, explicit: bool) -> tuple[bool, list[str]]:
    """扫一个文件，返回（是否扫过, 命中）。git 列出但工作区已删的文件跳过；命令行指定的读不出算命中。
    路径里的轮次标记对二进制文件和非 UTF-8 文件同样报。"""
    try:
        text = read_text(path)
    except NotUtf8Error:
        return False, [*name_hits(rel), f"{rel}:0:encoding: 不是 UTF-8 文本，未扫描，请转成 UTF-8"]
    except OSError:
        return False, [f"{rel}:0:unreadable: 文件不存在或读不出"] if explicit else []
    if text is None:
        return False, name_hits(rel)
    return True, [*name_hits(rel), *scan_text(rel, text)]


def main(argv: list[str], cwd: Path | None = None) -> int:
    hits: list[str] = []
    scanned = 0
    for rel, path, explicit in _targets(argv, cwd or Path.cwd()):
        done, found = scan_file(rel, path, explicit)
        scanned += done
        hits += found
    for hit in hits:
        print(hit)
    print(f"scan-public: 扫描 {scanned} 个文件，命中 {len(hits)} 处", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
