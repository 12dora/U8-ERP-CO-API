using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace U8Co
{
    // 并账（BZ）的查询和写。往来明细表按 AR / AP 取（WriteoffSql.Detail）；调用方的值只进参数，金额以不变区域的文本传、在 SQL 里转 decimal。
    // 写法与 U8 应收（应付）「并账」界面执行的 SQL 一致（实测核对）：
    // Ap_Proc_CancelNo 取 BZAR… / BZAP… 号，每张单据（发票按行 iBVid）照该单据的审核（Sign）行在往来明细上插一对行，
    // 出方 cDwCode = from、金额为负，入方 cDwCode = to、金额为正，cProcStyle = BZ；表头（Ap_Vouch.cDwCode 等）和发票累计都不改。
    internal static class ArapMergeSql
    {
        static readonly Regex NoPattern = new Regex("^BZ(AR|AP)[0-9]{1,20}\\z", RegexOptions.CultureInvariant);

        // U8 的取号过程（自带 BEGIN TRAN / COMMIT，嵌在桥的事务里回滚时号一并撤销；只是号可能跳过，与 U8 一样无妨）。
        const string NumberSql = "SET NOCOUNT ON; DECLARE @n nvarchar(30); "
            + "EXEC Ap_Proc_CancelNo @cType=N'BZ', @cFlag=?, @cID=N'', @iNum=@n OUTPUT; SELECT @n AS no";

        // 单据在某往来单位名下余额不为 0 的各行（iBVid → 原币、本币、数量），按行号升序。{O} 是方向（应收 借-贷、应付 贷-借）。
        const string OpenSql = "select convert(varchar(20), isnull(d.iBVid,0)) as line, {F} as f, {N} as n, {S} as s "
            + "from {D} d where d.cFlag=? and d.cCoVouchType=? and d.cCoVouchID=? and d.cDwCode=? and d.iFlag<3 "
            + "group by isnull(d.iBVid,0) having sum({OF})<>0 order by isnull(d.iBVid,0)";

        // 一行的余额（原币）：核对用。
        const string BalanceSql = "select {F} as f from {D} d where d.cFlag=? and d.cCoVouchType=? and d.cCoVouchID=? "
            + "and d.cDwCode=? and d.iFlag<3 and isnull(d.iBVid,0)=?";

        // 插一条并账行：列同 U8 并账写入的行（已在测试账套核对），从该单据该行的审核行抄部门、业务员、存货、科目、项目、汇率等，
        // 有多条审核行时优先 from 名下的那条。iFlag = 0，cPZid 空（制单另做），cCoVouchType / cCoVouchID = 单据本身；
        // isignseq、ino_id 是审核行自己凭证的期间、分录号，不抄、写空（同 U8 未制单的处理行）；审核人取审核行的（同 U8 的 BZ 行）。
        const string InsertSql = "INSERT INTO {D} (iPeriod,cVouchType,cVouchSType,cVouchID,dVouchDate,dRegDate,cDwCode,"
            + "cDeptCode,cPerson,cInvCode,iBVid,cCode,cItem_Class,cItemCode,csign,isignseq,ino_id,cDigest,iPrice,cexch_name,"
            + "iExchRate,iDAmount,iCAmount,iDAmount_f,iCAmount_f,iDAmount_s,iCAmount_s,cOrderNo,cSSCode,cPayCode,cProcStyle,"
            + "cCancelNo,cPZid,bPrePay,iFlag,cCoVouchType,cCoVouchID,cFlag,iClosesID,iCoClosesID,cOperator,cCheckMan,iOrderType,cDLCode) "
            + "SELECT TOP 1 ?, s.cVouchType, s.cVouchSType, s.cVouchID, s.dVouchDate, CAST(CONVERT(date, ?, 23) AS datetime), ?, "
            + "s.cDeptCode, s.cPerson, s.cInvCode, s.iBVid, s.cCode, s.cItem_Class, s.cItemCode, s.csign, NULL, NULL, ?, "
            + "s.iPrice, s.cexch_name, s.iExchRate, CONVERT(decimal(28,6), ?), CONVERT(decimal(28,6), ?), CONVERT(decimal(28,6), ?), "
            + "CONVERT(decimal(28,6), ?), CONVERT(decimal(28,6), ?), CONVERT(decimal(28,6), ?), s.cOrderNo, s.cSSCode, s.cPayCode, "
            + "N'BZ', ?, NULL, 0, 0, s.cVouchType, s.cVouchID, ?, s.iClosesID, s.iClosesID, ?, s.cCheckMan, s.iOrderType, s.cDLCode "
            + "FROM {D} s WHERE s.cProcStyle=s.cVouchType AND s.cFlag=? AND s.cVouchType=? AND s.cVouchID=? "
            + "AND isnull(s.iBVid,0)=? AND s.iFlag<3 ORDER BY CASE WHEN s.cDwCode=? THEN 0 ELSE 1 END, s.Auto_ID";

        const string CountSql = "select convert(varchar(20), count(*)) from {D} d where d.cProcStyle=N'BZ' and d.cCancelNo=? and d.cFlag=?";

        // 往来单位：存在、并账日期未停用。
        const string CustomerSql = "select cCusCode as code, convert(varchar(10), dEndDate, 23) as ended from Customer where cCusCode=?";
        const string VendorSql = "select cVenCode as code, convert(varchar(10), dEndDate, 23) as ended from Vendor where cVenCode=?";

        static string Of(string sql, string flag)
        {
            return sql.Replace("{D}", WriteoffSql.Detail(flag));
        }

        // 余额方向：应收 借-贷、应付 贷-借。suffix 是 ""（本币）、"_f"（原币）、"_s"（数量）。
        internal static string Open(string flag, string suffix)
        {
            string d = "isnull(d.iDAmount" + suffix + ",0)";
            string c = "isnull(d.iCAmount" + suffix + ",0)";
            return flag == "AP" ? c + "-" + d : d + "-" + c;
        }

        internal static string OpenQuery(string flag)
        {
            return Of(OpenSql, flag).Replace("{OF}", Open(flag, "_f"))
                .Replace("{F}", WriteoffSql.Dec("sum(" + Open(flag, "_f") + ")", 2))
                .Replace("{N}", WriteoffSql.Dec("sum(" + Open(flag, "") + ")", 2))
                .Replace("{S}", WriteoffSql.Dec("sum(" + Open(flag, "_s") + ")", 6));
        }

        public static List<MergeLine> OpenLines(object conn, string flag, string type, string code, string dw)
        {
            List<MergeLine> lines = new List<MergeLine>();
            foreach (Dictionary<string, object> r in Rows.Query(conn, OpenQuery(flag), new object[] { flag, type, code, dw }, 1001))
            {
                MergeLine line = new MergeLine();
                line.Type = type;
                line.Code = code;
                line.Line = CoRows.AsId(CoRows.Col(r, "line"));
                line.BalF = WriteoffSql.Num(CoRows.Col(r, "f"));
                line.BalN = WriteoffSql.Num(CoRows.Col(r, "n"));
                line.BalS = WriteoffSql.Num(CoRows.Col(r, "s"));
                lines.Add(line);
            }
            return lines;
        }

        public static decimal Balance(object conn, string flag, MergeLine line, string dw)
        {
            string sql = Of(BalanceSql, flag).Replace("{F}", WriteoffSql.Dec("sum(" + Open(flag, "_f") + ")", 2));
            return WriteoffSql.Num(Rows.Scalar(conn, sql, new object[] { flag, line.Type, line.Code, dw, line.Line }));
        }

        // 取号并核对格式、该号下还没有行；不符 409 u8_rejected（事务随之回滚）。
        public static string Number(object conn, string flag)
        {
            string no = Rows.Scalar(conn, NumberSql, new object[] { flag });
            no = no == null ? "" : no.Trim();
            if (!IsNumber(no, flag))
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有分配并账号" + (no.Length > 0 ? "：" + no : ""));
            }
            if (Count(conn, flag, no) != 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 分配的并账号 " + no + " 已被占用，请检查 Ap_CancelNo");
            }
            return no;
        }

        internal static bool IsNumber(string no, string flag)
        {
            return no != null && NoPattern.IsMatch(no) && no.Substring(2, 2) == flag;
        }

        public static int Count(object conn, string flag, string no)
        {
            return CoRows.AsId(Rows.Scalar(conn, Of(CountSql, flag), new object[] { no, flag }));
        }

        // 插出方或入方一行。
        public static void Insert(object conn, MergePlan plan, MergeLine line, bool target)
        {
            GlSql.Exec(conn, InsertQuery(plan.Flag), InsertArgs(plan, line, target));
        }

        internal static string InsertQuery(string flag)
        {
            return Of(InsertSql, flag);
        }

        // InsertSql 的参数，顺序与 ? 一致（--selftest 核对个数）。
        internal static object[] InsertArgs(MergePlan plan, MergeLine line, bool target)
        {
            decimal[] amounts = ArapMergePlan.Amounts(plan.Flag, line, target);
            List<object> args = new List<object>();
            args.Add(plan.Period);
            args.Add(plan.Date);
            args.Add(target ? plan.To : plan.From);
            args.Add(plan.Digest);
            foreach (decimal value in amounts)
            {
                args.Add(Text(value));
            }
            args.Add(plan.CancelNo);
            args.Add(plan.Flag);
            args.Add(plan.User);
            args.Add(plan.Flag);
            args.Add(line.Type);
            args.Add(line.Code);
            args.Add(line.Line);
            args.Add(plan.From);
            return args.ToArray();
        }

        internal static int Marks(string flag)
        {
            int n = 0;
            foreach (char ch in InsertQuery(flag))
            {
                if (ch == '?')
                {
                    n++;
                }
            }
            return n;
        }

        internal static string Text(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }

        // 往来单位存在（404），date 不为 null 时还要在该日（yyyy-MM-dd）未停用（409）；返回档案上的编码写法。
        // 并出单位不查停用（合并重复客户、供应商时它往往已停用）。
        public static string Partner(object conn, string flag, string code, string date, string label)
        {
            Dictionary<string, object> row = Rows.One(conn, flag == "AP" ? VendorSql : CustomerSql, new object[] { code });
            if (row == null)
            {
                throw new BridgeException(404, "not_found", label + " " + code + " 不存在");
            }
            string ended = CoRows.Col(row, "ended");
            if (date != null && ended.Length > 0 && string.CompareOrdinal(ended, date) <= 0)
            {
                throw ArapMergePlan.State(label + " " + code + " 已停用");
            }
            string exact = CoRows.Col(row, "code").Trim();
            return exact.Length == 0 ? code : exact;
        }
    }
}
