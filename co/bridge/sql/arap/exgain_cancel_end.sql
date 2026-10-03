-- U8 取消汇兑损益（9M）第二段：桥调过 UpdateBillForAR / UpdateBillForAP 之后，同一连接同一事务里执行（不分 GO）。
-- 收付款单全部恢复未核销时清核销人（cCancelMan / bPrepay，同 CancelHdsy），再删本次的 9M 行。最后是 k、n 计数。
SET NOCOUNT ON;

DECLARE @flag nvarchar(4), @rows int;
SELECT @flag = flag FROM #exg_args;
IF ISNULL(@flag, N'') NOT IN (N'AR', N'AP') THROW 50001, N'9M flag must be AR or AP', 1;
IF OBJECT_ID(N'tempdb..#CancelHdsy') IS NULL THROW 50029, N'9M working table missing', 1;

UPDATE Ap_CloseBill SET cCancelMan = NULL, bPrepay = 0 WHERE iID IN (
  SELECT a.iID FROM Ap_CloseBill a
  INNER JOIN (SELECT DISTINCT cCoVouchType, cCoVouchID, cFlag FROM #CancelHdsy WHERE cCoVouchType LIKE N'4%') t
    ON a.cVouchType = t.cCoVouchType AND a.cVouchID = t.cCoVouchID AND a.cFlag = t.cFlag
  INNER JOIN Ap_CloseBills b ON a.iID = b.iID
  GROUP BY a.iID
  HAVING SUM(b.iRAmt_f - b.iAmt_f) = 0);

IF @flag = N'AR'
BEGIN
  DELETE d FROM Ar_Detail d
  INNER JOIN (SELECT DISTINCT cCancelNo FROM #CancelHdsy) t ON d.cCancelNo = t.cCancelNo
  WHERE d.cProcStyle = N'9M' AND (d.cFlag = N'AR' OR d.cBusType = N'代理进口');
  SET @rows = @@ROWCOUNT;
END
ELSE
BEGIN
  DELETE d FROM Ap_Detail d
  INNER JOIN (SELECT DISTINCT cCancelNo FROM #CancelHdsy) t ON d.cCancelNo = t.cCancelNo
  WHERE d.cProcStyle = N'9M' AND (d.cFlag = N'AP' OR d.cBusType = N'代理进口');
  SET @rows = @@ROWCOUNT;
END

SELECT k, n FROM (VALUES
  (N'deleted', CONVERT(nvarchar(20), @rows)),
  (N'rows', CONVERT(nvarchar(20), (SELECT COUNT(*) FROM #CancelHdsy)))) v (k, n);

DROP TABLE #CancelHdsy;
IF OBJECT_ID(N'tempdb..#exg_odd') IS NOT NULL DROP TABLE #exg_odd;
