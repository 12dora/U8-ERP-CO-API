#Requires -Version 7.4
#Requires -PSEdition Core
# 指纹 SQL 与解析,fingerprint.ps1 和 snapshot-revert.ps1 共用(后者在还原的同一会话里算指纹)。
# 空表先用 EXISTS 判出来,不再全表算 CHECKSUM_AGG:记 0 行、校验 NULL,与原来对空表算出的结果
# (COUNT_BIG 为 0、CHECKSUM_AGG 为 NULL)完全一样,比对语义不变。非空表照旧一条语句同时取行数和校验。
$script:FingerSql = @"
SET NOCOUNT ON;
CREATE TABLE #fp (
  schema_name sysname NOT NULL,
  table_name sysname NOT NULL,
  row_count bigint NULL,
  checksum_value int NULL,
  error_text nvarchar(300) NOT NULL
);
DECLARE @schema sysname, @table sysname, @sql nvarchar(max), @rows bigint, @sum int, @has bit;
DECLARE c CURSOR LOCAL FAST_FORWARD FOR
  SELECT s.name, t.name
  FROM sys.tables AS t
  INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id
  WHERE t.is_ms_shipped = 0
  ORDER BY s.name, t.name;
OPEN c;
FETCH NEXT FROM c INTO @schema, @table;
WHILE @@FETCH_STATUS = 0
BEGIN
  BEGIN TRY
    SET @rows = NULL;
    SET @sum = NULL;
    SET @has = 1;
    SET @sql = N'SELECT @e = CASE WHEN EXISTS (SELECT 1 FROM '
      + QUOTENAME(@schema) + N'.' + QUOTENAME(@table) + N') THEN 1 ELSE 0 END';
    EXEC sp_executesql @sql, N'@e bit OUTPUT', @e = @has OUTPUT;
    IF @has = 0
      SET @rows = 0;
    ELSE
    BEGIN
      SET @sql = N'SELECT @r = COUNT_BIG(*), @c = CHECKSUM_AGG(BINARY_CHECKSUM(*)) FROM '
        + QUOTENAME(@schema) + N'.' + QUOTENAME(@table);
      EXEC sp_executesql @sql, N'@r bigint OUTPUT, @c int OUTPUT', @r = @rows OUTPUT, @c = @sum OUTPUT;
    END
    INSERT INTO #fp (schema_name, table_name, row_count, checksum_value, error_text)
      VALUES (@schema, @table, @rows, @sum, N'');
  END TRY
  BEGIN CATCH
    INSERT INTO #fp (schema_name, table_name, row_count, checksum_value, error_text)
      VALUES (@schema, @table, NULL, NULL, LEFT(REPLACE(REPLACE(ERROR_MESSAGE(), CHAR(31), N' '), CHAR(13), N' '), 300));
  END CATCH
  FETCH NEXT FROM c INTO @schema, @table;
END
CLOSE c;
DEALLOCATE c;
SELECT schema_name + CHAR(31) + table_name + CHAR(31)
  + ISNULL(CONVERT(varchar(30), row_count), N'') + CHAR(31)
  + ISNULL(CONVERT(varchar(20), checksum_value), N'') + CHAR(31)
  + error_text
FROM #fp
ORDER BY schema_name, table_name;
"@

function Add-FingerRow($Rows, [string]$Line) {
    $text = $Line.Trim()
    if ($text.Length -eq 0) { return }
    if (-not $text.Contains([char]31)) { return }
    $bits = $text.Split([char]31)
    if ($bits.Count -ne 5) { throw "指纹行无法解析" }
    $row = [ordered]@{
        schema = $bits[0]
        name = $bits[1]
        rows = $null
        checksum = $null
        error = $bits[4]
    }
    if ($bits[2].Length -gt 0) { $row.rows = [int64]$bits[2] }
    if ($bits[3].Length -gt 0) { $row.checksum = [int]$bits[3] }
    $Rows.Add($row)
}


function ConvertTo-FingerDoc([string]$Database, $Lines) {
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($line in @($Lines)) { Add-FingerRow $rows ([string]$line) }
    return [ordered]@{ database = $Database; tables = $rows }
}
