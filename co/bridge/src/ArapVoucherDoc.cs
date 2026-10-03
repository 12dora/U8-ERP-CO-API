using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 制单前的单据读取和闸门（在事务里，带锁）。拒绝一律 409 state_mismatch，文案尽量用 U8 的。
    // 表头：发票、应收应付单沿用核销的 WriteoffKind（id、code、vtype、vdate、cur、rate、auditor、cDwCode、cDeptCode、cPerson），
    // 收付款单沿用 WriteoffSql.Receipt。原始往来明细是审核时登记的 cProcStyle = cVouchType 行。
    internal static class ArapVoucherDoc
    {
        const string RowSql = "select convert(varchar(20), d.Auto_ID) as aid, convert(varchar(10), isnull(d.iFlag,0)) as iflag, "
            + "d.cCode, {dm} as dm, {cm} as cm, d.cexch_name as cur, {rate} as rate, convert(varchar(10), isnull(d.iPeriod,0)) as per, "
            + "convert(varchar(4), year(d.dRegDate)) as ry, isnull(d.cPZid,N'') as pz, d.cDwCode, d.cDeptCode, d.cPerson, "
            + "d.cItem_Class, d.cItemCode, d.cSSCode, d.cDigest, convert(varchar(20), isnull(d.iBVid,0)) as bvid, d.cInvCode "
            + "from {detail} d with (UPDLOCK, HOLDLOCK) where d.cFlag=? and d.cVouchType=? and d.cVouchID=? "
            + "and d.cProcStyle=d.cVouchType order by d.Auto_ID";

        // id：要制单的单据主键（单张时就是 ask.Id，合并制单时 ask.Ids 逐个）。
        public static VoucherDoc Load(object conn, VoucherAsk ask, int id)
        {
            VoucherDoc doc = new VoucherDoc();
            doc.Ask = ask;
            doc.Flag = ask.Flag;
            doc.Detail = WriteoffSql.Detail(ask.Flag);
            doc.Id = id;
            doc.Head = Head(conn, ask.Kind, id);
            doc.Code = CoRows.Col(doc.Head, "code");
            doc.VType = CoRows.Col(doc.Head, "vtype");
            doc.VDate = CoRows.Col(doc.Head, "vdate");
            CheckHead(doc);
            if (IsReceipt(ask.Kind))
            {
                doc.NoteNo = Rows.Scalar(conn, "select isnull(cNoteNo,N'') from Ap_CloseBill where iID=?", new object[] { id }) ?? "";
            }
            string sql = RowSql.Replace("{dm}", WriteoffSql.Dec("d.iDAmount", 2)).Replace("{cm}", WriteoffSql.Dec("d.iCAmount", 2))
                .Replace("{rate}", WriteoffSql.Dec("d.iExchRate", 10)).Replace("{detail}", doc.Detail);
            doc.Rows = Rows.Query(conn, sql, new object[] { doc.Flag, doc.VType, doc.Code }, 1001);
            CheckRows(conn, doc);
            NotCashSettled(conn, doc);
            NotBadRecovered(conn, doc);
            return doc;
        }

        // 现结 / 现付发票（往来明细有 cProcStyle='XJ' 行）：U8 的现结制单是单独一类，分录还要带现结的结算科目，桥不拼，409。
        static void NotCashSettled(object conn, VoucherDoc doc)
        {
            if (doc.Ask.Kind != "sale_invoice" && doc.Ask.Kind != "purchase_invoice")
            {
                return;
            }
            string sql = "select top 1 convert(varchar(20), Auto_ID) from " + doc.Detail
                + " where cFlag=? and cVouchType=? and cVouchID=? and cProcStyle=N'XJ'";
            if (Rows.Scalar(conn, sql, new object[] { doc.Flag, doc.VType, doc.Code }) != null)
            {
                throw Refuse("现结发票请在 U8 客户端制单");
            }
        }

        // 坏账收回用过的收款单（往来明细有 cVouchType 48、cVouchID 为该单号的 9H 处理行）：钱由坏账收回的处理制单
        // （arap/process/voucher，借银行、贷坏账准备）入账，不能再按收款单单独制单，409。
        internal const string BadRecoveredSql = "select top 1 isnull(cCancelNo,N'') from Ar_Detail "
            + "where cFlag=N'AR' and cVouchType=N'48' and cVouchID=? and cProcStyle=N'9H'";

        static void NotBadRecovered(object conn, VoucherDoc doc)
        {
            if (doc.Ask.Kind != "ar_receipt")
            {
                return;
            }
            string no = Rows.Scalar(conn, BadRecoveredSql, new object[] { doc.Code });
            if (no != null)
            {
                throw Refuse(BadRecoveredText(doc.Code, no));
            }
        }

        internal static string BadRecoveredText(string code, string no)
        {
            return "收款单 " + code + " 已用于坏账收回" + (no.Length > 0 ? "（处理号 " + no + "）" : "") + "，请对坏账收回制单";
        }

        // 防重复制单：总账里已有本单（coutbillsign = 单据类型、coutid = 单号，制单系统本系统或 GL，不论是否记账）的凭证，
        // 其外部业务号却没有本单的任何往来明细引用——上次制单 504（凭证已导入、单据没回写）留下的。拒绝并点名该凭证。
        // 存货核算（IA）结转成本的凭证也带发票的 coutbillsign / coutid，制单系统不同，不算。
        public static void NoOrphan(object conn, VoucherDoc doc)
        {
            const string Sql = "select top 1 isnull(g.coutno_id,N'') as pz, convert(varchar(6), g.iyear) as y, convert(varchar(4), g.iperiod) as p, "
                + "g.csign as s, convert(varchar(12), g.ino_id) as n from GL_accvouch g where (g.coutsysname=? or g.coutsysname=N'GL') "
                + "and g.coutbillsign=? and g.coutid=? "
                + "and not exists (select 1 from Ar_Detail d where d.cPZid=g.coutno_id and d.cVouchType=g.coutbillsign and d.cVouchID=g.coutid) "
                + "and not exists (select 1 from Ap_Detail d where d.cPZid=g.coutno_id and d.cVouchType=g.coutbillsign and d.cVouchID=g.coutid)";
            Dictionary<string, object> row = Rows.One(conn, Sql, new object[] { doc.Flag, doc.VType, doc.Code });
            if (row == null)
            {
                return;
            }
            string at = CoRows.Col(row, "y") + "年" + CoRows.Col(row, "p") + "期 " + CoRows.Col(row, "s") + "-" + CoRows.Col(row, "n");
            string pz = CoRows.Col(row, "pz");
            throw Refuse("单据已有一张没有回写的凭证 " + at + (pz.Length > 0 ? "（外部业务号 " + pz + "）" : "")
                + "，可能是上次制单结果不明时留下的；请先核对，确认后用 arap/voucher/delete 或在 U8 客户端删除该凭证再制单");
        }

        // 收付款单（Ap_CloseBill）：收款单、付款单，含退款单（供应商退款 AP48、客户退款 AR49），读取、结算行、合并选项相同。
        public static bool IsReceipt(string kind)
        {
            return kind == "ar_receipt" || kind == "ap_payment" || ArapVoucherRule.IsRefund(kind);
        }

        static Dictionary<string, object> Head(object conn, string kind, int id)
        {
            Dictionary<string, object> head = IsReceipt(kind)
                ? WriteoffSql.Receipt(conn, id)
                : WriteoffSql.Head(conn, WriteoffKind.Of(kind), id);
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return head;
        }

        static string[] Types(string kind)
        {
            if (kind == "ar_receipt" || kind == "ap_refund")
            {
                return new string[] { "48" };
            }
            return kind == "ap_payment" || kind == "ar_refund" ? new string[] { "49" } : WriteoffKind.Of(kind).Types;
        }

        static void CheckHead(VoucherDoc doc)
        {
            string kind = doc.Ask.Kind;
            if (Array.IndexOf(Types(kind), doc.VType) < 0)
            {
                throw new BridgeException(400, "bad_request", "只支持销售发票 26 / 27、采购发票 01 / 02、收款单 48、付款单 49、应收单 R0、应付单 P0、"
                    + "供应商退款 48、客户退款 49");
            }
            bool flagged = IsReceipt(kind) || kind == "ar_bill" || kind == "ap_bill";
            if (flagged && CoRows.Col(doc.Head, "flag") != doc.Flag)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(doc.Head, "auditor").Length == 0)
            {
                string side = doc.Flag == "AP" ? "应付" : "应收";
                throw Refuse(IsReceipt(kind) || flagged ? "单据未审核" : "发票未" + side + "审核");
            }
        }

        // U8 制单列表的条件：审核登记过（iPeriod > 0）、cPZid 为空；桥另外只收本币、蓝字（退款单除外），登记期间未结账。
        static void CheckRows(object conn, VoucherDoc doc)
        {
            if (doc.Rows.Count == 0 || doc.Rows.Count > 1000)
            {
                throw Refuse(doc.Rows.Count == 0 ? "单据没有审核登记的往来明细（未审核）" : "单据往来明细超过 1000 行");
            }
            string local = WriteoffSql.LocalCurrency(conn);
            foreach (Dictionary<string, object> row in doc.Rows)
            {
                CheckRow(conn, doc, row, local);
            }
        }

        static void CheckRow(object conn, VoucherDoc doc, Dictionary<string, object> row, string local)
        {
            if (CoRows.AsId(CoRows.Col(row, "per")) <= 0)
            {
                throw Refuse("单据没有审核登记的往来明细（未审核）");
            }
            if (CoRows.Col(row, "pz").Length > 0)
            {
                throw Refuse("凭证已生成，不能重复制单。");
            }
            if (CoRows.Col(row, "cur") != local || WriteoffSql.Num(CoRows.Col(row, "rate")) != 1m)
            {
                throw Refuse("外币单据制单暂不支持");
            }
            // 退款单的往来明细是 U8 审核时写的负数登记行（u8-notes §5），照常制单。
            bool red = WriteoffSql.Num(CoRows.Col(row, "dm")) < 0 || WriteoffSql.Num(CoRows.Col(row, "cm")) < 0;
            if (red && !ArapVoucherRule.IsRefund(doc.Ask.Kind))
            {
                throw Refuse("红字（负数）单据制单暂不支持");
            }
            int year = CoRows.AsId(CoRows.Col(row, "ry"));
            if (WriteoffSql.Closed(conn, doc.Flag, year, CoRows.AsId(CoRows.Col(row, "per"))))
            {
                throw Refuse((doc.Flag == "AP" ? "应付" : "应收") + "已结账（单据登记期间）");
            }
        }

        // 制单日期：不早于单据日期，在登录年度内，总账该期间未结账，应收（应付）该月未结账。
        public static void CheckDate(object conn, VoucherDoc doc, string date, int loginYear)
        {
            if (string.CompareOrdinal(date, doc.VDate) < 0)
            {
                throw Refuse("制单日期 " + date + " 不能早于单据日期 " + doc.VDate);
            }
            DateTime day = DateTime.ParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (day.Year != loginYear)
            {
                throw Refuse("制单日期必须在登录年度 " + loginYear.ToString(CultureInfo.InvariantCulture)
                    + " 内（请求的 date 是登录日期，制单日期用 voucher_date）");
            }
            GlState.PeriodOpen(conn, day.Year, day.Month);
            if (WriteoffSql.Closed(conn, doc.Flag, day.Year, day.Month))
            {
                throw Refuse((doc.Flag == "AP" ? "应付" : "应收") + "已结账（制单日期所在月份）");
            }
        }

        // 没给 voucher_date 时的制单日期：单张是单据日期，合并制单取各单据日期中最晚的（不早于任何一张）。
        public static string LastDate(List<VoucherDoc> docs)
        {
            string last = docs[0].VDate;
            foreach (VoucherDoc doc in docs)
            {
                if (string.CompareOrdinal(doc.VDate, last) > 0)
                {
                    last = doc.VDate;
                }
            }
            return last;
        }

        public static BridgeException Refuse(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
