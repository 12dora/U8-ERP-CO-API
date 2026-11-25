#Requires -Version 7.4
#Requires -PSEdition Core
# U8TaskConfig 的列清单。taskcfg-dump 和 snapshot-revert 共用。

function Get-TaskCols {
    # 全部转 nvarchar、分隔符用 NCHAR(31)：还原批先连 master 再 USE 账套库，varchar 的 Name 与 CHAR(31) 跨排序规则拼接时，
    # 末尾的中文双字节字符会和分隔符粘在一起（实测「U8推送消息到友空间」这类行少一列，还原中止）。
    $lines = @(
        "CONVERT(nvarchar(400), Name) + NCHAR(31)",
        "  + ISNULL(CONVERT(nvarchar(23), NextTime, 121), N'') + NCHAR(31)",
        "  + ISNULL(CONVERT(nvarchar(23), PreTime, 121), N'') + NCHAR(31)",
        "  + CONVERT(nvarchar(20), CONVERT(bigint, pubufts)) + NCHAR(31)",
        "  + ISNULL(CONVERT(nvarchar(400), AppServer), N'') + NCHAR(31)",
        "  + CONVERT(nvarchar(12), BINARY_CHECKSUM(Name, Sourcekey, State, Description, Creator, Frequency, Type,",
        "      bInterrupted, bShow, changer, isExcuteShedule, StartTime, EndTime))"
    )
    return ($lines -join "`r`n")
}

function Get-TaskQuery {
    return "SET NOCOUNT ON;`r`nSELECT " + (Get-TaskCols) + "`r`nFROM dbo.U8TaskConfig ORDER BY Name;"
}

function Get-TaskBatchSql {
    $lines = @(
        "DECLARE @started datetime2(3) = SYSDATETIME();",
        "SELECT N'U8REVERT_TIME' + CHAR(31) + CONVERT(varchar(23), @started, 121);",
        ("SELECT N'U8REVERT_TASK' + NCHAR(31) + " + (Get-TaskCols)),
        "FROM dbo.U8TaskConfig ORDER BY Name;"
    )
    return ($lines -join "`r`n")
}

function ConvertTo-TaskRow([string]$Text) {
    if (-not $Text.Contains([char]31)) { return $null }
    $bits = $Text.Split([char]31)
    if ($bits.Count -ne 6) { throw ("U8TaskConfig 行无法解析（" + $bits.Count + " 列）：" + ($Text -replace [char]31, '|')) }
    return [ordered]@{
        name = $bits[0]
        next = $bits[1]
        pre = $bits[2]
        ts = $bits[3]
        app = $bits[4]
        rest = $bits[5]
    }
}

function ConvertTo-TaskRows($Lines) {
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($line in @($Lines)) {
        $text = ([string]$line).Trim()
        if ($text.Length -eq 0) { continue }
        $row = ConvertTo-TaskRow $text
        if ($null -ne $row) { $rows.Add($row) }
    }
    return $rows
}
