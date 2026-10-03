namespace U8Co
{
    // ListKinds 的物料清单行（只列标准 BOM，BomType=1）。列顺序同 ListKinds.cs 的说明。
    // 表头没有单号：code 是母件存货编码（APPLY 里经 bom_parent → bas_part 取），筛选 code 即按母件编码；
    // 单据日期是版本生效日期；已审核 Status=3，已关闭（停用）Status=4。子件有 rowversion，变更按表头、子件较大的算。
    internal static partial class ListKinds
    {
        const string BomApply = " OUTER APPLY (SELECT (SELECT TOP 1 bp.InvCode FROM bom_parent p JOIN bas_part bp"
            + " ON bp.PartId = p.ParentId WHERE p.BomId = h.BomId) AS inv,"
            + " (SELECT MAX(d.Ufts) FROM bom_opcomponent d WHERE d.BomId = h.BomId) AS bu) b";
        const string BomExtra = "h.Version AS version, h.VersionDesc AS version_desc, h.VersionEndDate AS end_date, "
            + "h.Status AS status, h.IsWFControlled AS wf, h.CreateTime AS created_at, h.ModifyTime AS modified_at";

        static string[][] BomRows()
        {
            return new string[][]
            {
                new string[] { "bom", "bom_bom", "BomId", "b.inv", "h.VersionEffDate",
                    null, null, null, null, null, "h.CreateUser",
                    "h.RelsUser", "h.RelsDate", "h.CloseUser", "CASE WHEN h.Status = 4 THEN 1 ELSE 0 END",
                    "CASE WHEN h.Status = 3 THEN 1 ELSE 0 END", null,
                    "Ufts", "bom_opcomponent", "BomId", "Ufts", "h.BomType = 1", BomExtra, BomApply }
            };
        }
    }
}
