#Requires -Version 7.4
#Requires -PSEdition Core
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$script:SaleSql = @"
SELECT TOP (1) N'sale=' + CONVERT(varchar(20), ID)
FROM dbo.SO_SOMain
WHERE (cVerifier IS NULL OR LTRIM(RTRIM(cVerifier)) = N'')
  AND dverifydate IS NULL
  AND (iswfcontrolled = 0 OR iswfcontrolled IS NULL)
  AND (bReturnFlag = 0 OR bReturnFlag IS NULL)
ORDER BY ID;
"@

$script:DispatchSql = @"
SELECT TOP (1) N'dispatch=' + CONVERT(varchar(20), DLID)
FROM dbo.DispatchList
WHERE cVouchType = N'05'
  AND bFirst = 0
  AND bReturnFlag = 0
  AND (cVerifier IS NULL OR LTRIM(RTRIM(cVerifier)) = N'')
  AND dverifydate IS NULL
  AND (iswfcontrolled = 0 OR iswfcontrolled IS NULL)
ORDER BY DLID;
"@

# 测试账套常复制自正式账套,发货单可能几乎全部已审核。没有未审核的就选一张已审核、且下游未出库/未开票/未退货的蓝字发货单,
# 按「先弃审再审核」测;有下游的单据 U8 本来就不允许弃审。
$script:DispatchVerifiedSql = @"
SELECT TOP (1) N'verified=' + CONVERT(varchar(20), d.DLID)
FROM dbo.DispatchList AS d
WHERE d.cVouchType = N'05'
  AND d.bFirst = 0
  AND d.bReturnFlag = 0
  AND LTRIM(RTRIM(ISNULL(d.cVerifier, N''))) <> N''
  AND d.dverifydate IS NOT NULL
  AND (d.iswfcontrolled = 0 OR d.iswfcontrolled IS NULL)
  AND NOT EXISTS (
    SELECT 1 FROM dbo.DispatchLists AS l
    WHERE l.DLID = d.DLID
      AND (ISNULL(l.fOutQuantity, 0) <> 0 OR ISNULL(l.iSettleQuantity, 0) <> 0
           OR ISNULL(l.iRetQuantity, 0) <> 0 OR ISNULL(l.fVeriBillQty, 0) <> 0)
  )
ORDER BY d.DLID DESC;
"@

# 错误路径用:已审核、已有出库的蓝字发货单,U8 应拒绝弃审。
$script:DispatchDownstreamSql = @"
SELECT TOP (1) N'downstream=' + CONVERT(varchar(20), d.DLID)
FROM dbo.DispatchList AS d
WHERE d.cVouchType = N'05' AND d.bFirst = 0 AND d.bReturnFlag = 0
  AND LTRIM(RTRIM(ISNULL(d.cVerifier, N''))) <> N'' AND d.dverifydate IS NOT NULL
  AND (d.iswfcontrolled = 0 OR d.iswfcontrolled IS NULL)
  AND EXISTS (SELECT 1 FROM dbo.DispatchLists AS l WHERE l.DLID = d.DLID AND ISNULL(l.fOutQuantity, 0) <> 0)
ORDER BY d.DLID DESC;
"@

# 四条挑单 SQL 合成一批、一次 sqlcmd 连接：每条结果带「键=」前缀，按键取第一行。
$script:PickBatch = "SET NOCOUNT ON;`n" + (@($script:SaleSql, $script:DispatchSql, $script:DispatchVerifiedSql, $script:DispatchDownstreamSql) -join "`n")

function Read-PickIds([string]$Query) {
    $ids = @{}
    foreach ($line in @(Get-SqlText (Get-DataDb) $Query)) {
        $text = ([string]$line).Trim()
        if ($text -match '^(sale|dispatch|verified|downstream)=([1-9][0-9]*)$' -and -not $ids.ContainsKey($Matches[1])) {
            $ids[$Matches[1]] = [int]$Matches[2]
        }
    }
    return $ids
}

$ids = Read-PickIds $script:PickBatch
$dispatch = $ids["dispatch"]
$dispatchMode = "verify-first"
if ($null -eq $dispatch) {
    $dispatch = $ids["verified"]
    $dispatchMode = "unverify-first"
}
$doc = [ordered]@{
    sale_id = $ids["sale"]
    sale_mode = "verify-first"
    dispatch_id = $dispatch
    dispatch_mode = $dispatchMode
    dispatch_downstream_id = $ids["downstream"]
}
Write-Utf8 (Get-E2EPath "out\pick-docs.json") ($doc | ConvertTo-Json -Depth 4)
Write-Host "pick-docs ok"
