using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购结算单（卡片 99，PurSettleVouch / PurSettleVouchs）：读取（vouchers/load、list、search）纯 SQL，读线程上跑。
    // 结算单没有审核（只有制单人 cMaker），state.verified 恒为 false；表头、表体的 rowversion 是 psufts / psdufts，不是 ufts。
    // 每行另给入库与发票的对照：iRdsID → rdrecords01.AutoID（入库单 RdRecord01），iBsID → PurBillVouchs.ID（发票 PurBillVouch），
    // iBsID 为 0 的是红蓝入库对冲行，没有发票。cUpSoType 不是 01（入库单）的行不连入库单，入库对照为空。
    // 写入：参照采购发票自动结算（vouchers/generate，PuSettleGen）、删除（PuSettleDel）；手工结算（vouchers/create，PuSettleMan，
    // 只限测试账套）；修改、审核不开放。
    internal static class PuSettleRead
    {
        internal const string KindName = "purchase_settle";
        const int LineCap = 500;

        const string HeadSql = "select h.*, v.cVenName as ven_name, v.cVenAbbName as ven_abbname"
            + " from PurSettleVouch h left join Vendor v on v.cVenCode=h.cVenCode where h.PSVID=?";

        const string LinesSql = "select d.*,"
            + " r.ID as in_id, rh.cCode as in_code, rh.dDate as in_date, r.iQuantity as in_qty, r.iAPrice as in_aprice,"
            + " r.iPrice as in_price, b.PBVID as invoice_id, bh.cPBVCode as invoice_code, bh.dPBVDate as invoice_date,"
            + " b.iPBVQuantity as invoice_qty, b.iMoney as invoice_money"
            + " from PurSettleVouchs d"
            + " left join rdrecords01 r on r.AutoID=d.iRdsID and isnull(d.cUpSoType, N'01')=N'01'"
            + " left join RdRecord01 rh on rh.ID=r.ID"
            + " left join PurBillVouchs b on b.ID=d.iBsID and d.iBsID<>0"
            + " left join PurBillVouch bh on bh.PBVID=b.PBVID"
            + " where d.PSVID=? order by d.ID";

        const string StateSql = "select count(*) as n, sum(case when d.bAccount=1 then 1 else 0 end) as acc,"
            + " count(distinct case when d.iBsID<>0 then b.PBVID end) as invoices,"
            + " count(distinct r.ID) as receipts"
            + " from PurSettleVouchs d"
            + " left join PurBillVouchs b on b.ID=d.iBsID"
            + " left join rdrecords01 r on r.AutoID=d.iRdsID and isnull(d.cUpSoType, N'01')=N'01'"
            + " where d.PSVID=?";

        // 种类登记（Kinds.PuKinds 调用）。可删除、可参照采购发票生单、可新增（手工结算，第一级）；修改、审核、关闭一律 400。
        internal static VoucherKind Kind()
        {
            VoucherKind kind = new VoucherKind();
            kind.Name = KindName;
            kind.Title = "采购结算单";
            kind.Family = "pu";
            kind.SubId = "PU";
            kind.HeadTable = "PurSettleVouch";
            kind.IdColumn = "PSVID";
            kind.CodeColumn = "cSVCode";
            kind.BodyTable = "PurSettleVouchs";
            kind.BodyFk = "PSVID";
            kind.LineIdColumn = "ID";
            kind.VerifierColumn = "";
            kind.VerifyDateColumn = "";
            kind.StType = "";
            kind.BizObjectId = "";
            kind.SaCard = "";
            kind.GenerateFrom = "purchase_invoice";
            kind.Sources = new string[] { "purchase_invoice" };
            kind.Creatable = true;
            kind.Deletable = true;
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
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, LinesSql, new object[] { id }, LineCap + 1);
            if (lines == null)
            {
                lines = new List<Dictionary<string, object>>();
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = CoRows.Col(head, kind.CodeColumn);
            body["head"] = head;
            body["lines"] = lines;
            body["state"] = State(ctx.Conn, id);
            return ApiResult.Ok(body);
        }

        // accounted：每行都已由存货核算处理结算成本（bAccount=1）；有未处理的行时 accounted_lines 小于 line_count。
        static Dictionary<string, object> State(object conn, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, StateSql, new object[] { id });
            int total = Num(row, "n");
            int acc = Num(row, "acc");
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = false;
            state["verifier"] = "";
            state["verified_at"] = "";
            state["line_count"] = total;
            state["accounted_lines"] = acc;
            state["accounted"] = total > 0 && acc == total;
            state["invoice_count"] = Num(row, "invoices");
            state["receipt_count"] = Num(row, "receipts");
            return state;
        }

        static int Num(Dictionary<string, object> row, string name)
        {
            string text = row == null ? "" : CoRows.Col(row, name).Trim();
            int n;
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            return 0;
        }
    }
}
