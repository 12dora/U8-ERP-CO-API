#Requires -Version 7.4
#Requires -PSEdition Core
# 开发者自己用的文本守卫，不是沙箱。窗口检查在本脚本里做，不靠 Linux 标记。
param(
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string]$File
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

Assert-AllowedDb $Database
Assert-LiveWindow
$full = Assert-E2EPath $File
$out = [System.IO.Path]::GetFullPath((Get-E2EPath "out\"))
if (-not $full.StartsWith($out, [System.StringComparison]::OrdinalIgnoreCase)) { throw "SQL 文件必须在 out 下" }
if (-not [System.IO.File]::Exists($full)) { throw "SQL 文件不存在" }
$query = [System.IO.File]::ReadAllText($full)
if ([string]::IsNullOrWhiteSpace($query)) { throw "SQL 为空" }
Assert-SqlGuard $query
$exe = Get-SqlExe
& $exe -S ([string]$script:Cfg.SqlServer) -E -b -W -s "`t" -w 65535 -f 65001 -x -X1 -d $Database -i $full
if ($LASTEXITCODE -ne 0) { throw ("sqlcmd 退出码 " + $LASTEXITCODE) }
