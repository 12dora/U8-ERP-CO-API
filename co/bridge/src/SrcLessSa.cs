using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 无来源发货单（VT 9 卡片 01）、无来源退货单（VT 10 卡片 03，breturnflag=1，请求的正数量写进 DOM 取负）、
    // 先开票销售发票（26：VT 0 卡片 07；27：VT 2 卡片 13）。
    // 同销售订单新增：GetDefaultVoucherDom 模板 → 调用方字段 → 单行 BodyCheck 算价税 → getDefaltVTID + 制单人 →
    // GetVoucherNO + Save(…, 0) 在 CoTrans 里（SoSave.SaveFreeDispatch / SaveInvoice），退货单提交前核对保存结果（ReturnCheck）。
    // 表头不写 isosid / idlsid。
    // 发票表头 idisp=0（先开票）：实测 U8 把 iDisp=0 当先开票，并按发票生成发货单（DispatchList.SBVID 指回发票）。
    // 未覆盖：发货单在发票保存时还是复核时生成；保存后读不到就不返回 dispatch_id，不报错。
    internal static class SrcLessSa
    {
        const string DlByCodeSql = "select convert(varchar(20), DLID) from DispatchList where cDLCode=?";
        const string InvByCodeSql = "select convert(varchar(20), SBVID) from SaleBillVouch where cSBVCode=?";
        const string MadeDlSql = "select top 2 convert(varchar(20), DLID) as DLID from DispatchList where SBVID=?";

        public static ApiResult Create(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            bool invoice = kind.Name == "sale_invoice";
            bool red = SaleReturn.Is(kind);
            string type = invoice ? SrcLessReq.InvoiceType(head) : "05";
            string card;
            int vt = VtOf(kind, type, out card);
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            string code = "";
            int id;
            Dictionary<string, string> fixedHead = FixedHead(invoice, type, red);
            string[] exch = SrcLessReq.Exch(head, ctx.HomeCurrency);
            fixedHead["cexch_name"] = exch[0];
            fixedHead["iexchrate"] = exch[1];
            fixedHead["cdepcode"] = Dept(ctx.Conn, head, kind);
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, vt, out sys, out co);
                SoDom.Templates(co, ctx.Conn, card, ctx.Item, doms);
                SoDom.ApplyFree(ctx.Conn, doms, kind, head, lines, fixedHead);
                if (red)
                {
                    Negate(doms[1]);
                }
                SaleTaxFlag.FreshAll(ctx, doms[1]);
                SaleCalc.ForCreate(co, doms[0], doms[1], lines);
                SoSave.Stamp(ctx, sys, doms[0], card);
                if (invoice)
                {
                    SoDom.SetAttr(doms[0], "csource", fixedHead["csource"]);
                }
                id = invoice ? SoSave.SaveInvoice(ctx.Conn, co, doms, ctx.Item, out code)
                    : SoSave.SaveFreeDispatch(ctx, co, doms, kind.Name, red ? ReturnCheck(ctx.Item, lines) : null, out code);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
            return Saved(ctx, kind, id, code);
        }

        // 发货单、退货单按类型的 VT 与卡片；发票按 cVouchType：26 专票 VT 0 卡片 07，27 普票 VT 2 卡片 13。
        static int VtOf(VoucherKind kind, string type, out string card)
        {
            if (kind.Name != "sale_invoice")
            {
                card = kind.SaCard;
                return kind.SaVt;
            }
            card = type == "27" ? "13" : "07";
            return type == "27" ? 2 : 0;
        }

        // 退货单：请求的数量、件数是正数，写进 DOM 时取负，BodyCheck 按负数量算出负金额。
        static void Negate(object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                NegateCell(body, rows[i], "iquantity");
                NegateCell(body, rows[i], "inum");
            }
        }

        // 退货单提交前核对 U8 保存的结果（SaleGen.RequireNewLines）：红字、类型 05、行数与请求一致、每行负数量、
        // 退货合计等于请求数量之和、不挂发货行和订单行。不符 409 回滚。
        static FreshCheck ReturnCheck(WorkItem item, object[] lines)
        {
            decimal sum = 0m;
            for (int i = 0; i < lines.Length; i++)
            {
                sum = sum + SrcLessReq.Qty(lines[i] as Dictionary<string, object>);
            }
            decimal[] want = new decimal[] { lines.Length, sum };
            return delegate(object conn, string code) { SaleGen.RequireNewLines(conn, item, code, want, true); };
        }

        static void NegateCell(object body, object row, string col)
        {
            decimal value;
            if (StockUnits.Dec(DomRows.Get(row, col).Trim(), out value) && value > 0m)
            {
                DomRows.Set(body, row, col, (-value).ToString(CultureInfo.InvariantCulture));
            }
        }

        // 部门：请求的 cDepCode，否则客户档案的分管部门 cCusDepart；都没有时登录前 400（实测 U8 报「部门不能为空」）。
        static string Dept(object conn, Dictionary<string, object> head, VoucherKind kind)
        {
            string dept = MfgReq.Text(head, "cdepcode");
            if (dept.Length == 0)
            {
                string cus = MfgReq.Text(head, "ccuscode");
                dept = cus.Length == 0 ? "" : (Rows.Scalar(conn, CusDeptSql, new object[] { cus }) ?? "").Trim();
            }
            if (dept.Length == 0)
            {
                throw BridgeException.BadField("head.cdepcode", "无来源" + SrcLess.Plain(kind) + "需要部门 cDepCode");
            }
            return dept;
        }

        const string CusDeptSql = "select cCusDepart from Customer where cCusCode=?";

        // 业务类型只收普通销售（选项 bMustSO_ptxs 管的就是它）；发货单、发票蓝字，退货单 breturnflag=1；
        // 发票 idisp=0 表示先开票，sbvid 由 SaveInvoice 写空串。
        // 发票 csource 必须是「销售」：模板不带、列无缺省，留空时 U8 读单（USSAServer clsvouchload 加 cSource=N'销售'）、
        // 复核取数视图 SaleBillVouchZT 都过滤掉它，报「单据不存在或者已经被删除或者没有权限！」。Create 在 Stamp 后再写一次。
        // 币种、汇率由 Create 按 SrcLessReq.Exch 另加（实测不写汇率时 U8 报「汇率不可以小于等于0」）。
        internal static Dictionary<string, string> FixedHead(bool invoice, string type)
        {
            return FixedHead(invoice, type, false);
        }

        internal static Dictionary<string, string> FixedHead(bool invoice, string type, bool red)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            map["cbustype"] = "普通销售";
            map["cvouchtype"] = type;
            map["breturnflag"] = red ? "1" : "0";
            if (invoice)
            {
                map["idisp"] = "0";
                map["cdlcode"] = "";
                map["csource"] = "销售";
            }
            else
            {
                map["bfirst"] = "0";
            }
            return map;
        }

        // 已提交：先认 Save 的新主键，读不到按单号在新连接上找；都失败或回读出错是 504。
        static ApiResult Saved(WorkContext ctx, VoucherKind kind, int id, string code)
        {
            try
            {
                if (id <= 0)
                {
                    id = FreshInt(ctx, kind.Name == "sale_invoice" ? InvByCodeSql : DlByCodeSql, code);
                }
                if (id > 0)
                {
                    ApiResult result = EditMsg.Saved(ctx, kind, id, null, 0);
                    if (kind.Name == "sale_invoice" && result != null && result.Body != null)
                    {
                        AddDispatch(ctx, result.Body, id);
                    }
                    return result;
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "SrcLessSa " + ex.Message);
            }
            string text = "已保存但未能确定单据标识，单号 " + (code ?? "");
            if (id > 0)
            {
                text = text + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            throw new BridgeException(504, "outcome_unknown", text);
        }

        // 先开票：U8 生成的发货单（SBVID 指回本发票）。恰好一张才返回 dispatch_id。
        static void AddDispatch(WorkContext ctx, Dictionary<string, object> body, int invoiceId)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                List<Dictionary<string, object>> rows = Rows.Query(conn, MadeDlSql, new object[] { invoiceId }, 2);
                int count = rows == null ? 0 : rows.Count;
                CoRows.Note(ctx.Item, "先开票发货单 " + count.ToString(CultureInfo.InvariantCulture));
                if (count == 1)
                {
                    body["dispatch_id"] = CoRows.AsId(CoRows.Col(rows[0], "DLID"));
                }
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static int FreshInt(WorkContext ctx, string sql, string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return 0;
            }
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return CoRows.AsId(Rows.Scalar(conn, sql, new object[] { no }));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }
    }
}
