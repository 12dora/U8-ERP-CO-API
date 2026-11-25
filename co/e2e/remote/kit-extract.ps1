#Requires -Version 7.4
#Requires -PSEdition Core
# 把桥源码 zip 解到 kit。只枚举删除上一次解出的 .cs（kit\bridge\src、kit\bridge\private）和 .sql（kit\bridge\sql 下一层子目录），不递归。
param(
    [Parameter(Mandatory = $true)][string]$Archive
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Assert-KitPath([string]$Path) {
    $full = Assert-E2EPath $Path
    $kit = [System.IO.Path]::GetFullPath((Get-E2EPath "kit"))
    if ($full.Equals($kit, [System.StringComparison]::OrdinalIgnoreCase)) { return $full }
    $prefix = $kit + "\"
    if ($full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $full }
    throw ("目标不在 " + $prefix + " 之下")
}

function Assert-NotReparse([string]$Path) {
    if (-not [System.IO.File]::Exists($Path) -and -not [System.IO.Directory]::Exists($Path)) { return }
    Assert-NoReparse $Path
}

function Assert-ZipEntry([string]$Kit, [string]$Name) {
    if ($Name.Contains("..") -or $Name.Contains(":")) { throw "压缩包路径非法" }
    $trim = $Name.TrimEnd("/", "\")
    if ([string]::IsNullOrWhiteSpace($trim)) { return }
    $rel = $trim -replace "/", "\"
    if ($rel.StartsWith("\") -or [System.IO.Path]::IsPathRooted($rel)) { throw "压缩包路径非法" }
    $combined = [System.IO.Path]::GetFullPath((Join-Path $Kit $rel))
    Assert-KitPath $combined | Out-Null
}

function Assert-ArchiveEntries([string]$Zip, [string]$Kit) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $opened = [System.IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        $count = 0
        foreach ($entry in $opened.Entries) {
            $name = [string]$entry.FullName
            Assert-ZipEntry $Kit $name
            if (-not $name.EndsWith("/")) { $count += 1 }
        }
        if ($count -lt 1) { throw "压缩包是空的" }
    } finally {
        $opened.Dispose()
    }
}

function Remove-CsFile([string]$Dir, [string]$File) {
    if (-not $File.EndsWith(".cs", [System.StringComparison]::OrdinalIgnoreCase)) { return }
    $full = Assert-KitPath $File
    $parent = [System.IO.Path]::GetDirectoryName($full)
    if (-not $parent.Equals($Dir, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝删除子目录中的文件"
    }
    Assert-NotReparse $full
    [System.IO.File]::Delete($full)
}

function Remove-SqlFile([string]$Dir, [string]$File) {
    if (-not $File.EndsWith(".sql", [System.StringComparison]::OrdinalIgnoreCase)) { return }
    $full = Assert-KitPath $File
    if (-not [System.IO.Path]::GetDirectoryName($full).Equals($Dir, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝删除其他目录中的文件"
    }
    Assert-NotReparse $full
    [System.IO.File]::Delete($full)
}

# 桥把 kit\bridge\sql\<模块>\*.sql 编进程序：上一次留下、本次已删除的脚本不能再被编进去。
function Remove-PreviousScripts {
    $root = Assert-KitPath (Get-E2EPath "kit\bridge\sql")
    if (-not [System.IO.Directory]::Exists($root)) { return }
    Assert-NotReparse $root
    foreach ($sub in [System.IO.Directory]::GetDirectories($root)) {
        $dir = Assert-KitPath $sub
        Assert-NotReparse $dir
        foreach ($file in [System.IO.Directory]::GetFiles($dir)) { Remove-SqlFile $dir $file }
    }
}

# kit\bridge\private（可选的附加源码目录，存在时用 -ExtraSourceDir 一并编译）同样清掉：本次没有带的文件不能被编进去。
function Remove-PreviousSources {
    foreach ($rel in @("kit\bridge\src", "kit\bridge\private")) {
        $dir = Assert-KitPath (Get-E2EPath $rel)
        if (-not [System.IO.Directory]::Exists($dir)) { continue }
        Assert-NotReparse $dir
        foreach ($file in [System.IO.Directory]::GetFiles($dir)) {
            Remove-CsFile $dir $file
        }
    }
}

$zip = Assert-E2EPath $Archive
$out = [System.IO.Path]::GetFullPath((Get-E2EPath "out\"))
if (-not $zip.StartsWith($out, [System.StringComparison]::OrdinalIgnoreCase)) { throw "压缩包必须在 out 下" }
if (-not $zip.EndsWith("\bridge-src.zip", [System.StringComparison]::OrdinalIgnoreCase)) { throw "压缩包路径不对" }
if (-not [System.IO.File]::Exists($zip)) { throw "压缩包不存在" }
Assert-NotReparse $zip
$kit = Assert-KitPath (Get-E2EPath "kit")
Assert-NotReparse $kit
Assert-ArchiveEntries $zip $kit
Remove-PreviousSources
Remove-PreviousScripts
Expand-Archive -LiteralPath $zip -DestinationPath $kit -Force
$build = Assert-KitPath (Get-E2EPath "kit\bridge\build.ps1")
if (-not [System.IO.File]::Exists($build)) { throw "解压后没有 build.ps1" }
Write-Host "kit-extract ok"
