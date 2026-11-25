"""编译套件并换上已安装服务的程序。不写 config.json，不写 secret.hex，不删日志。
服务器上的桥已是本机这份源码时跳过编译部署（deployskip），--force-deploy 不跳过。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from co.e2e.context import Run
from co.e2e.deployskip import PROBE_FAILED_NOTE, SKIP_NOTE, local_problem, parse_probe, why_deploy
from co.e2e.kitpack import pack_bridge, source_hash
from co.e2e.plan import secret_text_problem
from co.e2e.remote import RemoteError, RemoteResult
from co.e2e.settings import conf, remote_doc


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _under_repo(path: Path, root: Path) -> None:
    full = path.resolve()
    base = root.resolve()
    if full != base and base not in full.parents:
        raise RuntimeError("拒绝上传仓库以外的文件")


def write_remote_config(dest: Path) -> Path:
    path = dest / "e2e.json"
    text = json.dumps(remote_doc(conf()), ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def upload_scripts(ctx: Run) -> None:
    # 脚本从同目录的 e2e.json 读站点值（remote/common.ps1）。配置和全部脚本一次 scp 传完，配置排在最前。
    root = repo_root()
    folder = Path(__file__).resolve().parent / "remote"
    paths = [write_remote_config(ctx.report_dir)]
    for path in sorted(folder.glob("*.ps1")):
        _under_repo(path, root)
        paths.append(path)
    try:
        ctx.remote.upload_many(paths, conf().remote_root)
    except RemoteError as exc:
        save_log(ctx, "upload-scripts.log", exc.output)
        ctx.add_step("upload-scripts", False)
        raise
    save_log(ctx, "upload-scripts.log", "uploaded " + str(len(paths)) + " files")
    ctx.add_step("upload-scripts", True)


def local_hash() -> str:
    try:
        return source_hash()
    except (OSError, RuntimeError):
        return ""


def upload_bridge(ctx: Run, before: str = "") -> str:
    """打包上传并解到 kit。返回这份包的源码哈希：打包前后算的不一致（打包时文件在变）返回空串，服务器上就不写哈希。"""
    archive = ctx.report_dir / "bridge-src.zip"
    pack_bridge(archive)
    packed = before if before and local_hash() == before else ""
    remote = conf().remote("out", "bridge-src.zip")
    ctx.remote.upload(archive, remote)
    result = ctx.remote.run_script("kit-extract.ps1", {"Archive": remote}, 180)
    require_ok(ctx, result, "kit-extract")
    return packed


def save_log(ctx: Run, name: str, text: str) -> None:
    (ctx.report_dir / name).write_text(ctx.scrub(text), encoding="utf-8")


def require_ok(ctx: Run, result, name: str) -> None:
    save_log(ctx, name + ".log", result.out)
    ctx.add_step(name, result.code == 0)
    if result.code != 0:
        raise RuntimeError(name + " 失败")


def read_service_secret(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(secret_text_problem(0, "", False))
    mode = path.stat().st_mode & 0o777
    text = path.read_text(encoding="utf-8")
    problem = secret_text_problem(mode, text, True)
    if problem:
        raise RuntimeError(problem)
    return text.strip()


def load_service_secret(ctx: Run) -> None:
    text = read_service_secret(Path(conf().secret_file))
    ctx.secret = text
    if text not in ctx.needles:
        ctx.needles.append(text)


def build_kit(ctx: Run) -> None:
    require_ok(ctx, ctx.remote.run_fixed("build", 300), "build")
    require_ok(ctx, ctx.remote.run_fixed("selftest", 60), "selftest")
    require_ok(ctx, ctx.remote.run_fixed("check", 60), "check-config")


def redeploy(ctx: Run, src_hash: str = "") -> None:
    # 健康检查通过后服务器在 exe 旁边写 bin\src.sha256，下一次据此判断能否跳过。
    named = {"SrcHash": src_hash} if src_hash else None
    result = ctx.remote.run_script("service-redeploy.ps1", named, 330)
    require_ok(ctx, result, "service-redeploy")


def probe_deployed(ctx: Run) -> dict[str, str]:
    """核对服务器上的桥（remote/bridge-hash.ps1：不停服务、不写文件，健康检查会追加一行审计）。脚本失败或连不上返回空字典，调用方照常部署。"""
    try:
        result = ctx.remote.run_script("bridge-hash.ps1", None, 90)
    except RemoteError as exc:
        result = RemoteResult(1, exc.output)
    save_log(ctx, "bridge-hash.log", result.out)
    if result.code != 0:
        ctx.add_step("bridge-hash", False, PROBE_FAILED_NOTE)
        return {}
    ctx.add_step("bridge-hash", True)
    return parse_probe(result.out)


def can_skip(ctx: Run, local: str) -> bool:
    # 先看 --force-deploy 和本机哈希：注定要部署时不去服务器探测。
    force = bool(getattr(ctx, "force_deploy", False))
    reason = local_problem(local, force)
    if not reason:
        reason = why_deploy(local, probe_deployed(ctx), False)
    if reason:
        print("编译部署桥：" + reason, file=sys.stderr)
        return False
    return True


def wait_until_healthy(ctx: Run) -> None:
    # 延迟导入：健康检查才需要客户端，单元测试不因此连上服务器。
    from co.client.u8co_client import U8CoClient, U8CoError

    client = U8CoClient(conf().bridge_url, ctx.secret, timeout=10)
    deadline = time.time() + 40
    last = "未就绪"
    while time.time() < deadline:
        try:
            payload = client.health()
        except U8CoError as exc:
            last = exc.message
            time.sleep(1)
            continue
        if payload.get("ok") is True:
            return
        time.sleep(1)
    raise RuntimeError("健康检查超时 " + last)


def deploy_service(ctx: Run) -> bool:
    """返回 True 表示桥未改动、跳过了编译部署。跳过时照样载入服务密钥并等健康检查。"""
    local = local_hash()
    skipped = can_skip(ctx, local)
    if skipped:
        ctx.add_step("deploy-skip", True, SKIP_NOTE)
        print(SKIP_NOTE, file=sys.stderr)
    else:
        packed = upload_bridge(ctx, local)
        build_kit(ctx)
        redeploy(ctx, packed)
    load_service_secret(ctx)
    wait_until_healthy(ctx)
    return skipped
