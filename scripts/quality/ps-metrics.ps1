# 用 PowerShell AST 度量 *.ps1。
# 文件和函数只数非空、非注释行。圈复杂度从 1 起，
# 每遇到 if/elseif 子句、switch 子句、for/foreach/while/do、catch、trap、-and、-or 加 1。
# 嵌套函数的决策点只计在内层；外层函数的行数仍包含内层源码。
# -Json 把结果打到标准输出，退出码 0。人类可读模式有违规则退出码 1。
# -Smell 只报告解析错误和圈复杂度（长度交给 sizes.py，避免门禁里同一条打两次）。

param(
    [Parameter(Mandatory = $true)][string]$Root,
    [switch]$Json,
    [switch]$Smell,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Paths
)

$ErrorActionPreference = 'Stop'
$FileLimit = 400
$FuncLimit = 60
$ComplexityLimit = 10

function Test-SkippedToken($tok) {
    if ($null -eq $tok) { return $true }
    $kind = [string]$tok.Kind
    return ($kind -eq 'Comment' -or $kind -eq 'NewLine' -or $kind -eq 'EndOfInput')
}

function Get-CodeLines($tokens) {
    $set = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($tok in @($tokens)) {
        if (Test-SkippedToken $tok) { continue }
        $line = $tok.Extent.StartLineNumber
        $end = $tok.Extent.EndLineNumber
        for ($i = $line; $i -le $end; $i++) {
            [void]$set.Add($i)
        }
    }
    return $set
}

function Test-LoopAst($node) {
    $name = $node.GetType().Name
    return (
        ($name -eq 'ForStatementAst') -or
        ($name -eq 'ForEachStatementAst') -or
        ($name -eq 'WhileStatementAst') -or
        ($name -eq 'DoWhileStatementAst') -or
        ($name -eq 'DoUntilStatementAst')
    )
}

function Get-SwitchWeight($node) {
    $count = @($node.Clauses).Count
    if ($null -ne $node.Default) { $count++ }
    return $count
}

function Get-NodeWeight($node) {
    $name = $node.GetType().Name
    if ($name -eq 'IfStatementAst') {
        return @($node.Clauses).Count
    }
    if ($name -eq 'SwitchStatementAst') {
        return (Get-SwitchWeight $node)
    }
    if (Test-LoopAst $node) {
        return 1
    }
    if ($name -eq 'CatchClauseAst' -or $name -eq 'TrapStatementAst') {
        return 1
    }
    if ($name -eq 'BinaryExpressionAst') {
        $op = [string]$node.Operator
        if ($op -eq 'And' -or $op -eq 'Or') {
            return 1
        }
    }
    return 0
}

function Test-UnderNested($node, $func, $skip) {
    $cur = $node
    while ($null -ne $cur -and $cur -ne $func) {
        if ($skip.Contains($cur)) { return $true }
        $cur = $cur.Parent
    }
    return $false
}

function Get-Complexity($func) {
    $nested = $func.FindAll({
            param($n)
            $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n -ne $func
        }, $true)
    $skip = New-Object 'System.Collections.Generic.HashSet[System.Management.Automation.Language.Ast]'
    foreach ($item in @($nested)) {
        if ($null -ne $item -and $item -ne $func) {
            [void]$skip.Add($item)
        }
    }
    $total = 1
    foreach ($node in @($func.FindAll({ param($n) $true }, $true))) {
        if (Test-UnderNested $node $func $skip) { continue }
        $total += Get-NodeWeight $node
    }
    return $total
}

function Get-SpanCount($set, [int]$start, [int]$end) {
    $n = 0
    for ($i = $start; $i -le $end; $i++) {
        if ($set.Contains($i)) { $n++ }
    }
    return $n
}

function Get-RelPath([string]$full, [string]$root) {
    $base = [System.IO.Path]::GetFullPath($root).TrimEnd([char[]]@('\', '/'))
    $item = [System.IO.Path]::GetFullPath($full)
    $prefix = $base + [System.IO.Path]::DirectorySeparatorChar
    if ($item.StartsWith($prefix, [System.StringComparison]::Ordinal)) {
        return ($item.Substring($prefix.Length) -replace '\\', '/')
    }
    return ($item -replace '\\', '/')
}

function Get-FuncInfo($fn, $code) {
    $start = [int]$fn.Extent.StartLineNumber
    $end = [int]$fn.Extent.EndLineNumber
    return [ordered]@{
        name       = [string]$fn.Name
        line       = $start
        lines      = (Get-SpanCount $code $start $end)
        complexity = (Get-Complexity $fn)
    }
}

function Add-ParseError($errors, $err) {
    if ($null -eq $err) { return }
    $msg = (([string]$err.Message) -replace '\s+', ' ').Trim()
    $errors.Add([ordered]@{
            line    = [int]$err.Extent.StartLineNumber
            message = $msg
        }) | Out-Null
}

function Add-Functions($funcs, $ast, $code) {
    if ($null -eq $ast) { return }
    $found = $ast.FindAll({
            param($n)
            $n -is [System.Management.Automation.Language.FunctionDefinitionAst]
        }, $true)
    foreach ($fn in @($found)) {
        if ($null -eq $fn) { continue }
        $funcs.Add((Get-FuncInfo $fn $code)) | Out-Null
    }
}

function Get-FileReport([string]$path, [string]$root) {
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $path, [ref]$tokens, [ref]$parseErrors)
    $code = Get-CodeLines $tokens
    $errors = New-Object System.Collections.Generic.List[object]
    foreach ($err in @($parseErrors)) {
        Add-ParseError $errors $err
    }
    $funcs = New-Object System.Collections.Generic.List[object]
    Add-Functions $funcs $ast $code
    return [ordered]@{
        path      = (Get-RelPath $path $root)
        lines     = $code.Count
        errors    = @($errors.ToArray())
        functions = @($funcs.ToArray())
    }
}

function Write-Hit($path, $line, $rule, $metric, $limit, $extra) {
    if ([string]::IsNullOrEmpty($extra)) {
        Write-Output "${path}:${line} ${rule} metric=${metric} limit=${limit}"
        return
    }
    Write-Output "${path}:${line} ${rule} metric=${metric} limit=${limit} ${extra}"
}

function Add-LengthHits($report, $hits) {
    if ([int]$report.lines -gt $FileLimit) {
        $text = Write-Hit $report.path 1 'file-lines' $report.lines $FileLimit ''
        $hits.Add($text) | Out-Null
    }
    foreach ($fn in @($report.functions)) {
        if ($null -eq $fn) { continue }
        if ([int]$fn.lines -gt $FuncLimit) {
            $text = Write-Hit $report.path $fn.line 'func-lines' $fn.lines $FuncLimit ("name=" + $fn.name)
            $hits.Add($text) | Out-Null
        }
    }
}

function Add-SmellHits($report, $hits) {
    foreach ($err in @($report.errors)) {
        if ($null -eq $err) { continue }
        $text = Write-Hit $report.path $err.line 'ps-parse' 1 0 ("message=" + $err.message)
        $hits.Add($text) | Out-Null
    }
    foreach ($fn in @($report.functions)) {
        if ($null -eq $fn) { continue }
        if ([int]$fn.complexity -gt $ComplexityLimit) {
            $text = Write-Hit $report.path $fn.line 'ps-complexity' $fn.complexity $ComplexityLimit ("name=" + $fn.name)
            $hits.Add($text) | Out-Null
        }
    }
}

function Get-HitLines($report, [bool]$smellOnly) {
    $hits = New-Object System.Collections.Generic.List[string]
    if (-not $smellOnly) {
        Add-LengthHits $report $hits
    }
    Add-SmellHits $report $hits
    return @($hits.ToArray())
}

function Add-ExplicitPath($list, [string]$raw) {
    if ([string]::IsNullOrWhiteSpace($raw)) { return }
    if (-not (Test-Path -LiteralPath $raw)) { return }
    $item = Get-Item -LiteralPath $raw
    if ($item.PSIsContainer) { return }
    if ($item.Extension -ne '.ps1') { return }
    $list.Add($item.FullName) | Out-Null
}

function Get-Targets([string]$root, [string[]]$paths) {
    $list = New-Object System.Collections.Generic.List[string]
    if ($null -ne $paths -and @($paths).Count -gt 0) {
        foreach ($raw in @($paths)) {
            Add-ExplicitPath $list $raw
        }
        return @($list | Sort-Object -Unique)
    }
    $skip = '[\\/](\.git|__pycache__|\.ruff_cache|node_modules|\.venv|out)[\\/]'
    Get-ChildItem -LiteralPath $root -Filter *.ps1 -Recurse -File |
        Where-Object { $_.FullName -notmatch $skip } |
        ForEach-Object { $list.Add($_.FullName) | Out-Null }
    return @($list | Sort-Object -Unique)
}

$targets = @(Get-Targets $Root $Paths)
$reports = New-Object System.Collections.Generic.List[object]
foreach ($target in @($targets)) {
    if ([string]::IsNullOrWhiteSpace($target)) { continue }
    $reports.Add((Get-FileReport $target $Root)) | Out-Null
}

if ($Json) {
    $payload = [ordered]@{
        version = $PSVersionTable.PSVersion.ToString()
        results = @($reports.ToArray())
    }
    $jsonText = $payload | ConvertTo-Json -Depth 8 -Compress
    [Console]::Out.WriteLine($jsonText)
    exit 0
}

$hitLines = New-Object System.Collections.Generic.List[string]
foreach ($report in $reports.ToArray()) {
    foreach ($line in @(Get-HitLines $report ([bool]$Smell))) {
        if (-not [string]::IsNullOrWhiteSpace($line)) {
            $hitLines.Add($line) | Out-Null
        }
    }
}
if ($hitLines.Count -eq 0) {
    Write-Output 'ps OK'
    exit 0
}
foreach ($line in $hitLines.ToArray()) {
    Write-Output $line
}
Write-Output ("ps FAIL " + $hitLines.Count)
exit 1
