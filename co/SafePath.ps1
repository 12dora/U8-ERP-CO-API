#Requires -Version 7.4
#Requires -PSEdition Core
# 路径断言。调用方先保存自己的目录，再 dot-source，然后 Initialize-Staging。
# 运行目录（-RuntimeRoot）是桥读写 config.json、secret.hex、日志的地方；U8 安装目录（-U8Home）只读不写。

function Get-DefaultRuntimeRoot {
    $data = [Environment]::GetFolderPath([Environment+SpecialFolder]::CommonApplicationData)
    if ([string]::IsNullOrWhiteSpace($data)) { $data = "C:\ProgramData" }
    return (Join-Path $data "U8Co\u8co")
}

function Test-PathWithin([string]$Full, [string]$Dir) {
    $root = $Dir.TrimEnd("\")
    if ($Full.TrimEnd("\").Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $Full.StartsWith($root + "\", [System.StringComparison]::OrdinalIgnoreCase)
}

# 带盘符的绝对路径，不是盘符根目录。返回去掉末尾反斜杠的完整路径。
function Resolve-DriveDir([string]$Path, [string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw ($Name + " 不能为空") }
    if ($Path -notmatch '^[A-Za-z]:[\\/]') { throw ($Name + " 必须是带盘符的绝对路径：" + $Path) }
    # 引号会拆开服务 binPath 里的 --root "..."；.NET 8 的 GetInvalidPathChars 不含引号，单独查。
    if ($Path.Contains('"') -or $Path.IndexOfAny([System.IO.Path]::GetInvalidPathChars()) -ge 0) {
        throw ($Name + " 含引号或非法字符：" + $Path)
    }
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    if ($full.Length -le 2) { throw ($Name + " 不能是盘符根目录：" + $Path) }
    return $full
}

# 不碰 U8 安装目录。U8 装在系统盘以外的盘时，整个盘都不碰（那里通常还有账套数据）。
function Assert-DriveSafe([string]$Full) {
    if (Test-PathWithin $Full $script:U8HomeRoot) {
        throw ("拒绝使用 U8 安装目录下的路径：" + $script:U8HomeRoot)
    }
    $drive = $script:U8Drive
    if ($drive.Length -gt 0 -and $Full.StartsWith($drive, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw ("拒绝使用 U8 安装盘 " + $drive + " 上的路径")
    }
}

function Set-U8Home([string]$U8Home) {
    $full = Resolve-DriveDir $U8Home "U8 安装目录"
    $script:U8HomeRoot = $full
    $drive = [System.IO.Path]::GetPathRoot($full)
    $system = [System.IO.Path]::GetPathRoot([Environment]::SystemDirectory)
    $script:U8Drive = ""
    if (-not $drive.Equals($system, [System.StringComparison]::OrdinalIgnoreCase)) { $script:U8Drive = $drive }
}

function Assert-RuntimeDepth([string]$Full) {
    if ($Full.Substring(3).Split("\").Count -lt 2) { throw ("运行目录至少要在盘符下两级：" + $Full) }
}

function Set-RuntimeRoot([string]$RuntimeRoot) {
    $full = Resolve-DriveDir $RuntimeRoot "运行目录"
    Assert-RuntimeDepth $full
    Assert-DriveSafe $full
    $script:RuntimeRoot = $full
}

function Initialize-Staging([string]$ScriptDir, [string]$RuntimeRoot = "", [string]$U8Home = "C:\U8SOFT") {
    Set-U8Home $U8Home
    if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) { $RuntimeRoot = Get-DefaultRuntimeRoot }
    Set-RuntimeRoot $RuntimeRoot
    $full = [System.IO.Path]::GetFullPath($ScriptDir)
    Assert-DriveSafe $full
    if (-not [System.IO.Directory]::Exists($full)) { throw "脚本目录不存在" }
    $script:StagingRoot = $full
}

function Assert-UnderStaging([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($script:StagingRoot)) { throw "尚未初始化脚本目录" }
    $full = [System.IO.Path]::GetFullPath($Path)
    Assert-DriveSafe $full
    $prefix = $script:StagingRoot.TrimEnd("\") + "\"
    if ($full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $full }
    throw "输出路径不在脚本目录内"
}

function Assert-UnderRuntime([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($script:RuntimeRoot)) { throw "尚未初始化运行目录" }
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = $script:RuntimeRoot
    if ($full.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) { return $full }
    $prefix = $root.TrimEnd("\") + "\"
    if ($full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $full }
    throw ("路径不在 " + $prefix + " 之下")
}

function Assert-NoReparse([string]$Path) {
    $attr = [System.IO.File]::GetAttributes($Path)
    $bit = [System.IO.FileAttributes]::ReparsePoint
    if (($attr -band $bit) -eq $bit) { throw ("拒绝继续：重解析点 " + $Path) }
}

function Get-OwnerSid([string]$Path) {
    # pwsh 7(.NET 8)的 FileInfo/DirectoryInfo 没有实例方法 GetAccessControl;Get-Acl -LiteralPath 两边都能用。
    $owner = (Get-Acl -LiteralPath $Path).GetOwner([System.Security.Principal.SecurityIdentifier])
    return [string]$owner.Value
}

function Assert-OneOwner([string]$Path) {
    $sid = Get-OwnerSid $Path
    if ($sid -eq "S-1-5-18") { return }
    if ($sid -eq "S-1-5-32-544") { return }
    # 常见于别的管理员账号手工放进来的文件。核对内容无误后把整棵树的所有者改回 Administrators 再重跑。
    throw ("拒绝继续：所有者不是 SYSTEM 或 Administrators " + $Path + " " + $sid + "。核对这些文件确实是自己放的之后，以管理员身份执行 icacls.exe `"" + $script:RuntimeRoot + "`" /setowner *S-1-5-32-544 /T /C 再重跑")
}

function Assert-TreeItem([string]$Path) {
    Assert-NoReparse $Path
    Assert-OneOwner $Path
}

function Push-Child($Pending, [string]$Child) {
    Assert-TreeItem $Child
    if (-not [System.IO.Directory]::Exists($Child)) { return }
    $Pending.Enqueue($Child)
}

function Assert-ExistingOwners([string]$Root) {
    if (-not [System.IO.Directory]::Exists($Root)) { return }
    $pending = New-Object System.Collections.Generic.Queue[string]
    $pending.Enqueue($Root)
    while ($pending.Count -gt 0) {
        $dir = $pending.Dequeue()
        Assert-TreeItem $dir
        foreach ($child in [System.IO.Directory]::GetFileSystemEntries($dir)) {
            Push-Child $pending $child
        }
    }
}

# 运行目录的每一级上级（盘符根到直接上级）都要可信：谁能把其中一级改名、删掉或改权限，
# 谁就能把整个运行目录换成自己的，程序、config.json、secret.hex、u8Home 都由 LocalSystem 使用。
# 别人在上级里新建东西不要紧（盘符根、ProgramData 本来就允许）：新建的归他自己，换不掉已有的这一支。
# 与桥启动时 Paths.cs 的检查相同，改一处要同步另一处。
$script:TrustedSids = @("S-1-5-18", "S-1-5-32-544", "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")
# DELETE、FILE_DELETE_CHILD、WRITE_DAC、WRITE_OWNER、GENERIC_ALL（完全控制、修改都含其中之一）。
$script:AncestorDanger = 0x10000 -bor 0x40 -bor 0x40000 -bor 0x80000 -bor 0x10000000

function Get-AncestorDirs([string]$Full) {
    $list = New-Object System.Collections.Generic.List[string]
    $dir = [System.IO.Path]::GetDirectoryName($Full)
    while (-not [string]::IsNullOrEmpty($dir)) {
        $list.Insert(0, $dir)
        $dir = [System.IO.Path]::GetDirectoryName($dir)
    }
    return , $list.ToArray()
}

function Test-AncestorRule($Rule, [int]$Mask) {
    if ($Rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) { return $false }
    # 只继承给子项的 ACE 不作用于这一级本身。
    $inheritOnly = [int][System.Security.AccessControl.PropagationFlags]::InheritOnly
    if (([int]$Rule.PropagationFlags -band $inheritOnly) -ne 0) { return $false }
    if (([int]$Rule.FileSystemRights -band $Mask) -eq 0) { return $false }
    $sid = [string]$Rule.IdentityReference.Value
    if ($sid -eq "S-1-3-0") { return $false }
    return ($script:TrustedSids -notcontains $sid)
}

function Assert-SafeAncestor([string]$Dir) {
    Assert-NoReparse $Dir
    $acl = Get-Acl -LiteralPath $Dir
    $owner = [string]$acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    if ($script:TrustedSids -notcontains $owner) { throw ("拒绝继续：上级目录的所有者不可信 " + $Dir + " " + $owner) }
    $mask = $script:AncestorDanger
    # 盘符根不能改名或删除，只看删子项、改权限、改所有者。
    if ($Dir.Length -le 3) { $mask = $mask -band (-bnot 0x10000) }
    foreach ($rule in $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
        if (Test-AncestorRule $rule $mask) {
            throw ("拒绝继续：上级目录 " + $Dir + " 允许 " + $rule.IdentityReference.Value + " 删除、改名或改权限")
        }
    }
}

# 从盘符根往下走到 $Full 的直接上级；不存在的那一级及以下还没建，不用查。
function Assert-SafeAncestors([string]$Full) {
    foreach ($dir in (Get-AncestorDirs $Full)) {
        if (-not [System.IO.Directory]::Exists($dir)) { return }
        Assert-SafeAncestor $dir
    }
}

# 本次要新建的各级目录（上级里还不存在的几级，加运行目录本身），从上往下排。
function Get-MissingLevels([string]$Full) {
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($dir in (Get-AncestorDirs $Full)) {
        if (-not [System.IO.Directory]::Exists($dir)) { $list.Add($dir) }
    }
    if (-not [System.IO.Directory]::Exists($Full)) { $list.Add($Full) }
    return , $list.ToArray()
}

# 刚建出的一级：所有者只能是 SYSTEM、Administrators、TrustedInstaller 或当前账号
# （管理员令牌的默认所有者可能是账号本身，如 UAC 关闭或 OpenSSH 登录），否则是别人抢先建的，拒绝。
# 再去掉继承（CREATOR OWNER 会给当前账号一条完全控制）、只留 SYSTEM 和 Administrators，所有者改成 Administrators。
function Lock-NewLevel([string]$Dir) {
    Assert-NoReparse $Dir
    $owner = Get-OwnerSid $Dir
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($script:TrustedSids -notcontains $owner -and $owner -ne $me) {
        throw ("拒绝继续：刚建的目录所有者是 " + $owner + "，可能被别人抢先建出 " + $Dir)
    }
    & icacls.exe $Dir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F"
    if ($LASTEXITCODE -ne 0) { throw ("icacls 授权失败 " + $Dir) }
    & icacls.exe $Dir /setowner "*S-1-5-32-544"
    if ($LASTEXITCODE -ne 0) { throw ("icacls 设置所有者失败 " + $Dir) }
}

# 先查上级和已有的整棵树，再建目录、锁 ACL；锁完再查一遍，
# 抓住检查与新建之间别人抢先建出的上级目录或放进来的东西（所有者会是他自己）。
# 本次新建的各级先改好所有者再复查，最后才递归改整棵树的所有者，免得复查前把别人放进来的东西也改成 Administrators。
function Lock-U8CoRoot {
    Assert-SafeAncestors $script:RuntimeRoot
    Assert-ExistingOwners $script:RuntimeRoot
    $created = Get-MissingLevels $script:RuntimeRoot
    [void][System.IO.Directory]::CreateDirectory($script:RuntimeRoot)
    foreach ($dir in $created) { Lock-NewLevel $dir }
    & icacls.exe $script:RuntimeRoot /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F"
    if ($LASTEXITCODE -ne 0) { throw "icacls 授权失败" }
    Assert-SafeAncestors $script:RuntimeRoot
    Assert-ExistingOwners $script:RuntimeRoot
    Set-RootOwner
}

function Set-RootOwner {
    & icacls.exe $script:RuntimeRoot /setowner "*S-1-5-32-544" /T
    if ($LASTEXITCODE -ne 0) { throw "icacls 设置所有者失败" }
}

# 读 config.json 之前先核对运行目录的上级和整棵树：卸载要按它删 urlacl、删 bin 里的程序，
# 也按它的 u8Home 决定保护哪个盘，不能读一个被人换掉的目录里的文件。
function Read-ConfigU8Home {
    $path = Assert-UnderRuntime (Join-Path $script:RuntimeRoot "config.json")
    Assert-SafeAncestors $script:RuntimeRoot
    Assert-ExistingOwners $script:RuntimeRoot
    if (-not [System.IO.File]::Exists($path)) { return "" }
    $cfg = Get-Content -LiteralPath $path -Encoding UTF8 -Raw | ConvertFrom-Json
    return [string]$cfg.u8Home
}

# 安装、卸载用：config.json 里已有 u8Home 时以它为准，重跑不必再给 -U8Home；显式给了不一致的值就拒绝。
# 先只定运行目录、读 config.json，再做盘符保护：否则缺省的 D:\U8SOFT 会在读到真正的 u8Home 之前
# 就拒绝放在 D: 上的脚本目录或运行目录。
function Initialize-BridgeStaging([string]$ScriptDir, [string]$RuntimeRoot, [string]$U8Home, [bool]$Explicit) {
    if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) { $RuntimeRoot = Get-DefaultRuntimeRoot }
    $full = Resolve-DriveDir $RuntimeRoot "运行目录"
    # 读 config.json 要遍历整棵树：先挡住层数不够和落在给定 U8 安装目录里的路径（如把 -Root 误写成 U8 目录）。
    # 整块盘的保护要等读到 config.json 的 u8Home 才知道是哪块盘，只有显式给了 -U8Home 才提前做。
    Assert-RuntimeDepth $full
    Set-U8Home $U8Home
    if ($Explicit) { Assert-DriveSafe $full }
    elseif (Test-PathWithin $full $script:U8HomeRoot) { throw ("拒绝使用 U8 安装目录下的路径：" + $script:U8HomeRoot) }
    $script:RuntimeRoot = $full
    $saved = Read-ConfigU8Home
    if (-not [string]::IsNullOrWhiteSpace($saved)) {
        $full = Resolve-DriveDir $saved "config.json 的 u8Home"
        $given = Resolve-DriveDir $U8Home "U8 安装目录"
        if ($Explicit -and -not $full.Equals($given, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw ("-U8Home 与 config.json 的 u8Home 不一致：" + $U8Home + " / " + $saved)
        }
        if (-not $full.Equals($given, [System.StringComparison]::OrdinalIgnoreCase)) { Write-Host ("按 config.json 的 u8Home 保护 " + $full) }
        $U8Home = $full
    }
    Initialize-Staging $ScriptDir $RuntimeRoot $U8Home
}

# 服务名撞上别的服务（拼错或同名）时，不能停它、改它的 binPath 或删掉它。
# 已有服务的可执行文件必须正是 <root>\bin\u8co-bridge.exe（忽略大小写，允许引号和参数）。
function Get-ServiceExe([string]$PathName) {
    $text = $PathName.Trim()
    if ($text.StartsWith('"')) {
        $end = $text.IndexOf('"', 1)
        if ($end -lt 0) { return "" }
        return $text.Substring(1, $end - 1)
    }
    $space = $text.IndexOf(" ")
    if ($space -lt 0) { return $text }
    return $text.Substring(0, $space)
}

# 返回服务是否已存在。调用方先校验过服务名的字符集，拼进 WQL 是安全的。
function Assert-BridgeService([string]$Name) {
    $svc = Get-CimInstance -ClassName Win32_Service -Filter ("Name='" + $Name + "'")
    if ($null -eq $svc) { return $false }
    $want = Join-Path $script:RuntimeRoot "bin\u8co-bridge.exe"
    $exe = Get-ServiceExe ([string]$svc.PathName)
    if ($exe.Equals($want, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    throw ("服务 " + $Name + " 已存在但不是这个运行目录的桥（" + $svc.PathName + "），拒绝停止、改配置或删除。核对 -ServiceName 和 -Root")
}
