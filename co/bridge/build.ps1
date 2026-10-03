#Requires -Version 7.4
#Requires -PSEdition Core
# 不用 /warnaserror：警告不让编译失败；csc 返回非 0（有错误）才失败。
# /codepage:65001：源文件是 UTF-8。不写这个，中文 Windows 会按系统 ANSI 代码页读，界面文字会乱。
# -U8Home：U8 安装目录。脚本目录和输出不能落在它下面（U8 装在非系统盘时整盘都不碰）。调用方按配置的 u8_home 传入。
# -ExtraSourceDir：另外参与编译的 .cs 所在目录（缺省空，只编译 src）。相对路径按脚本目录解析。
param(
    [string]$U8Home = "C:\U8SOFT",
    [string]$ExtraSourceDir = ""
)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$here = $PSScriptRoot
Set-Location -LiteralPath $here
. (Join-Path (Split-Path -Parent $here) "SafePath.ps1")
Initialize-Staging $here "" $U8Home

function Get-CscExe {
    $path = "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
    if (-not (Test-Path -LiteralPath $path)) {
        throw "找不到 32 位 csc: $path"
    }
    return $path
}

# 先 src 下的 .cs，再 -ExtraSourceDir 下的 .cs（给出时），各自按文件名排序。
# 点数租约读取的实现不在本源码树中；没有实现时只用 UA_TaskLog（见 src\LicenseHooks.cs）。
# 两处不能有同名文件：同一个文件名在两处出现说明搬家没搬干净。
function Get-SourceList {
    $dir = Join-Path $here "src"
    $files = @(Get-ChildItem -LiteralPath $dir -File | Where-Object { $_.Extension -ceq ".cs" } | Sort-Object Name)
    if ($files.Count -eq 0) {
        throw "src 下没有 .cs"
    }
    if ([string]::IsNullOrWhiteSpace($ExtraSourceDir)) {
        return $files
    }
    $extraDir = $ExtraSourceDir
    if (-not [System.IO.Path]::IsPathRooted($extraDir)) {
        $extraDir = Join-Path $here $extraDir
    }
    $extraDir = [System.IO.Path]::GetFullPath($extraDir)
    Assert-DriveSafe $extraDir
    if (-not (Test-Path -LiteralPath $extraDir -PathType Container)) {
        throw "找不到附加源码目录: $extraDir"
    }
    $extra = @(Get-ChildItem -LiteralPath $extraDir -File | Where-Object { $_.Extension -ceq ".cs" } | Sort-Object Name)
    foreach ($file in $extra) {
        if (@($files | Where-Object { $_.Name -eq $file.Name }).Count -gt 0) {
            throw "src 和附加源码目录下有同名文件: $($file.Name)"
        }
    }
    $files += $extra
    return $files
}

# sql 下的 .sql 作为清单资源嵌入，逻辑名是相对 bridge 的路径、用 /（如 sql/ia/post.sql），桥按这个名字读（SqlScript.Text）。
# 只认一层模块目录 sql/<模块>/*.sql，别的深度的 .sql 拒绝。
# /resource 的参数用逗号分隔路径和逻辑名，路径里有逗号就拒绝。
function Get-SqlResources {
    $dir = Join-Path $here "sql"
    if (-not (Test-Path -LiteralPath $dir)) {
        throw "缺少 sql 目录"
    }
    $root = [System.IO.Path]::GetFullPath($here).TrimEnd("\") + "\"
    $items = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $dir -File -Recurse | Where-Object { $_.Extension -eq ".sql" })) {
        $full = Assert-UnderStaging $file.FullName
        if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "sql 脚本不在 bridge 目录下: $full"
        }
        $name = $full.Substring($root.Length).Replace("\", "/")
        if ($name.Split("/").Count -ne 3 -or $file.Extension -cne ".sql") {
            throw "sql 脚本只能放在 sql\<模块>\ 下、扩展名小写 .sql: $full"
        }
        if ($full.Contains(",")) {
            throw "sql 脚本路径不能含逗号: $full"
        }
        $items += [pscustomobject]@{ Path = $full; Name = $name }
    }
    if ($items.Count -eq 0) {
        throw "sql 下没有 .sql"
    }
    return @($items | Sort-Object Name)
}

function New-CscArgs($Out, $Files, $Resources) {
    $cscArgs = @(
        "/nologo",
        "/utf8output",
        "/platform:x86",
        "/target:exe",
        "/codepage:65001",
        "/out:$Out",
        "/reference:System.dll",
        "/reference:System.Core.dll",
        "/reference:System.Data.dll",
        "/reference:System.ServiceProcess.dll",
        "/reference:System.Transactions.dll",
        "/reference:System.Web.Extensions.dll"
    )
    foreach ($file in $Files) {
        $cscArgs += $file.FullName
    }
    foreach ($res in $Resources) {
        $cscArgs += ("/resource:" + $res.Path + "," + $res.Name)
    }
    return $cscArgs
}

function Copy-ExeConfig([string]$OutDir) {
    $src = Assert-UnderStaging (Join-Path $here "u8co-bridge.exe.config")
    if (-not (Test-Path -LiteralPath $src)) {
        throw "缺少 u8co-bridge.exe.config"
    }
    $dest = Assert-UnderStaging (Join-Path $OutDir "u8co-bridge.exe.config")
    Copy-Item -LiteralPath $src -Destination $dest -Force
}

# 参数写进响应文件（csc @文件）：源文件多了以后整条命令行会超过 Windows 的 32767 字符上限
# （「文件名或扩展名太长」）。一行一个参数，含空白的加引号。
function Write-CscResponse([string]$Csc, [string]$OutDir, $CscArgs) {
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($arg in $CscArgs) {
        if ($arg -match "\s") {
            $lines.Add('"' + $arg + '"')
        } else {
            $lines.Add($arg)
        }
    }
    $rsp = Assert-UnderStaging (Join-Path $OutDir "csc.rsp")
    [System.IO.File]::WriteAllLines($rsp, $lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host ($Csc + " @" + $rsp + "（" + $lines.Count + " 个参数）")
    return $rsp
}

$csc = Get-CscExe
$files = Get-SourceList
$outDir = Assert-UnderStaging (Join-Path $here "out")
[void][System.IO.Directory]::CreateDirectory($outDir)
$out = Assert-UnderStaging (Join-Path $outDir "u8co-bridge.exe")
$resources = Get-SqlResources
$cscArgs = New-CscArgs $out $files $resources
$rsp = Write-CscResponse $csc $outDir $cscArgs
& $csc ("@" + $rsp)
if ($LASTEXITCODE -ne 0) {
    throw "编译失败,退出码 $LASTEXITCODE"
}
Copy-ExeConfig $outDir
Write-Host "已生成 $out"
