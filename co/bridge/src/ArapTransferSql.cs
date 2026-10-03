using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 应收冲应付 / 应付冲应收的 SQL。往来明细表按单据所在一侧取（WriteoffSql.Detail）；调用方的值只进参数，金额以文本传入再 convert。
    // 写处理行与 U8 界面写入的行一致（已在测试账套逐列核对）：
    // 从该单据（行）的审核行（cProcStyle = cVouchType）复制部门、业务员、存货、科目 cCode、发票行 iBVid、自定义项等，
    // 改写期间、登记日期、摘要、金额、处理方式、处理号、cFlag、操作员，iFlag 写 0，清凭证列。
    internal static class TransferSql
    {
        // 与 U8 写入的列相同（已在测试账套逐列核对）。
        static readonly string[] Cols = new string[]
        {
            "iPeriod", "cVouchType", "cVouchSType", "cVouchID", "dVouchDate", "dRegDate", "cDwCode", "cDeptCode", "cPerson",
            "cInvCode", "iBVid", "cCode", "cItem_Class", "cItemCode", "csign", "isignseq", "ino_id", "cDigest", "iPrice",
            "cexch_name", "iExchRate", "iDAmount", "iCAmount", "iDAmount_f", "iCAmount_f", "iDAmount_s", "iCAmount_s",
            "cOrderNo", "cSSCode", "cPayCode", "cProcStyle", "cCancelNo", "cPZid", "bPrePay", "iFlag", "cCoVouchType",
            "cCoVouchID", "cFlag", "cDefine1", "cDefine2", "cDefine3", "cDefine4", "cDefine5", "cDefine6", "cDefine7",
            "cDefine8", "cDefine9", "cDefine10", "iClosesID", "iCoClosesID", "cDefine11", "cDefine12", "cDefine13",
            "cDefine14", "cDefine15", "cDefine16", "cGLSign", "iGLno_id", "dPZDate", "cItemName", "cContractType",
            "cContractID", "BalancesGuid", "dHideDate", "cGatheringPlan", "dCreditStart", "iCreditPeriod", "dGatheringDate",
            "bCredit", "cOperator", "cCheckMan", "iOrderType", "cDLCode"
        };

        // 不带参数的固定值：未制单、非预收预付、对方单据就是自身。凭证期间 isignseq、分录号 ino_id 不抄审核行
        // （那是审核行自己凭证的），按 U8 未制单处理行的样子写空，制单时回写。
        static readonly string[][] Fixed = new string[][]
        {
            new string[] { "cPZid", "NULL" }, new string[] { "bPrePay", "0" }, new string[] { "iFlag", "0" },
            new string[] { "isignseq", "NULL" }, new string[] { "ino_id", "NULL" }, new string[] { "cGLSign", "NULL" },
            new string[] { "iGLno_id", "NULL" }, new string[] { "dPZDate", "NULL" }, new string[] { "dHideDate", "NULL" },
            new string[] { "iDAmount_s", "0" }, new string[] { "iCAmount_s", "0" },
            new string[] { "cCoVouchType", "s.cVouchType" }, new string[] { "cCoVouchID", "s.cVouchID" },
            new string[] { "iCoClosesID", "s.iClosesID" }
        };

        const string Money = "convert(decimal(28,2), ?)";

        // Ap_Proc_CancelNo 的输出参数取不回 ADO 的记录集，先放进本连接上的临时表再读（类型、标志是代码里的常量）。
        const string NoSql = "SET NOCOUNT ON; IF OBJECT_ID('tempdb..#ap_procno') IS NOT NULL DROP TABLE #ap_procno; "
            + "CREATE TABLE #ap_procno(cno nvarchar(40)); DECLARE @n nvarchar(40); "
            + "EXEC Ap_Proc_CancelNo @cType=N'{t}', @cFlag=N'{f}', @cID=N'', @iNum=@n OUTPUT; "
            + "INSERT INTO #ap_procno(cno) VALUES(@n); SET NOCOUNT OFF;";

        // 应收单 / 应付单表头余额（同 U8 转账后的余额变化；iRAmount_s 用 U8 的比例公式，实测核对）。参数 (原币, 本币, 原币, 原币, 类型, 单号, AR|AP)。
        const string BillSql = "update Ap_Vouch set iRAmount_f=iRAmount_f-" + Money + ", iRAmount=iRAmount-" + Money + ", "
            + "iRAmount_s=case when iAmount_f<>0 then iAmount_s*(iRAmount_f-" + Money + ")/iAmount_f else iRAmount_f-" + Money
            + " end where cVouchType=? and cVouchID=? and cFlag=?";

        // 采购发票行累计（同 9P 核销的 PurBillVouchs 回写）。参数 (原币, 本币, 行 ID)。
        const string PurSql = "update PurBillVouchs set iOriTotal=isnull(iOriTotal,0)+" + Money + ", iTotal=isnull(iTotal,0)+"
            + Money + " where ID=?";

        public static string Detail(string side)
        {
            return WriteoffSql.Detail(side);
        }

        // 单据（行）的审核行条件，别名 s；参数追加到 args：(类型, 单号, AR|AP, 往来单位[, 行])。
        internal static string SignWhere(TransferDoc doc, int line, string dw, List<object> args)
        {
            args.Add(doc.Ask.Type);
            args.Add(doc.Ask.Code);
            args.Add(doc.Side);
            args.Add(dw);
            string where = "s.cProcStyle=s.cVouchType and s.cVouchType=? and s.cVouchID=? and s.cFlag=? and s.cDwCode=? "
                + "and s.iFlag<3";
            if (ArapTransferRule.Mode(doc.Ask.Type) == ArapTransferRule.WholeDoc)
            {
                return where + " and isnull(s.iBVid,0)=0";
            }
            args.Add(line);
            return where + " and s.iBVid=?";
        }

        // 审核行：币种、汇率、合同号、科目（读）。没有返回 null。
        public static Dictionary<string, object> SignRow(object conn, TransferDoc doc, int line, string dw)
        {
            List<object> args = new List<object>();
            string where = SignWhere(doc, line, dw, args);
            string sql = "select top 1 convert(varchar(20), s.Auto_ID) as auto, s.cexch_name as cur, "
                + WriteoffSql.Dec("s.iExchRate", 10) + " as rate, isnull(s.cContractID,N'') as contract "
                + "from " + Detail(doc.Side) + " s where " + where + " order by s.Auto_ID";
            return Rows.One(conn, sql, args.ToArray());
        }

        // 处理行的 INSERT … SELECT TOP 1 … FROM 审核行。
        // 选择列表的参数按列的顺序追加，条件的参数随后，与 SQL 里 ? 的顺序一致。
        internal static string Insert(TransferPlan plan, TransferPiece p, string dw, List<object> args)
        {
            Dictionary<string, string> fix = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (string[] pair in Fixed)
            {
                fix[pair[0]] = pair[1];
            }
            Dictionary<string, object> vals = Values(plan, p);
            StringBuilder sel = new StringBuilder();
            foreach (string col in Cols)
            {
                sel.Append(sel.Length > 0 ? ", " : string.Empty).Append(Expr(col, fix, vals, args));
            }
            string where = SignWhere(p.Doc, p.Line, dw, args);
            string d = Detail(p.Doc.Side);
            return "insert into " + d + " (" + string.Join(", ", Cols) + ") select top 1 " + sel + " from " + d + " s where "
                + where + " order by s.Auto_ID";
        }

        static string Expr(string col, Dictionary<string, string> fix, Dictionary<string, object> vals, List<object> args)
        {
            string expr;
            if (fix.TryGetValue(col, out expr))
            {
                return expr;
            }
            object val;
            if (!vals.TryGetValue(col, out val))
            {
                return "s." + col;
            }
            if (val is decimal)
            {
                decimal money = (decimal)val;
                if (money == 0m)
                {
                    return "0";
                }
                args.Add(ArapTransferRule.Money(money));
                return Money;
            }
            args.Add(val);
            return col == "dRegDate" ? "convert(datetime, convert(date, ?, 23))" : "?";
        }

        // 按参数写的列。金额是 decimal（0 写常数），其余是字符串或整数。
        // 应收一侧的审核人写操作员、应付一侧取审核行的（同 U8）。
        static Dictionary<string, object> Values(TransferPlan plan, TransferPiece p)
        {
            bool credit = ArapTransferRule.Credit(p.Doc.Side);
            Dictionary<string, object> v = new Dictionary<string, object>(StringComparer.Ordinal);
            v["iPeriod"] = plan.Period;
            v["dRegDate"] = plan.Date;
            v["cDigest"] = plan.Digest;
            v["iDAmount"] = credit ? 0m : p.N;
            v["iCAmount"] = credit ? p.N : 0m;
            v["iDAmount_f"] = credit ? 0m : p.F;
            v["iCAmount_f"] = credit ? p.F : 0m;
            v["cProcStyle"] = plan.Style;
            v["cCancelNo"] = plan.CancelNo;
            v["cFlag"] = p.Doc.Side;
            v["cOperator"] = plan.Operator;
            if (p.Doc.Side == "AR")
            {
                v["cCheckMan"] = plan.Operator;
            }
            return v;
        }

        // 取号（在请求连接的事务里执行；U8 同样直接调这个过程）。返回 null 表示过程没有给出号。
        public static string CancelNo(object conn, string flag)
        {
            string[] a = ArapTransferRule.NoArgs(flag);
            UnwriteoffSql.Run(conn, NoSql.Replace("{t}", a[0]).Replace("{f}", a[1]));
            string no = Rows.Scalar(conn, "select top 1 cno from #ap_procno", new object[0]);
            UnwriteoffSql.Run(conn, "DROP TABLE #ap_procno");
            return no == null ? null : no.Trim();
        }

        // 号已出现在任一张往来明细的同一处理方式上（按索引 cProcStyle, cCancelNo 定位）。
        public static bool Used(object conn, string style, string no)
        {
            string sql = "select top 1 'x' from Ar_Detail where cProcStyle=? and cCancelNo=? "
                + "union all select top 1 'x' from Ap_Detail where cProcStyle=? and cCancelNo=?";
            return Rows.Scalar(conn, sql, new object[] { style, no, style, no }) != null;
        }

        public static int CountRows(object conn, string side, string style, string no)
        {
            string sql = "select convert(varchar(20), count(*)) from " + Detail(side)
                + " d where d.cProcStyle=? and d.cCancelNo=? and d.cFlag=?";
            return CoRows.AsId(Rows.Scalar(conn, sql, new object[] { style, no, side }));
        }

        // 发票行 / 应收应付单整单（line 0）的余额：原币、本币，应收 借-贷、应付 贷-借，口径同核销（WriteoffSql.Balance）。
        public static TransferSlot Remain(object conn, TransferDoc doc, int line, string dw)
        {
            string sql = RemainSql(doc.Side) + " and isnull(d.iBVid,0)=?";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { doc.Side, doc.Ask.Type, doc.Ask.Code, dw, line });
            TransferSlot slot = new TransferSlot();
            slot.Line = line;
            slot.RemainF = WriteoffSql.Num(CoRows.Col(row, "f"));
            slot.RemainN = WriteoffSql.Num(CoRows.Col(row, "n"));
            return slot;
        }

        // 整张单据（各行合计）的余额（原币），红字判定用。
        public static decimal DocRemain(object conn, TransferDoc doc, string dw)
        {
            Dictionary<string, object> row = Rows.One(conn, RemainSql(doc.Side), new object[] { doc.Side, doc.Ask.Type, doc.Ask.Code, dw });
            return WriteoffSql.Num(CoRows.Col(row, "f"));
        }

        // 单据余额现在所在的往来单位（并账后可能不是审核行上的）：余额不为 0 的第一个；都为 0 返回 null。
        public static string Holder(object conn, TransferDoc doc)
        {
            string f = Open(doc.Side, "_f");
            string sql = "select top 1 d.cDwCode from " + Detail(doc.Side) + " d where d.cFlag=? and d.cCoVouchType=? "
                + "and d.cCoVouchID=? and d.iFlag<3 group by d.cDwCode having sum(" + f + ")<>0 order by d.cDwCode";
            string dw = Rows.Scalar(conn, sql, new object[] { doc.Side, doc.Ask.Type, doc.Ask.Code });
            return dw == null ? null : dw.Trim();
        }

        static string RemainSql(string side)
        {
            string f = Open(side, "_f");
            string n = Open(side, "");
            return "select " + WriteoffSql.Dec("sum(" + f + ")", 2) + " as f, " + WriteoffSql.Dec("sum(" + n + ")", 2)
                + " as n from " + Detail(side) + " d where d.cFlag=? and d.cCoVouchType=? and d.cCoVouchID=? "
                + "and d.cDwCode=? and d.iFlag<3";
        }

        static string Open(string side, string suffix)
        {
            string d = "isnull(d.iDAmount" + suffix + ",0)";
            string c = "isnull(d.iCAmount" + suffix + ",0)";
            return side == "AP" ? c + "-" + d : d + "-" + c;
        }

        // 发票行的累计核销（带锁）：销售 iExchSum / iMoneySum，采购 iOriTotal / iTotal。
        public static decimal[] InvoiceAcc(object conn, string type, int line)
        {
            bool sale = type == "26" || type == "27";
            string sql = sale
                ? "select " + WriteoffSql.Dec("x.iExchSum", 2) + " as a, " + WriteoffSql.Dec("x.iMoneySum", 2)
                    + " as b from SaleBillVouchs x with (UPDLOCK, HOLDLOCK) where x.AutoID=?"
                : "select " + WriteoffSql.Dec("x.iOriTotal", 2) + " as a, " + WriteoffSql.Dec("x.iTotal", 2)
                    + " as b from PurBillVouchs x with (UPDLOCK, HOLDLOCK) where x.ID=?";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { line });
            return new decimal[] { WriteoffSql.Num(CoRows.Col(row, "a")), WriteoffSql.Num(CoRows.Col(row, "b")) };
        }

        // U8 打开单据时写的网络锁（实测）：有人正占用时返回工作站名（可能为空串），没有返回 null。
        // 键的写法未在客户端核对，单号和主键两种都查。
        public static string LockedBy(object conn, string type, string code, int id)
        {
            string sql = "select top 1 isnull(cWorkStation,N'') as ws from LockVouch with (NOLOCK) "
                + "where cVouchType=? and (ID=? or ID=?)";
            string key = id.ToString(System.Globalization.CultureInfo.InvariantCulture);
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { type, code, key });
            return row == null ? null : CoRows.Col(row, "ws");
        }

        // 客户 / 供应商名称；档案不存在返回 null。
        public static string PartnerName(object conn, string side, string code)
        {
            string sql = side == "AP" ? "select isnull(cVenName,N'') as v from Vendor where cVenCode=?"
                : "select isnull(cCusName,N'') as v from Customer where cCusCode=?";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { code });
            return row == null ? null : CoRows.Col(row, "v").Trim();
        }

        public static void Bill(object conn, TransferDoc doc, decimal f, decimal n)
        {
            string ff = ArapTransferRule.Money(f);
            GlSql.Exec(conn, BillSql, new object[]
            {
                ff, ArapTransferRule.Money(n), ff, ff, doc.Ask.Type, doc.Ask.Code, doc.Side
            });
        }

        public static void PurLine(object conn, TransferPiece p)
        {
            GlSql.Exec(conn, PurSql, new object[] { ArapTransferRule.Money(p.F), ArapTransferRule.Money(p.N), p.Line });
        }

        // 销售发票行的本次金额放进 #ap_SaleBillVouchHXdata（表由 ArapUnwriteoffBill.SaleTmp 建在本连接上），交给 UpdateBillForAR 加到累计。
        public static void SaleLine(object conn, TransferPiece p)
        {
            GlSql.Exec(conn, "insert into #ap_SaleBillVouchHXdata (autoid, iexchsum, imoneysum) values (?, "
                + "convert(decimal(29,6), ?), convert(decimal(29,6), ?))",
                new object[] { p.Line, ArapTransferRule.Money(p.F), ArapTransferRule.Money(p.N) });
        }
    }
}
