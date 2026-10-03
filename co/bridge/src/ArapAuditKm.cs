using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 审核后补往来科目：Sign_SaleBill / Sign_PurBill 直接调用时写出的往来明细（原始行）cCode 为空，
    // U8 客户端审核的发票在同一列上都有往来控制科目（如应收 1122 下的明细科目）。桥在同一事务里用 U8 自己的
    // clsPub_AP.CusVenToCtrlKMForSAPU(往来单位, 币种, 销售/采购类型, 存货, "AR"|"AP")（测试账套实测返回应收控制科目）
    // 逐行取科目补上；取不到就整笔回滚、409，不猜科目。制单（ArapVoucher）按这一列取往来科目。
    internal static class ArapAuditKm
    {
        // 按单据类型加单号取销售 / 采购类型（同一单号可能在专用、普通两种发票上各有一张）。
        const string SaleTypeSql = "select top 1 isnull(cSTCode, N'') as t from SaleBillVouch where cVouchType=? and cSBVCode=?";
        const string PurTypeSql = "select top 1 isnull(cPTCode, N'') as t from PurBillVouch where cPBVBillType=? and cPBVCode=?";

        public static void Fill(WorkContext ctx, ArapAuditSpec spec, object pub, string type, string code)
        {
            object conn = ctx.Conn;
            string sql = "select convert(varchar(20), a.Auto_ID) as id, isnull(a.cDwCode, N'') as dw,"
                + " isnull(a.cexch_name, N'') as exch, isnull(a.cInvCode, N'') as inv from " + spec.Detail
                + " a where a.cVouchType=? and a.cVouchID=? and a.cProcStyle=a.cVouchType and isnull(a.cCode, N'')=N''";
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { type, code }, 1000);
            if (rows.Count == 0)
            {
                return;
            }
            string bizType = Rows.Scalar(conn, spec.Sub == "AR" ? SaleTypeSql : PurTypeSql, new object[] { type, code }) ?? "";
            int filled = 0;
            foreach (Dictionary<string, object> row in rows)
            {
                string km = ControlAccount(pub, row, bizType.Trim(), spec.Sub);
                GlSql.Exec(conn, "update " + spec.Detail + " set cCode=? where Auto_ID=?",
                    new object[] { km, CoRows.Col(row, "id") });
                filled++;
            }
            CoRows.Note(ctx.Item, "补往来科目 " + filled.ToString(CultureInfo.InvariantCulture) + " 行");
        }

        static string ControlAccount(object pub, Dictionary<string, object> row, string bizType, string flag)
        {
            object ret = ComUtil.Call(pub, "CusVenToCtrlKMForSAPU", new object[]
            {
                CoRows.Col(row, "dw"), CoRows.Col(row, "exch"), bizType, CoRows.Col(row, "inv"), flag
            });
            string km = ret == null ? "" : Convert.ToString(ret, CultureInfo.InvariantCulture).Trim();
            if (km.Length == 0)
            {
                throw new BridgeException(409, "state_mismatch",
                    "取不到往来单位 " + CoRows.Col(row, "dw") + " 的往来控制科目，请先在 U8 应收应付的科目设置里设好");
            }
            return km;
        }
    }
}
