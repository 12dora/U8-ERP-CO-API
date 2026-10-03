using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 坏账发生 / 收回的 SQL：脚本参数表（#bad_args、#bad_pick，坏账收回共用 #bad_args）、单据行余额、审核行、
    // 销售发票累计核销的快照与核对。参数表用不带参数的整段 SQL 建（留在请求连接上，脚本和 U8 的 clsWrite2Bill 都看得见），
    // 之后的写入才带参数；金额以两位小数的文本传入，脚本里再 convert。
    internal static class ArapBadOccurSql
    {
        internal const string Script = "sql/arap/bad_occur.sql";
        const decimal Tolerance = 0.005m;

        internal const string PrepSql = "SET NOCOUNT ON;"
            + " IF OBJECT_ID('tempdb..#bad_args') IS NOT NULL DROP TABLE #bad_args;"
            + " CREATE TABLE #bad_args (y smallint, m tinyint, reg_date nvarchar(10), accounter nvarchar(20), digest nvarchar(255),"
            + " dw nvarchar(40), dept nvarchar(40), person nvarchar(40), para_id int, receipt_id int, receipt nvarchar(60),"
            + " amt_f nvarchar(40), amt_n nvarchar(40));"
            + " IF OBJECT_ID('tempdb..#bad_pick') IS NOT NULL DROP TABLE #bad_pick;"
            + " CREATE TABLE #bad_pick (rid int IDENTITY(1,1), vtype nvarchar(10), vid nvarchar(60), line int, whole int,"
            + " amt_f nvarchar(40), amt_n nvarchar(40));"
            + " IF OBJECT_ID('tempdb..#ap_SaleBillVouchHXdata') IS NOT NULL DROP TABLE #ap_SaleBillVouchHXdata;"
            + " CREATE TABLE #ap_SaleBillVouchHXdata (autoid bigint, iexchsum decimal(29,6), imoneysum decimal(29,6));"
            + " CREATE INDEX idx_HXautoid ON #ap_SaleBillVouchHXdata (autoid);"
            + " SET NOCOUNT OFF;";

        internal const string DropSql = "SET NOCOUNT ON;"
            + " IF OBJECT_ID('tempdb..#bad_args') IS NOT NULL DROP TABLE #bad_args;"
            + " IF OBJECT_ID('tempdb..#bad_pick') IS NOT NULL DROP TABLE #bad_pick;"
            + " IF OBJECT_ID('tempdb..#bad_rem') IS NOT NULL DROP TABLE #bad_rem;"
            + " IF OBJECT_ID('tempdb..#ap_SaleBillVouchHXdata') IS NOT NULL DROP TABLE #ap_SaleBillVouchHXdata;"
            + " SET NOCOUNT OFF;";

        internal const string ArgsFill = "INSERT INTO #bad_args (y, m, reg_date, accounter, digest, dw, dept, person, para_id,"
            + " receipt_id, receipt, amt_f, amt_n) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";
        const string PickFill = "INSERT INTO #bad_pick (vtype, vid, line, whole, amt_f, amt_n) VALUES (?, ?, ?, ?, ?, ?)";

        // 单据各行（iBVid，应收单整单为 0）的余额：原币、本币，借 − 贷，口径同核销（WriteoffSql.Balance）、带锁。
        const string SlotLine = "convert(varchar(20), isnull(d.iBVid,0)) as line";
        const string SlotSql = "select " + SlotLine + ", {f} as f, {n} as n"
            + " from Ar_Detail d with (UPDLOCK, HOLDLOCK) where d.cFlag=N'AR' and d.cCoVouchType=? and d.cCoVouchID=?"
            + " and d.cDwCode=? and d.iFlag<3";
        const string SignSql = "select top 1 convert(varchar(20), s.Auto_ID) as auto, isnull(s.cContractID,N'') as contract"
            + " from Ar_Detail s where s.cProcStyle=s.cVouchType and s.cVouchType=? and s.cVouchID=? and s.cFlag=N'AR'"
            + " and s.cDwCode=? and s.iFlag<3 and isnull(s.iBVid,0)=? order by s.Auto_ID";
        const string BillSql = "select {a} as a, {b} as b from SaleBillVouchs x with (UPDLOCK, HOLDLOCK) where x.AutoID=?";

        // 脚本拒绝编号 → 中文 409（公共编号 50102–50104 在 ArapBad.Refused；50101、50111 是防御性的，按内部错误）。
        internal static readonly Dictionary<int, string> Texts = BuildTexts();

        static Dictionary<int, string> BuildTexts()
        {
            Dictionary<int, string> map = new Dictionary<int, string>();
            map[50112] = "单据的审核记录在处理中被改动或已不存在，已回滚{0}";
            map[50113] = "单据余额在处理中被改动，已回滚，请重试{0}";
            map[50114] = "应收单余额回写不符，已回滚{0}";
            return map;
        }

        // 建参数表、写入参数和各分摊行。
        public static void Prepare(object conn, BadOccurPlan plan, ArapBadAsk ask)
        {
            UnwriteoffSql.Run(conn, PrepSql);
            BadOpen o = plan.Open;
            GlSql.Exec(conn, ArgsFill, new object[]
            {
                o.Year, o.Period, o.Date, o.Accounter, plan.Digest, ask.Customer, ask.Dept, ask.Person, o.ParaId, 0, "", "0", "0"
            });
            foreach (BadPiece p in plan.Pieces)
            {
                GlSql.Exec(conn, PickFill, new object[]
                {
                    p.Doc.Ask.Type, p.Doc.Ask.Id, p.Line, ArapBadRule.WholeDoc(p.Doc.Ask.Type) ? 1 : 0,
                    ArapBadRule.Money(p.F), ArapBadRule.Money(p.N)
                });
            }
        }

        // 失败不影响结果（临时表随连接释放）；只为登录复用时不把临时表留给下一笔请求。
        public static void Drop(object conn)
        {
            try
            {
                UnwriteoffSql.Run(conn, DropSql);
            }
            catch (Exception)
            {
            }
        }

        // 各行余额；给了 line_id 时只有那一行（没有往来明细的行余额为 0）；应收单整单一行（行号 0）。
        public static List<BadSlot> Slots(object conn, BadDoc d, string customer)
        {
            string sql = SlotQuery(ArapBadRule.WholeDoc(d.Ask.Type));
            List<BadSlot> slots = new List<BadSlot>();
            object[] args = new object[] { d.Ask.Type, d.Ask.Id, customer };
            foreach (Dictionary<string, object> row in Rows.Query(conn, sql, args, 1001))
            {
                BadSlot s = new BadSlot();
                s.Line = CoRows.AsId(CoRows.Col(row, "line"));
                s.RemainF = WriteoffSql.Num(CoRows.Col(row, "f"));
                s.RemainN = WriteoffSql.Num(CoRows.Col(row, "n"));
                if (d.Ask.LineId == 0 || s.Line == d.Ask.LineId)
                {
                    slots.Add(s);
                }
            }
            return slots;
        }

        // 余额查询：发票按行分组，应收单整单一行（行号 '0'）。纯函数，--selftest 用。
        internal static string SlotQuery(bool whole)
        {
            string sql = SlotSql.Replace("{f}", WriteoffSql.Dec("sum(isnull(d.iDAmount_f,0)-isnull(d.iCAmount_f,0))", 2))
                .Replace("{n}", WriteoffSql.Dec("sum(isnull(d.iDAmount,0)-isnull(d.iCAmount,0))", 2));
            return whole ? sql.Replace(SlotLine, "'0' as line") : sql + " group by isnull(d.iBVid,0)";
        }

        // 每个分摊行都要有审核行（处理行从它复制），且没有关联合同；记下销售发票行的累计核销，写后核对用。
        public static void Before(object conn, BadDoc d, string customer)
        {
            foreach (BadPiece p in d.Pieces)
            {
                Dictionary<string, object> sign = Rows.One(conn, SignSql, new object[] { d.Ask.Type, d.Ask.Id, customer, p.Line });
                string where = p.Line > 0 ? " 的行 " + p.Line.ToString(CultureInfo.InvariantCulture) : string.Empty;
                if (sign == null)
                {
                    throw ArapBad.State(d.Label + where + " 没有审核记录，不能做坏账发生");
                }
                if (CoRows.Col(sign, "contract").Trim().Length > 0)
                {
                    throw ArapBad.State(d.Label + " 关联了合同，请在 U8 客户端做坏账发生");
                }
                if (!ArapBadRule.WholeDoc(d.Ask.Type))
                {
                    decimal[] acc = Bill(conn, p.Line);
                    p.BillA = acc[0];
                    p.BillB = acc[1];
                }
            }
        }

        static decimal[] Bill(object conn, int line)
        {
            string sql = BillSql.Replace("{a}", WriteoffSql.Dec("x.iExchSum", 2)).Replace("{b}", WriteoffSql.Dec("x.iMoneySum", 2));
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { line });
            return new decimal[] { WriteoffSql.Num(CoRows.Col(row, "a")), WriteoffSql.Num(CoRows.Col(row, "b")) };
        }

        // 脚本已把销售发票行的本次金额（原币、本币，正数）放进 #ap_SaleBillVouchHXdata：交给 U8 的 clsWrite2Bill.UpdateBillForAR
        // 加到累计核销（连带订单、发货单，同核销、转账），再核对 累计 = 写前 + 本次。不符回滚、409。
        public static void SaleBill(WorkContext ctx, BadOccurPlan plan)
        {
            ArapUnwriteoffBill.Write2(ctx, "处理号 " + plan.CancelNo);
            foreach (BadPiece p in plan.Pieces)
            {
                if (ArapBadRule.WholeDoc(p.Doc.Ask.Type))
                {
                    continue;
                }
                decimal[] now = Bill(ctx.Conn, p.Line);
                if (Math.Abs(now[0] - (p.BillA + p.F)) > Tolerance || Math.Abs(now[1] - (p.BillB + p.N)) > Tolerance)
                {
                    throw new BridgeException(409, "u8_rejected", "坏账发生回写后销售发票行 " + p.Line.ToString(CultureInfo.InvariantCulture)
                        + " 的累计核销不符，已回滚");
                }
            }
        }

        // 提交前：处理行数等于分摊行数，每行余额 = 写前 − 本次。不符返回原因，符合返回 null。
        public static string Mismatch(object conn, BadOccurPlan plan, string customer)
        {
            int want = 0;
            foreach (BadDoc d in plan.Docs)
            {
                List<BadSlot> now = Slots(conn, d, customer);
                foreach (BadPiece p in d.Pieces)
                {
                    want++;
                    BadSlot s = now.Find(delegate(BadSlot x) { return x.Line == p.Line; });
                    decimal left = s == null ? 0m : s.RemainF;
                    if (Math.Abs(left - (p.BeforeF - p.F)) > Tolerance)
                    {
                        return d.Label + (p.Line > 0 ? " 的行 " + p.Line.ToString(CultureInfo.InvariantCulture) : string.Empty)
                            + " 的余额不是 " + ArapBadRule.Money(p.BeforeF - p.F);
                    }
                }
            }
            int rows = ArapBad.CountRows(conn, ArapBadRule.OccurStyle, plan.CancelNo);
            return rows == want ? null : "处理行 " + rows.ToString(CultureInfo.InvariantCulture) + " 条，应为 "
                + want.ToString(CultureInfo.InvariantCulture) + " 条";
        }
    }
}
