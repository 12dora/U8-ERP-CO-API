using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 无来源发货单（VT 9 卡片 01）、先开票销售发票（26：VT 0 卡片 07；27：VT 2 卡片 13）。
    // 同销售订单新增：GetDefaultVoucherDom 模板 → 调用方字段 → 单行 BodyCheck 算价税 → getDefaltVTID + 制单人 →
    // GetVoucherNO + Save(…, 0) 在 CoTrans 里（SoSave.SaveDispatch / SaveInvoice）。表头不写 isosid / idlsid。
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
            string type = invoice ? SrcLessReq.InvoiceType(head) : "05";
            int vt = !invoice ? 9 : (type == "27" ? 2 : 0);
            string card = !invoice ? "01" : (type == "27" ? "13" : "07");
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            string code = "";
            int id;
            Dictionary<string, string> fixedHead = FixedHead(invoice, type);
            string[] exch = SrcLessReq.Exch(head, ctx.HomeCurrency);
            fixedHead["cexch_name"] = exch[0];
            fixedHead["iexchrate"] = exch[1];
            fixedHead["cdepcode"] = Dept(ctx.Conn, head, invoice);
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, vt, out sys, out co);
                SoDom.Templates(co, ctx.Conn, card, ctx.Item, doms);
                SoDom.ApplyFree(ctx.Conn, doms, kind, head, lines, fixedHead);
                SaleCalc.ForCreate(co, doms[0], doms[1], lines);
                SoSave.Stamp(ctx, sys, doms[0], card);
                if (invoice)
                {
                    SoDom.SetAttr(doms[0], "csource", fixedHead["csource"]);
                }
                id = invoice ? SoSave.SaveInvoice(ctx.Conn, co, doms, ctx.Item, out code)
                    : SoSave.SaveDispatch(ctx.Conn, co, doms, ctx.Item, out code);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
            return Saved(ctx, kind, id, code);
        }

        // 部门：请求的 cDepCode，否则客户档案的分管部门 cCusDepart；都没有时登录前 400（实测 U8 报「部门不能为空」）。
        static string Dept(object conn, Dictionary<string, object> head, bool invoice)
        {
            string dept = MfgReq.Text(head, "cdepcode");
            if (dept.Length == 0)
            {
                string cus = MfgReq.Text(head, "ccuscode");
                dept = cus.Length == 0 ? "" : (Rows.Scalar(conn, CusDeptSql, new object[] { cus }) ?? "").Trim();
            }
            if (dept.Length == 0)
            {
                throw BridgeException.BadField("head.cdepcode", invoice ? "无来源销售发票需要部门 cDepCode" : "无来源发货单需要部门 cDepCode");
            }
            return dept;
        }

        const string CusDeptSql = "select cCusDepart from Customer where cCusCode=?";

        // 业务类型只收普通销售（选项 bMustSO_ptxs 管的就是它）；蓝字；发票 idisp=0 表示先开票，sbvid 由 SaveInvoice 写空串。
        // 发票 csource 必须是「销售」：模板不带、列无缺省，留空时 U8 读单（USSAServer clsvouchload 加 cSource=N'销售'）、
        // 复核取数视图 SaleBillVouchZT 都过滤掉它，报「单据不存在或者已经被删除或者没有权限！」。Create 在 Stamp 后再写一次。
        // 币种、汇率由 Create 按 SrcLessReq.Exch 另加（实测不写汇率时 U8 报「汇率不可以小于等于0」）。
        internal static Dictionary<string, string> FixedHead(bool invoice, string type)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            map["cbustype"] = "普通销售";
            map["cvouchtype"] = type;
            map["breturnflag"] = "0";
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
