using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 取消坏账处理（ArapProcCancelBad）的 SQL：识别处理方式、脚本参数表和临时表、脚本拒绝编号到中文、发票累计核对、删行、回读。
    // 参数表、发票回写临时表用不带参数的整段 SQL 建（Connection.Execute，不经 sp_executesql），留在请求连接上，
    // 脚本 sql/arap/bad_cancel.sql 和 U8 的 clsWrite2Bill（同一连接）都看得见。
    internal static class ArapProcCancelBadSql
    {
        internal const string Script = "sql/arap/bad_cancel.sql";

        internal const string PrepSql = "SET NOCOUNT ON;"
            + " IF OBJECT_ID('tempdb..#badc_args') IS NOT NULL DROP TABLE #badc_args;"
            + " CREATE TABLE #badc_args (flag nvarchar(4), style nvarchar(4), cancel_no nvarchar(30), acc nvarchar(10));"
            + " IF OBJECT_ID('tempdb..#ap_SaleBillVouchHXdata') IS NOT NULL DROP TABLE #ap_SaleBillVouchHXdata;"
            + " CREATE TABLE #ap_SaleBillVouchHXdata (autoid bigint, iexchsum decimal(29,6), imoneysum decimal(29,6));"
            + " CREATE INDEX idx_HXautoid ON #ap_SaleBillVouchHXdata (autoid);"
            + " IF OBJECT_ID('tempdb..#badc_bill') IS NOT NULL DROP TABLE #badc_bill;"
            + " CREATE TABLE #badc_bill (id bigint, a decimal(29,6), b decimal(29,6));"
            + " SET NOCOUNT OFF;";

        internal const string DropSql = "SET NOCOUNT ON;"
            + " IF OBJECT_ID('tempdb..#badc_args') IS NOT NULL DROP TABLE #badc_args;"
            + " IF OBJECT_ID('tempdb..#ap_SaleBillVouchHXdata') IS NOT NULL DROP TABLE #ap_SaleBillVouchHXdata;"
            + " IF OBJECT_ID('tempdb..#badc_bill') IS NOT NULL DROP TABLE #badc_bill;"
            + " IF OBJECT_ID('tempdb..#badc_snap') IS NOT NULL DROP TABLE #badc_snap;"
            + " IF OBJECT_ID('tempdb..#badc_vouch') IS NOT NULL DROP TABLE #badc_vouch;"
            + " SET NOCOUNT OFF;";

        const string ArgsFill = "INSERT INTO #badc_args (flag, style, cancel_no, acc) VALUES (N'AR', ?, ?, ?)";

        // 处理号下的 9G / 9H 往来明细（带锁）：处理方式和数据权限用的往来单位、部门、业务员。
        const string DetailSql = "select d.cProcStyle as style, d.cDwCode, d.cDeptCode, d.cPerson from Ar_Detail d with (UPDLOCK, HOLDLOCK) "
            + "where d.cCancelNo=? and d.cProcStyle in (N'9G',N'9H')";
        // 9F：处理号在坏账准备参数上（只认还没取消的，dJtDate 非空）。
        const string ParaSql = "select top 1 convert(varchar(10), b.iYear) as y from Ar_BadPara b with (UPDLOCK, HOLDLOCK) "
            + "where b.cCancelNo=? and b.cProcStyle=N'9F' and b.dJtDate is not null";

        // 发票累计核对：临时表里有、快照里没有的发票行（行已不存在）也算不符。累计 = 快照 + 临时表合计（U8 自己的公式）。
        const string BillBad = "select top 1 convert(varchar(20), s.autoid) from (select autoid, sum(iexchsum) a, sum(imoneysum) b"
            + " from #ap_SaleBillVouchHXdata group by autoid) s left join #badc_bill p on p.id = s.autoid"
            + " left join SaleBillVouchs x on x.AutoID = s.autoid"
            + " where p.id is null or x.AutoID is null or abs(isnull(x.iExchSum,0) - (p.a + s.a)) > 0.005"
            + " or abs(isnull(x.iMoneySum,0) - (p.b + s.b)) > 0.005";

        // 开事务之前判断是不是坏账收回（不加锁，只决定要不要先打开收款单审核组件；事务里 Detect 带锁再认）。
        const string RecoverSql = "select top 1 convert(varchar(20), d.Auto_ID) from Ar_Detail d where d.cCancelNo=? and d.cProcStyle=N'9H'";
        // 取消坏账收回之后的收款单：审核人、审核日期、余额未还原的行数、往来明细行数。
        const string ReceiptSql = "select isnull(h.cCheckMan,N'') as auditor, isnull(convert(varchar(10), h.dverifydate, 23),'') as vdate,"
            + " (select convert(varchar(20), count(*)) from Ap_CloseBills b where b.iID=h.iID and (isnull(b.iRAmt,0)<>isnull(b.iAmt,0)"
            + " or isnull(b.iRAmt_f,0)<>isnull(b.iAmt_f,0))) as open_lines,"
            + " (select convert(varchar(20), count(*)) from Ar_Detail d where d.cFlag=N'AR' and ((d.cVouchType=N'48' and d.cVouchID=h.cVouchID)"
            + " or (d.cCoVouchType=N'48' and d.cCoVouchID=h.cVouchID))) as details"
            + " from Ap_CloseBill h where h.iID=? and h.cVouchType=N'48' and h.cFlag=N'AR'";

        const string DeleteSql = "DELETE FROM Ar_Detail WHERE cProcStyle=? AND cCancelNo=? AND cFlag=N'AR'";
        const string CountSql = "select convert(varchar(20), count(*)) from Ar_Detail where cProcStyle=? and cCancelNo=?";
        const string ParaLeftSql = "select convert(varchar(20), count(*)) from Ar_BadPara where cCancelNo=? and cProcStyle=N'9F' "
            + "and dJtDate is not null";

        static readonly Dictionary<int, string> Texts = BuildTexts();

        static Dictionary<int, string> BuildTexts()
        {
            Dictionary<int, string> map = new Dictionary<int, string>();
            map[50141] = "应收该期间已结账，不能取消坏账处理{0}";
            map[50142] = "未设置坏账准备参数（应收款管理 › 设置 › 坏账准备）";
            map[50143] = "坏账收回的收款单不存在{0}";
            map[50144] = ArapProcCancelGate.Vouchered;
            map[50145] = ArapProcCancelGate.Later;
            map[50147] = "该处理号对应多行坏账准备参数，请在 U8 客户端取消";
            map[50148] = "处理记录的日期不一致，请在 U8 客户端取消";
            map[50149] = "坏账准备参数的最新一行不是处理所在年度的，不能取消，请在 U8 客户端处理{0}";
            map[50150] = "处理记录的形状不符，请在 U8 客户端取消";
            map[50151] = "坏账收回的收款单已制单，请先取消制单";
            map[50152] = ArapProcCancelGate.Incomplete;
            map[50153] = "处理涉及合同，请在 U8 客户端取消";
            map[50154] = "处理日期不在 U8 的会计期间里，不能取消{0}";
            map[50155] = "该坏账收回不是本接口生成的（收款单没有审核行），请在 U8 客户端取消";
            map[50156] = "该年度有多行坏账准备参数，不能取消计提，请在 U8 客户端处理{0}";
            map[50157] = "坏账收回的收款单号不唯一，请在 U8 客户端取消{0}";
            return map;
        }

        // 处理号是哪一种坏账处理：9G / 9H 看往来明细，9F 看坏账准备参数；都没有 404。rows 收 9G / 9H 的明细行（数据权限用）。
        public static string Detect(object conn, string cancelNo, List<Dictionary<string, object>> rows)
        {
            rows.AddRange(Rows.Query(conn, DetailSql, new object[] { cancelNo }, ArapProcCancelGate.MaxRows + 1));
            if (rows.Count > ArapProcCancelGate.MaxRows)
            {
                throw ArapProcCancelGate.State("处理记录超过 500 行，请在 U8 客户端取消");
            }
            string style = StyleOf(rows);
            if (style != null)
            {
                return style;
            }
            if (Rows.Scalar(conn, ParaSql, new object[] { cancelNo }) != null)
            {
                return "9F";
            }
            throw NotFound(cancelNo);
        }

        // 明细行的处理方式：没有行返回 null，混了两种 409。纯函数，--selftest 用。
        internal static string StyleOf(List<Dictionary<string, object>> rows)
        {
            string style = null;
            foreach (Dictionary<string, object> row in rows)
            {
                string one = CoRows.Col(row, "style");
                if (style != null && one != style)
                {
                    throw ArapProcCancelGate.State("处理号下同时有坏账发生和坏账收回的记录，请在 U8 客户端取消");
                }
                style = one;
            }
            return style;
        }

        internal static BridgeException NotFound(string cancelNo)
        {
            return new BridgeException(404, "not_found", "处理号 " + cancelNo + " 不存在（坏账计提只能取消各年度最近一次、尚未取消的计提）");
        }

        public static bool IsRecover(object conn, string cancelNo)
        {
            return Rows.Scalar(conn, RecoverSql, new object[] { cancelNo }) != null;
        }

        // 取消坏账收回、弃审之后（同一事务）：收款单应未审核、余额已还原、没有往来明细。不符返回原因，符合返回 null。
        public static string ReceiptProblem(object conn, ScriptResult result)
        {
            int id = ReceiptId(result);
            Dictionary<string, object> row = id > 0 ? Rows.One(conn, ReceiptSql, new object[] { id }) : null;
            return ReceiptRefusal(row);
        }

        // 9H 脚本第一个结果集里收款单的 iID；没有返回 0。
        public static int ReceiptId(ScriptResult result)
        {
            if (result == null || result.Rows == null || result.Rows.Count == 0)
            {
                return 0;
            }
            return CoRows.AsId(CoRows.Col(result.Rows[0], "doc_id"));
        }

        // 收款单复原状态的判断。纯函数，--selftest 用。
        internal static string ReceiptRefusal(Dictionary<string, object> row)
        {
            if (row == null)
            {
                return "读不到收款单";
            }
            if (CoRows.Col(row, "auditor").Length > 0 || CoRows.Col(row, "vdate").Length > 0)
            {
                return "收款单仍是已审核状态";
            }
            if (CoRows.AsId(CoRows.Col(row, "open_lines")) != 0)
            {
                return "收款单行的余额没有还原";
            }
            return CoRows.AsId(CoRows.Col(row, "details")) != 0 ? "收款单上还有往来明细" : null;
        }

        // acc 是登录账套号：脚本按 UA_Period 定处理日期的会计年度、期间（同新增）。
        public static void Prepare(object conn, string acc, string style, string cancelNo)
        {
            UnwriteoffSql.Run(conn, PrepSql);
            GlSql.Exec(conn, ArgsFill, new object[] { style, cancelNo, acc ?? "" });
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

        // 销售发票累计核对（UpdateBillForAR 之后）：不符返回发票行号，否则 null。
        public static string BillMismatch(object conn)
        {
            string line = Rows.Scalar(conn, BillBad, new object[0]);
            return line == null || line.Trim().Length == 0 ? null : line.Trim();
        }

        // 9G 删行（COM 之后，同 U8 的顺序），返回删掉的行数。
        public static int Delete(object conn, string style, string cancelNo)
        {
            GlSql.Exec(conn, DeleteSql, new object[] { style, cancelNo });
            return Count(conn, style, cancelNo);
        }

        // 还剩的处理行：9G / 9H 数往来明细，9F 数还挂着该处理号、计提日期非空的参数行。
        public static int Count(object conn, string style, string cancelNo)
        {
            string sql = style == "9F" ? ParaLeftSql : CountSql;
            object[] args = style == "9F" ? new object[] { cancelNo } : new object[] { style, cancelNo };
            return CoRows.AsId(Rows.Scalar(conn, sql, args));
        }

        // 脚本拒绝 → 中文。50146（处理号不存在）是 404，50159（写后核对不符）409 u8_rejected，50140（参数无效，防御性）和没登记的编号 500，
        // 其余 409 state_mismatch。
        internal static BridgeException Refused(ScriptRefusal refusal, string cancelNo)
        {
            int number = refusal == null ? 0 : refusal.Number;
            string tail = ArapExGainSql.TailOf(refusal == null ? "" : refusal.Message);
            if (number == 50146)
            {
                return NotFound(cancelNo);
            }
            if (number == 50159)
            {
                return new BridgeException(409, "u8_rejected", "取消坏账处理后核对不符，已回滚");
            }
            string text;
            if (!Texts.TryGetValue(number, out text))
            {
                return new BridgeException(500, "internal", "坏账取消脚本拒绝 " + number.ToString(CultureInfo.InvariantCulture));
            }
            return ArapProcCancelGate.State(string.Format(CultureInfo.InvariantCulture, text, tail.Length > 0 ? "：" + tail : ""));
        }
    }
}
