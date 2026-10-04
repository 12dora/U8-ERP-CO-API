#Requires -Version 7.4
#Requires -PSEdition Core
# CI 用：在任意目录编译 u8co-bridge.exe，然后跑 u8co-bridge.exe --selftest。
# 不需要安装 U8：桥接用后期绑定调用 COM，编译期不引用 U8 程序集；selftest 只校验内置测试向量。
# 编译参数与 co/bridge/build.ps1 一致。
# csc 固定用 .NET Framework 4 自带的 32 位 csc（只支持 C# 5），所以这里也顺带检查了 C# 5 语法。
# 服务器上的部署构建仍用 co/bridge/build.ps1。
param(
    [string]$OutDir = (Join-Path ([System.IO.Path]::GetTempPath()) "u8co-ci-build"),
    [switch]$SkipSelfTest
)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$CommonRefs = @("/reference:System.dll", "/reference:System.Core.dll", "/reference:System.Web.Extensions.dll")

function Get-CscExe {
    $path = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
    if (-not (Test-Path -LiteralPath $path)) {
        throw "找不到 32 位 csc: $path"
    }
    return $path
}

function Get-CsSource([string]$Dir) {
    $files = @(Get-ChildItem -LiteralPath $Dir -File | Where-Object { $_.Extension -ceq ".cs" } | Sort-Object Name)
    if ($files.Count -eq 0) {
        throw "$Dir 下没有 .cs"
    }
    return $files
}

function Invoke-Csc([string]$Csc, [string]$Out, [string[]]$Extra, $Files) {
    $cscArgs = @("/nologo", "/utf8output", "/platform:x86", "/target:exe", "/codepage:65001", "/out:$Out")
    $cscArgs += $CommonRefs
    $cscArgs += $Extra
    foreach ($file in $Files) {
        $cscArgs += $file.FullName
    }
    # 源文件有八百多个，命令行会超过 Windows 的长度上限，参数一律写进响应文件（与 co/bridge/build.ps1 相同）。
    $rsp = [System.IO.Path]::ChangeExtension($Out, ".rsp")
    $lines = foreach ($arg in $cscArgs) { if ($arg -match "\s") { '"' + $arg + '"' } else { $arg } }
    [System.IO.File]::WriteAllLines($rsp, [string[]]$lines, [System.Text.UTF8Encoding]::new($false))
    Write-Host ("csc -> " + $Out + "（" + $cscArgs.Count + " 个参数）")
    & $Csc "@$rsp" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "编译失败,退出码 $LASTEXITCODE"
    }
}

# 与 co/bridge/build.ps1 相同：sql/<模块>/*.sql 作为清单资源嵌入，逻辑名是相对 bridge 的路径（如 sql/ia/post.sql）。
function Get-SqlResourceArgs([string]$Bridge) {
    $root = [System.IO.Path]::GetFullPath($Bridge).TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
    $files = @(Get-ChildItem -LiteralPath (Join-Path $Bridge "sql") -File -Recurse | Where-Object { $_.Extension -ceq ".sql" })
    if ($files.Count -eq 0) {
        throw "sql 下没有 .sql"
    }
    $out = @()
    foreach ($file in ($files | Sort-Object FullName)) {
        $name = $file.FullName.Substring($root.Length).Replace("\", "/")
        if ($name.Split("/").Count -ne 3 -or $file.FullName.Contains(",")) {
            throw "sql 脚本只能放在 sql/<模块>/ 下且路径不含逗号: $($file.FullName)"
        }
        $out += ("/resource:" + $file.FullName + "," + $name)
    }
    return $out
}

function Build-Bridge([string]$Csc, [string]$Dir) {
    $bridge = Join-Path $RepoRoot "co/bridge"
    $out = Join-Path $Dir "u8co-bridge.exe"
    $extra = @("/reference:System.ServiceProcess.dll", "/reference:System.Data.dll", "/reference:System.Transactions.dll")
    $extra += Get-SqlResourceArgs $bridge
    Invoke-Csc $Csc $out $extra (Get-CsSource (Join-Path $bridge "src"))
    Copy-Item -LiteralPath (Join-Path $bridge "u8co-bridge.exe.config") -Destination $Dir -Force
    return $out
}

function Invoke-SelfTest([string]$Exe) {
    & $Exe --selftest | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "u8co-bridge.exe --selftest 失败,退出码 $LASTEXITCODE"
    }
}

$full = [System.IO.Path]::GetFullPath($OutDir)
[void][System.IO.Directory]::CreateDirectory($full)
$csc = Get-CscExe
$bridgeExe = Build-Bridge $csc $full
if (-not $SkipSelfTest) {
    Invoke-SelfTest $bridgeExe
}
Write-Host "已生成 $full"
