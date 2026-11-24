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

-- 期末处理生成的出库调整（IA_sp_PerdealJustVouch / IA_sp_PerdealRedCostJustVouch：明细账 cBusType=期末处理，调整单 cAuto=TRUE，
-- 调整单行生成时已盖记账人）。只删这些：手工录入的 21、记账时生成的 21（cBusType 空 / 记账）是 ia/post 记的账，
-- 取消期末处理不碰，它们由 ia/unpost 恢复。明细账行连同调整单的分项、表体行、没有表体行的表头一起删。
IF OBJECT_ID(N'tempdb..#pe21') IS NOT NULL DROP TABLE #pe21;
SELECT AutoID, JustID, cVouCode INTO #pe21
FROM IA_Subsidiary
WHERE cVouType=N'21' AND cBusType=N'期末处理' AND iYear=@y AND iMonth=@m
  AND cInvCode IN (SELECT cInvCode FROM IA_PerdealInventory);

IF OBJECT_ID(N'IA_Subsidiary_Item', N'U') IS NOT NULL
  DELETE x FROM IA_Subsidiary_Item x INNER JOIN #pe21 p ON x.iSubID=p.AutoID;
IF OBJECT_ID(N'IA_Subsidiary_ItemOut', N'U') IS NOT NULL
  DELETE x FROM IA_Subsidiary_ItemOut x INNER JOIN #pe21 p ON x.iSubID=p.AutoID;
IF OBJECT_ID(N'ia_subsidiary_STSub', N'U') IS NOT NULL
  DELETE x FROM ia_subsidiary_STSub x INNER JOIN #pe21 p ON x.iIASubID=p.AutoID;

DELETE x FROM IA_Subsidiary x INNER JOIN #pe21 p ON x.AutoID=p.AutoID;

IF OBJECT_ID(N'IA_JustInVouchs_Item', N'U') IS NOT NULL
  DELETE x FROM IA_JustInVouchs_Item x INNER JOIN #pe21 p ON x.iJustAutoID=p.JustID;
DELETE l FROM JustInVouchs l INNER JOIN #pe21 p ON l.AutoID=p.JustID;
DELETE h FROM justinvouch h
WHERE h.cjvcode IN (SELECT cVouCode FROM #pe21)
  AND NOT EXISTS (SELECT 1 FROM justinvouchs l WHERE l.cjvcode=h.cjvcode);
DROP TABLE #pe21;

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
