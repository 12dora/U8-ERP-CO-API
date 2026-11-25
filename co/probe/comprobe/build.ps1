#Requires -Version 7.4
#Requires -PSEdition Core
# 在 <e2e 目录>\comprobe 里编译 32 位 comprobe.exe。不删除文件，不碰 U8 安装目录及其所在盘。
# 目录就是本脚本所在目录；comprobe.json（Linux 端按 e2e 配置生成）给出不能碰的路径。
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$Root = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
if ((Split-Path -Leaf $Root) -ne 'comprobe') {
    throw 'build.ps1 必须放在 e2e 目录下的 comprobe 目录里'
}

function Read-Protected {
    $path = Join-Path $Root 'comprobe.json'
    if (-not [System.IO.File]::Exists($path)) { throw '缺少 comprobe.json' }
    $doc = [System.IO.File]::ReadAllText($path) | ConvertFrom-Json
    if (-not ([string]$doc.probeRoot).Equals($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'comprobe.json 的 probeRoot 不是本脚本所在目录'
    }
    $items = @($doc.protected)
    if ($items.Count -lt 1) { throw 'comprobe.json 缺少 protected' }
    foreach ($item in $items) {
        if ([string]$item -notmatch '^[A-Za-z]:\\') { throw 'comprobe.json 的 protected 不合法' }
    }
    return $items
}

$script:Protected = Read-Protected

function Assert-NotProtected([string]$Full) {
    foreach ($item in $script:Protected) {
        $top = ([string]$item).TrimEnd('\')
        $hit = $Full.Equals($top, [System.StringComparison]::OrdinalIgnoreCase) -or $Full.StartsWith($top + '\', [System.StringComparison]::OrdinalIgnoreCase)
        if ($hit) { throw '拒绝 U8 安装目录所在路径' }
    }
}

function Assert-ProbeRoot([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    Assert-NotProtected $full
    $prefix = $Root + '\'
    $same = $full.Equals($Root, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $same -and -not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw ('路径必须位于 ' + $prefix + ' 之下')
    }
    return $full
}

function Get-Csc {
    $path = 'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe'
    if (-not (Test-Path -LiteralPath $path)) {
        throw '找不到 32 位 csc'
    }
    return $path
}

function Get-Sources([string]$Dir) {
    $full = Assert-ProbeRoot $Dir
    $files = @(Get-ChildItem -LiteralPath $full -File | Where-Object { $_.Extension -ceq '.cs' } | Sort-Object Name)
    if ($files.Count -eq 0) {
        throw 'src 下没有 .cs'
    }
    return $files
}

[void](Assert-ProbeRoot $Root)
$src = Assert-ProbeRoot ($Root + '\src')
$outDir = Assert-ProbeRoot ($Root + '\out')
[void][System.IO.Directory]::CreateDirectory($outDir)
$csc = Get-Csc
$out = Assert-ProbeRoot ($Root + '\out\comprobe.exe')
$cscArgs = @(
    '/nologo', '/utf8output', '/platform:x86', '/target:exe', '/codepage:65001',
    '/main:U8Co.ProbeMain',
    "/out:$out",
    '/reference:System.dll',
    '/reference:System.Core.dll',
    '/reference:System.Web.Extensions.dll'
)
foreach ($file in (Get-Sources $src)) {
    $name = Assert-ProbeRoot $file.FullName
    $cscArgs += $name
}
& $csc @cscArgs
if ($LASTEXITCODE -ne 0) {
    throw "编译失败,退出码 $LASTEXITCODE"
}
$cfgSrc = Assert-ProbeRoot ($Root + '\comprobe.exe.config')
$cfgDest = Assert-ProbeRoot ($Root + '\out\comprobe.exe.config')
Copy-Item -LiteralPath $cfgSrc -Destination $cfgDest -Force
$jsonSrc = Assert-ProbeRoot ($Root + '\comprobe.json')
$jsonDest = Assert-ProbeRoot ($Root + '\out\comprobe.json')
Copy-Item -LiteralPath $jsonSrc -Destination $jsonDest -Force
