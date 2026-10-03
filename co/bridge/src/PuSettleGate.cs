using System;
using System.Collections.Generic;

namespace U8Co
{
    // 采购结算单写入的登录后闸门（全是参数化 SQL，在任何 COM 调用之前）与数据权限的行。
    // 生单：发票存在、普通采购、非期初、蓝字、非现付、已采购复核、未结算（表头、表体 dSDate 和 PurSettleVouchs 都没有），
    // 每行参照已审核的采购入库行（UpSoType 为 rd、RdsId 指向存在的 rdrecords01 行），1 到 400 行，
    // 结算日期（U8 登录日期）不早于发票日期和入库日期、所在期间采购未结账。
    // 删除：结算单存在、普通采购、每行未经存货核算处理结算成本（bAccount）、关联发票未应付审核、结算日期所在期间采购未结账。
    // 「结算日期所在月以前的月份未结账」由 U8 自己判断（CheckSettle / bRdBVAutoSettle 的原文 409）。
    internal static class PuSettleGate
    {
        const string InvoiceSql = "select h.cPBVCode, h.cPBVBillType, h.cBusType, h.cVerifier, h.cPBVVerifier,"
            + " h.cVenCode, h.cDepCode, h.cPersonCode, h.cPTCode, convert(varchar(10), h.dSDate, 23) as SDate,"
            + " convert(varchar(10), h.dPBVDate, 23) as BillDate, convert(varchar(5), isnull(h.bPayment,0)) as Paid,"
            + " convert(varchar(5), isnull(h.bFirst,0)) as First, convert(varchar(5), isnull(h.bOriginal,0)) as Orig,"
            + " convert(varchar(5), isnull(h.bNegative,0)) as Red"
            + " from PurBillVouch h where h.PBVID=?";
        const string InvLinesSql = "select convert(varchar(20), b.ID) as ID, convert(varchar(20), b.RdsId) as RdsId,"
            + " b.cInvCode, convert(varchar(40), b.iPBVQuantity) as Qty, upper(isnull(b.UpSoType, N'rd')) as UpSo,"
            + " convert(varchar(10), b.dSDate, 23) as SDate, convert(varchar(20), r.ID) as RdId, rh.cHandler,"
            + " convert(varchar(10), rh.dDate, 23) as RdDate, rh.cWhCode, rh.cVenCode as RdVen"
            + " from PurBillVouchs b left join rdrecords01 r on r.AutoID=b.RdsId left join RdRecord01 rh on rh.ID=r.ID"
            + " where b.PBVID=? order by b.ID";
        const string SettledSql = "select top 1 convert(varchar(20), s.PSVID) from PurSettleVouchs s"
            + " where s.iBsID in (select b.ID from PurBillVouchs b where b.PBVID=?)";
        const string SettleSql = "select h.cSVCode, h.cBusType, h.cVenCode, h.cDepCode, h.cPersonCode, h.cPTCode,"
            + " convert(varchar(10), h.dSVDate, 23) as SVDate, isnull(h.bMakePz, N'') as MakePz,"
            + " convert(varchar(20), isnull(h.iNetLock, 0)) as NetLock from PurSettleVouch h where h.PSVID=?";
        const string SettleLinesSql = "select convert(varchar(20), d.ID) as ID, convert(varchar(20), d.iBsID) as BsId,"
            + " d.cInvCode, d.cWhCode, convert(varchar(5), isnull(d.bAccount,0)) as Acc,"
            + " convert(varchar(20), b.PBVID) as PBVID, bh.cPBVVerifier"
            + " from PurSettleVouchs d left join PurBillVouchs b on b.ID=d.iBsID and d.iBsID<>0"
            + " left join PurBillVouch bh on bh.PBVID=b.PBVID where d.PSVID=? order by d.ID";
        const string PeriodSql = "select convert(varchar(5), isnull(bflag_PU,0)) from GL_mend where iyear=? and iperiod=?";
        const string Settled = "发票已结算";

        // 先查功能权限（不泄露单据状态），闸门之后再按行查数据权限。
        internal static SettleJob ForGenerate(WorkContext ctx, int pbvid)
        {
            PermContext perm = RequireFunc(ctx, PuSettleReq.GenerateRule);
            string date = (ctx.Item.Date ?? "").Trim();
            if (!PuSettleReq.IsDay(date))
            {
                throw BridgeException.BadField("date", "结算日期必须是 yyyy-MM-dd");
            }
            Dictionary<string, object> head = Rows.One(ctx.Conn, InvoiceSql, new object[] { pbvid });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "发票不存在");
            }
            GateInvoice(head, date);
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, InvLinesSql, new object[] { pbvid },
                PuSettleReq.LinesMax + 1);
            GateCount(lines);
            string ven = CoRows.Col(head, "cVenCode");
            for (int i = 0; i < lines.Count; i++)
            {
                GateLine(lines[i], date, ven);
            }
            if (Rows.Scalar(ctx.Conn, SettledSql, new object[] { pbvid }) != null)
            {
                throw new BridgeException(409, "state_mismatch", Settled);
            }
            RequireOpen(ctx, date);
            MoDelete.CheckRows(perm, PermRegistry.ForKey(PuSettleReq.GenerateRule), PermRows(head, lines));
            SettleJob job = new SettleJob();
            job.Pbvid = pbvid;
            job.Date = date;
            job.Head = head;
            job.Lines = lines;
            return job;
        }

        static void GateInvoice(Dictionary<string, object> head, string date)
        {
            if (CoRows.Col(head, "cBusType") != "普通采购")
            {
                throw State("只支持普通采购的发票结算");
            }
            if (CoRows.Col(head, "First") != "0" || CoRows.Col(head, "Orig") != "0")
            {
                throw State("期初发票不支持结算");
            }
            if (CoRows.Col(head, "Red") != "0")
            {
                throw State("只支持蓝字发票结算");
            }
            if (CoRows.Col(head, "Paid") != "0")
            {
                throw State("现付发票不支持结算");
            }
            if (CoRows.Col(head, "cVerifier").Length == 0)
            {
                throw State("发票未复核");
            }
            if (CoRows.Col(head, "SDate").Length > 0)
            {
                throw State(Settled);
            }
            if (string.CompareOrdinal(date, CoRows.Col(head, "BillDate")) < 0)
            {
                throw State("结算日期早于发票日期");
            }
            PuInv.BillKey(CoRows.Col(head, "cPBVBillType"));
        }

        static void GateCount(List<Dictionary<string, object>> lines)
        {
            if (lines == null || lines.Count == 0)
            {
                throw State("发票没有明细");
            }
            if (lines.Count > PuSettleReq.LinesMax)
            {
                throw State("发票超过 400 行，请在 U8 客户端结算");
            }
        }

        static void GateLine(Dictionary<string, object> line, string date, string ven)
        {
            if (CoRows.Col(line, "UpSo") != "RD")
            {
                throw State("只支持参照采购入库单开具的发票行");
            }
            if (CoRows.AsId(CoRows.Col(line, "RdsId")) <= 0 || CoRows.Col(line, "RdId").Length == 0)
            {
                throw State("发票行没有对应的采购入库单行");
            }
            if (CoRows.Col(line, "cHandler").Length == 0)
            {
                throw State("采购入库单未审核");
            }
            if (!string.Equals(CoRows.Col(line, "RdVen"), ven, StringComparison.OrdinalIgnoreCase))
            {
                throw State("采购入库单的供应商与发票不一致");
            }
            if (PuInv.Num(CoRows.Col(line, "Qty")) <= 0m)
            {
                throw State("只支持蓝字发票结算");
            }
            if (CoRows.Col(line, "SDate").Length > 0)
            {
                throw State(Settled);
            }
            if (string.CompareOrdinal(date, CoRows.Col(line, "RdDate")) < 0)
            {
                throw State("结算日期早于入库日期");
            }
        }

        internal static SettleDoc ForDelete(WorkContext ctx, int psvid)
        {
            PermContext perm = RequireFunc(ctx, PuSettleReq.DeleteRule);
            Dictionary<string, object> head = Rows.One(ctx.Conn, SettleSql, new object[] { psvid });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            GateSettleHead(head);
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, SettleLinesSql, new object[] { psvid },
                PuSettleReq.LinesMax + 1) ?? new List<Dictionary<string, object>>();
            if (lines.Count > PuSettleReq.LinesMax)
            {
                throw State("结算单超过 400 行，请在 U8 客户端删除");
            }
            SettleDoc doc = new SettleDoc();
            doc.Psvid = psvid;
            doc.Head = head;
            doc.Lines = lines;
            for (int i = 0; i < lines.Count; i++)
            {
                GateSettleLine(lines[i], doc.Invoices);
            }
            RequireOpen(ctx, CoRows.Col(head, "SVDate"));
            MoDelete.CheckRows(perm, PermRegistry.ForKey(PuSettleReq.DeleteRule), PermRows(head, lines));
            return doc;
        }

        // 普通采购；已制凭证（bMakePz）、被网络锁定（iNetLock）的不删。
        static void GateSettleHead(Dictionary<string, object> head)
        {
            if (CoRows.Col(head, "cBusType") != "普通采购")
            {
                throw State("只支持删除普通采购的结算单");
            }
            string pz = CoRows.Col(head, "MakePz");
            if (pz.Length > 0 && pz != "0" && !pz.Equals("false", StringComparison.OrdinalIgnoreCase))
            {
                throw State("结算单已制凭证，先删除凭证");
            }
            if (PuInv.Num(CoRows.Col(head, "NetLock")) != 0m)
            {
                throw State("结算单已被锁定");
            }
        }

        static void GateSettleLine(Dictionary<string, object> line, List<int> invoices)
        {
            if (CoRows.Col(line, "Acc") != "0")
            {
                throw State("存货核算已记账，先取消记账");
            }
            if (CoRows.Col(line, "cPBVVerifier").Length > 0)
            {
                throw State("发票已应付审核，请先取消应付审核");
            }
            int pbvid = CoRows.AsId(CoRows.Col(line, "PBVID"));
            if (pbvid > 0 && !invoices.Contains(pbvid))
            {
                invoices.Add(pbvid);
            }
        }

        // 结算日期按 U8 会计期间（UA_Period，WriteoffSql.PeriodOf）定年度、期间，再查 GL_mend 的采购结账标志；
        // 不在任何期间里 409，期间在 GL_mend 没有行不拦，交给 U8。手工结算（PuSettleManGate）同样调用。
        internal static void RequireOpen(WorkContext ctx, string date)
        {
            if (!PuSettleReq.IsDay(date))
            {
                throw State("结算日期无效");
            }
            int[] period = WriteoffSql.PeriodOf(ctx.Conn, ctx.Item.Acc, date);
            if (period == null)
            {
                throw State("结算日期 " + date + " 不在 U8 的会计期间里");
            }
            string flag = Rows.Scalar(ctx.Conn, PeriodSql, new object[] { period[0], period[1] });
            if (flag != null && flag.Trim() == "1")
            {
                throw State("结算日期所在期间采购已结账");
            }
        }

        // 数据权限的行：表头的供应商、部门、业务员、采购类型，加上每行的存货、仓库（PermRegistryPuSettle）。
        internal static List<Dictionary<string, object>> PermRows(Dictionary<string, object> head,
            List<Dictionary<string, object>> lines)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int i = 0; i < lines.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
                row["cVenCode"] = CoRows.Col(head, "cVenCode");
                row["cDepCode"] = CoRows.Col(head, "cDepCode");
                row["cPersonCode"] = CoRows.Col(head, "cPersonCode");
                row["cPTCode"] = CoRows.Col(head, "cPTCode");
                row["cInvCode"] = CoRows.Col(lines[i], "cInvCode");
                row["cWhCode"] = CoRows.Col(lines[i], "cWhCode");
                rows.Add(row);
            }
            return rows;
        }

        // 写规则的功能 id（闸门之前）；每行数据权限在闸门之后由 ForGenerate / ForDelete 查。
        static PermContext RequireFunc(WorkContext ctx, string key)
        {
            PermContext perm = PermCheck.Of(ctx);
            PermCheck.RequireRule(perm, PermRegistry.ForKey(key));
            return perm;
        }

        static BridgeException State(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }

    // 生单的一张发票：闸门读出的表头、表体（PuSettleGate.InvoiceSql / InvLinesSql 的列）。
    internal sealed class SettleJob
    {
        public int Pbvid;
        public string Date;
        public Dictionary<string, object> Head;
        public List<Dictionary<string, object>> Lines;
    }

    // 删除的一张结算单：表头、表体和关联的发票（PBVID，去重）。
    internal sealed class SettleDoc
    {
        public int Psvid;
        public Dictionary<string, object> Head;
        public List<Dictionary<string, object>> Lines;
        public List<int> Invoices = new List<int>();
    }
}
