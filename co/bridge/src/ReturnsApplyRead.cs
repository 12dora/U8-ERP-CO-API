using System.Collections.Generic;

namespace U8Co
{
    // 退货申请单（销售管理，卡片 SA31，SA_ReturnsApplyMain / SA_ReturnsApplyDetail）：只读（vouchers/load、list、search），
    // 纯 SQL，读线程上跑。表头主键 ID、单号 cCode、日期 dDate；表体外键 ID、行主键 AutoID，数量为负数。
    // 表体 iDLsID 指原发货单行（DispatchLists.iDLsID）；下游退货单行的 irtnappid / crtnappcode 指申请单行和单号（已在测试账套核对）。
    // K12 写入：销售 CO（VoucherCO_Sa，VoucherTypeSA 34 = ReturnsApplyVouch，卡片 SA31）新增（参照蓝字发货单行）、修改、删除、
    // 审核、弃审，见 ReturnsApply*.cs、SaleGenApply.cs；退货单可参照已审核的退货申请单生成（SaleGenApplyRet.cs）。不开关闭。
    internal static class ReturnsApplyRead
    {
        internal const string KindName = "sale_return_apply";
        // ClsVoucherCO_SA.Init(34, …) 后 GetVoucherData 能读出退货申请单。
        internal const int SaVt = 34;
        internal const string Card = "SA31";

        internal static bool Is(VoucherKind kind)
        {
            return kind != null && kind.Name == KindName;
        }
        const int LineCap = 500;

        const string HeadSql = "select h.*, cus.cCusName as cus_name, dep.cDepName as dep_name, per.cPersonName as person_name"
            + " from SA_ReturnsApplyMain h left join Customer cus on cus.cCusCode=h.cCusCode"
            + " left join Department dep on dep.cDepCode=h.cDepCode left join Person per on per.cPersonCode=h.cPersonCode"
            + " where h.ID=?";

        const string LinesSql = "select top (?) d.*, i.cInvName as inv_name, i.cInvStd as inv_std, w.cWhName as wh_name"
            + " from SA_ReturnsApplyDetail d left join Inventory i on i.cInvCode=d.cInvCode"
            + " left join Warehouse w on w.cWhCode=d.cWhCode where d.ID=? order by d.AutoID";

        // 种类登记（Kinds.SaKinds 调用）。关闭、生单（以它为目标）400。
        internal static VoucherKind Kind()
        {
            VoucherKind kind = new VoucherKind();
            kind.Name = KindName;
            kind.Title = "退货申请单";
            kind.Family = "sa";
            kind.SubId = "SA";
            kind.HeadTable = "SA_ReturnsApplyMain";
            kind.IdColumn = "ID";
            kind.CodeColumn = "cCode";
            kind.BodyTable = "SA_ReturnsApplyDetail";
            kind.BodyFk = "ID";
            kind.LineIdColumn = "AutoID";
            kind.VerifierColumn = "cVerifier";
            kind.VerifyDateColumn = "dverifydate";
            kind.StType = "";
            kind.BizObjectId = "";
            kind.SaVt = SaVt;
            kind.SaCard = Card;
            kind.GenerateFrom = "";
            kind.Sources = new string[0];
            kind.Creatable = true;
            kind.Updatable = true;
            kind.Deletable = true;
            kind.Verifiable = true;
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
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = CoRows.Col(head, "cCode");
            body["head"] = head;
            body["state"] = State(head);
            // 超过 500 行时由 VoucherRead.Cap 截断并标 lines_truncated。
            body["lines"] = lines;
            return ApiResult.Ok(body);
        }

        // 已审核按审核人非空，已关闭按关闭人非空（与列表的 verified、closed 同一口径）。
        static Dictionary<string, object> State(Dictionary<string, object> head)
        {
            string verifier = CoRows.Col(head, "cVerifier").Trim();
            string closer = CoRows.Col(head, "cCloser").Trim();
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = verifier.Length > 0;
            state["verifier"] = verifier;
            state["verified_at"] = verifier.Length > 0 ? CoRows.Col(head, "dverifydate") : "";
            state["closed"] = closer.Length > 0;
            state["closer"] = closer;
            return state;
        }
    }
}
