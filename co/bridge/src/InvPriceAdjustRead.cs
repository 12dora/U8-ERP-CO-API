using System.Collections.Generic;

namespace U8Co
{
    // 存货调价单（销售管理，卡片 SA18，SA_InvPriceJustMain / SA_InvPriceJustDetail）：只读（vouchers/load、list、search），
    // 纯 SQL，读线程上跑。审核后 U8 把新价格写进存货价格表 SA_InvUPrice（reports/price_list kind=inventory 读的就是它）。
    // 该表可能为空；列名按数据字典（主键 id、单号 ccode、审核人 cverifier、表体外键 id、
    // 行主键 autoid），未经实测。写入不开放：U8 的存货调价走销售管理的单据组件，本项目没有接。
    internal static class InvPriceAdjustRead
    {
        internal const string KindName = "inventory_price_adjust";
        const int LineCap = 500;

        const string HeadSql = "select h.*, dep.cDepName as dep_name from SA_InvPriceJustMain h"
            + " left join Department dep on dep.cDepCode=h.cdepcode where h.id=?";

        const string LinesSql = "select top (?) d.*, i.cInvName as inv_name, i.cInvStd as inv_std from SA_InvPriceJustDetail d"
            + " left join Inventory i on i.cInvCode=d.cinvcode where d.id=? order by d.autoid";

        // 种类登记（Kinds.SaKinds 调用）。读取、列表、查找之外的路由一律 400。
        internal static VoucherKind Kind()
        {
            VoucherKind kind = new VoucherKind();
            kind.Name = KindName;
            kind.Title = "存货调价单";
            kind.Family = "sa";
            kind.SubId = "SA";
            kind.HeadTable = "SA_InvPriceJustMain";
            kind.IdColumn = "id";
            kind.CodeColumn = "ccode";
            kind.BodyTable = "SA_InvPriceJustDetail";
            kind.BodyFk = "id";
            kind.LineIdColumn = "autoid";
            kind.VerifierColumn = "cverifier";
            kind.VerifyDateColumn = "";
            kind.StType = "";
            kind.BizObjectId = "";
            kind.SaCard = "";
            kind.GenerateFrom = "";
            kind.Sources = new string[0];
            kind.Verifiable = false;
            kind.VerifySub = "";
            return kind;
        }

        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> head = Rows.One(ctx.Conn, HeadSql, new object[] { id });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, LinesSql, new object[] { LineCap + 1, id },
                LineCap + 1);
            if (lines == null)
            {
                lines = new List<Dictionary<string, object>>();
            }
            string verifier = CoRows.Col(head, "cverifier").Trim();
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = verifier.Length > 0;
            state["verifier"] = verifier;
            state["verified_at"] = verifier.Length > 0 ? CoRows.Col(head, "dverifydate") : "";
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = CoRows.Col(head, "ccode");
            body["head"] = head;
            body["state"] = state;
            // 超过 500 行时由 VoucherRead.Cap 截断并标 lines_truncated。
            body["lines"] = lines;
            return ApiResult.Ok(body);
        }
    }
}
