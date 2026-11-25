-- 空账套的第一条存货。存货新增必须带模板（已有存货），所以第一条只能用 SQL 插入，之后的存货都经 archives/create 复制。
-- 双花括号占位符由 co/e2e/seed/steps_inventory.py 换成 data.py 里的值。列按现场账套调整：
-- 目标库上只有 Inventory.cInvCode 和 Inventory_Sub.cInvSubCode 非空且没有缺省值，其余列都可以不写。
-- 标志：销售、采购、自制、生产耗用、在制、BOM 母件、BOM 子件都打开；质检、批次关闭（变体复制后再按用途改）。
SET XACT_ABORT ON;
BEGIN TRANSACTION;
IF NOT EXISTS (SELECT 1 FROM Inventory WHERE cInvCode = N'{{code}}')
    INSERT INTO Inventory (
        cInvCode, cInvName, cInvCCode, cComUnitCode, cGroupCode, iGroupType, iTaxRate,
        bSale, bPurchase, bSelf, bComsume, bProducing, bBomMain, bBomSub, bPropertyCheck, bInvBatch)
    VALUES (
        N'{{code}}', N'{{name}}', N'{{class}}', N'{{unit}}', N'{{group}}', 0, {{tax}},
        1, 1, 1, 1, 1, 1, 1, 0, 0);
IF NOT EXISTS (SELECT 1 FROM Inventory_Sub WHERE cInvSubCode = N'{{code}}')
    INSERT INTO Inventory_Sub (cInvSubCode, dInvCreateDatetime) VALUES (N'{{code}}', GETDATE());
COMMIT TRANSACTION;
