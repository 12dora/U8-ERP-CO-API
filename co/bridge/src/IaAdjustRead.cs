using System.Collections.Generic;

namespace U8Co
{
    // 出入库调整单（存货核算，JustInVouch / JustInVouchs）：只读（vouchers/load、list、search），纯 SQL，读线程上跑。
    // 一张表放入库调整（cVouchType 20，卡片 0401）、出库调整（21，卡片 0402）和发出商品等调整类型（如 66），不按类型拆分，
    // 类型在 vouch_type。出库调整多是存货核算期末处理自动生成的（cAuto=TRUE，ia/period_end 生成、取消时删除）。
    // 表头主键是自增列 id，单号 cJVCode；表体按 cJVCode 挂（不是 id），行主键 AutoID。表体没有 rowversion，只按表头 ufts 增量。
    // 调整单不改现存量和货位，只记存货核算明细账（IA_Subsidiary 的 JustID / ID 指向表体 AutoID）。没有审核，
    // 状态看记账：表头记账人 cAccounter 常为空，表体每行的 cbAccounter 才可靠，
    // state.posted（即 verified）按表体每行都有记账人判断。写入一律不开放：记账、期末处理走 ia/post、ia/period_end。
    internal static class IaAdjustRead
    {
        internal const string KindName = "ia_adjust";
        const int LineCap = 500;

        const string HeadSql = "select h.*, w.cWhName as wh_name, dep.cDepName as dep_name, rd.cRdName as rd_name"
            + " from JustInVouch h left join Warehouse w on w.cWhCode=h.cWhCode"
            + " left join Department dep on dep.cDepCode=h.cDepCode left join Rd_Style rd on rd.cRdCode=h.cRdCode"
            + " where h.id=?";

        const string LinesSql = "select top (?) d.*, i.cInvName as inv_name, i.cInvStd as inv_std from JustInVouchs d"
            + " left join Inventory i on i.cInvCode=d.cInvCode where d.cJVCode=? order by d.AutoID";

        // 种类登记（Kinds.StKinds 调用）。读取、列表、查找之外的路由一律 400。登录子系统用 SA（同生产订单、质量单据的读取），
        // IA 不在许可采样的登录模块里。
        internal static VoucherKind Kind()
        {
            VoucherKind kind = new VoucherKind();
            kind.Name = KindName;
            kind.Title = "出入库调整单";
            kind.Family = "ia";
            kind.SubId = "SA";
            kind.HeadTable = "JustInVouch";
            kind.IdColumn = "id";
            kind.CodeColumn = "cJVCode";
            kind.BodyTable = "JustInVouchs";
            kind.BodyFk = "cJVCode";
            kind.LineIdColumn = "AutoID";
            kind.VerifierColumn = "cAccounter";
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
            string code = CoRows.Col(head, "cJVCode");
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, LinesSql, new object[] { LineCap + 1, code },
                LineCap + 1);
            if (lines == null)
            {
                lines = new List<Dictionary<string, object>>();
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = code;
            body["head"] = head;
            body["state"] = State(head, lines);
            // 超过 500 行时由 VoucherRead.Cap 截断并标 lines_truncated。
            body["lines"] = lines;
            return ApiResult.Ok(body);
        }

        // posted / verified：表体至少一行且每行都有记账人；verifier 取表头记账人，表头为空时取第一个有记账人的行。
        internal static Dictionary<string, object> State(Dictionary<string, object> head, List<Dictionary<string, object>> lines)
        {
            int posted = 0;
            string first = "";
            for (int i = 0; i < lines.Count; i++)
            {
                string who = CoRows.Col(lines[i], "cbAccounter").Trim();
                if (who.Length > 0)
                {
                    posted++;
                    first = first.Length == 0 ? who : first;
                }
            }
            string accounter = CoRows.Col(head, "cAccounter").Trim();
            bool all = lines.Count > 0 && posted == lines.Count;
            string type = CoRows.Col(head, "cVouchType").Trim();
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = all;
            state["verifier"] = accounter.Length > 0 ? accounter : first;
            state["verified_at"] = "";
            state["posted"] = all;
            state["line_count"] = lines.Count;
            state["posted_lines"] = posted;
            state["vouch_type"] = type;
            state["vouch_name"] = TypeName(type);
            state["auto"] = CoRows.Col(head, "cAuto").Trim().ToUpperInvariant() == "TRUE";
            return state;
        }

        // 只给两种常用类型的名称，其余（发出商品、项目成本等调整）原样给空串，以 vouch_type 为准。
        internal static string TypeName(string type)
        {
            if (type == "20")
            {
                return "入库调整单";
            }
            return type == "21" ? "出库调整单" : "";
        }
    }
}
