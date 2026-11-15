-- U8 存货核算 取消期末处理。由桥在请求连接、请求事务里整批执行（不分 GO）；参数取自调用方先建好的 #ia_args
-- （y、m、keep_date、accounter、on_uncosted）。拒绝时 THROW 50000–50099，桥按编号转成 409。
-- 依据：测试账套实测（回滚事务），见 docs/api-reference.md「存货核算记账与期末处理」。
-- 取消期末处理. No dedicated proc. ONE batch. No GO.
SET NOCOUNT ON;

DECLARE @y smallint; SELECT @y = y FROM #ia_args;
DECLARE @m tinyint; SELECT @m = m FROM #ia_args;

IF EXISTS (SELECT 1 FROM GL_mend WHERE iyear=@y AND iperiod=@m AND ISNULL(bflag_IA,0)=1)
  THROW 50030, N'IA month already closed (bflag_IA=1). Unclose first.', 1;

IF NOT EXISTS (SELECT 1 FROM sysobjects WHERE name=N'IA_PerdealInventory' AND xtype=N'U')
BEGIN
  SELECT cinvcode INTO IA_PerdealInventory FROM Inventory WHERE 1=0;
  INSERT INTO IA_PerdealInventory(cinvcode)
  SELECT DISTINCT cInvCode FROM IA_Summary
  WHERE iYear=@y AND iMonth=@m AND ISNULL(cInvCode,N'')<>N'';
END
IF NOT EXISTS (SELECT 1 FROM IA_PerdealInventory)
  INSERT INTO IA_PerdealInventory(cinvcode)
  SELECT DISTINCT cInvCode FROM IA_Summary
  WHERE iYear=@y AND iMonth=@m AND ISNULL(cInvCode,N'')<>N'';

-- type-21 出库调整 created by period-end. Bus type not distinguishable in the observed rows; delete all type-21 of the month. INFERRED.
IF OBJECT_ID(N'IA_Subsidiary_Item', N'U') IS NOT NULL
  DELETE FROM IA_Subsidiary_Item
  WHERE iSubID IN (
    SELECT AutoID FROM IA_Subsidiary
    WHERE cVouType=N'21' AND iYear=@y AND iMonth=@m
      AND cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory));
IF OBJECT_ID(N'IA_Subsidiary_ItemOut', N'U') IS NOT NULL
  DELETE FROM IA_Subsidiary_ItemOut
  WHERE iSubID IN (
    SELECT AutoID FROM IA_Subsidiary
    WHERE cVouType=N'21' AND iYear=@y AND iMonth=@m
      AND cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory));
IF OBJECT_ID(N'ia_subsidiary_STSub', N'U') IS NOT NULL
  DELETE ia_subsidiary_STSub
  FROM ia_subsidiary_STSub
  INNER JOIN IA_Subsidiary ON IA_Subsidiary.autoid = ia_subsidiary_STSub.iIASubID
  WHERE cVouType=N'21' AND iYear=@y AND iMonth=@m
    AND cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory);

DELETE FROM IA_Subsidiary
WHERE cVouType=N'21' AND iYear=@y AND iMonth=@m
  AND cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory);

DELETE justinvouch
FROM justinvouch
LEFT JOIN justinvouchs ON justinvouch.cjvcode = justinvouchs.cjvcode
WHERE justinvouchs.cjvcode IS NULL;

UPDATE IA_Subsidiary
SET iOutCost=NULL, iAOutPrice=NULL, CostSource=NULL
WHERE iYear=@y AND iMonth=@m
  AND ISNULL(bMoneyFlag,0)=0 AND bRdFlag=0
  AND cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory);

UPDATE rdrecords09 SET iunitcost=NULL, iPrice=NULL
FROM rdrecords09
INNER JOIN IA_Subsidiary a ON rdrecords09.autoid=a.id AND a.cvoutype=N'09'
WHERE a.iYear=@y AND a.iMonth=@m AND ISNULL(a.bMoneyFlag,0)=0
  AND a.cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory);

UPDATE rdrecords11 SET iunitcost=NULL, iPrice=NULL
FROM rdrecords11
INNER JOIN IA_Subsidiary a ON rdrecords11.autoid=a.id AND a.cvoutype=N'11'
WHERE a.iYear=@y AND a.iMonth=@m AND ISNULL(a.bMoneyFlag,0)=0
  AND a.cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory);

UPDATE rdrecords32 SET iunitcost=NULL, iPrice=NULL
FROM rdrecords32
INNER JOIN IA_Subsidiary a ON rdrecords32.autoid=a.id AND a.cvoutype IN (N'32',N'3201')
WHERE a.iYear=@y AND a.iMonth=@m AND ISNULL(a.bMoneyFlag,0)=0
  AND a.cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory);

UPDATE IA_Summary
SET iPeriod=0, iDifRate=NULL, iUnitPrice=NULL
WHERE iYear=@y AND iMonth=@m
  AND cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory);

SELECT N'summary_iPeriod1' k, COUNT(*) n FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iPeriod,0)=1
UNION ALL SELECT N'summary_iPeriod0', COUNT(*) FROM IA_Summary WHERE iYear=@y AND iMonth=@m AND ISNULL(iPeriod,0)=0
UNION ALL SELECT N'type21_left', COUNT(*) FROM IA_Subsidiary WHERE iYear=@y AND iMonth=@m AND cVouType=N'21'
UNION ALL SELECT N'out_null_cost', COUNT(*) FROM IA_Subsidiary
  WHERE iYear=@y AND iMonth=@m AND bRdFlag=0 AND ISNULL(bMoneyFlag,0)=0 AND iOutCost IS NULL;
