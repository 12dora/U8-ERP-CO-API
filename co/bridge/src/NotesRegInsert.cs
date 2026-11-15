using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 登记写入的票据行（AP_Note）：列和值照 U8 登记的当期应收票据（已在测试账套逐列核对）。分包票据另写 bsubpackage=1 和
    // 子票区间 csubnostart / csubnoend，非分包 bsubpackage=0、区间两列不写（NULL），见 NoteSplit。
    // U8 界面用 NoteManageAR 的 NoteSave 保存，无界面调用会挂起（测试账套实测），所以桥自己写这一行，与收款单在同一事务。
    // 金额四组（票面、余额、背书、收票）都等于票面，本币同额，汇率 1；cDwCode 留空（往来单位在 cEndorser）；cOperator 只有期初票据才填，不写；
    // csysbarcode 照 U8 写成「||ar50|票据号」（应付票据「||ap50|票据号」，cLink 是 AP50+号，按应收对称推断），但不建条码档案（AA_GeneralBarCode）。
    // cEndorser 是往来单位（应收是交票客户，应付是收票供应商）。U8 留空串的文本列同样写空串。
    // 纯函数：列、表达式、参数由同一张表生成，--selftest 核对列数、参数个数一致。
    internal static class NoteInsert
    {
        const string Amount = "v.a";
        const string Day = "convert(datetime, convert(date, ?, 23))";
        static readonly object NoArg = new object();

        static readonly string[] Blank = new string[]
        {
            "cDwCode", "cSuperBank", "cPayBillAccount", "cPayBillBankID", "cPayBillBankAdress", "dProxyPayBank", "dProxyPayBankID",
            "cRemitAdress", "cReceiverCode", "cReceiveBankID", "cReceiveAdress", "cPurpose", "cPayMode", "cCompactID", "cAgreementID",
            "cSendDesc", "cConsignPayee", "cRemark", "cAPDesc", "cDefine1", "cDefine2", "cDefine3", "cDefine8", "cDefine9", "cDefine10",
            "cDefine11", "cDefine12", "cDefine13", "cDefine14"
        };

        static readonly string[] Face = new string[]
        {
            "iAmount", "iAmount_Local", "iRAmount", "iRAmount_Local", "iEndorAmount", "iEndorAmount_Local", "iReceiveAmount",
            "iReceiveAmount_Local"
        };

        static readonly string[] Zero = new string[]
        {
            "iRate", "bStartFlag", "iCloseID", "VT_ID", "iSettleAmount", "iOverAmount", "iAccessories", "iPrintCount", "bSecurityDeposit",
            "iChangeType"
        };

        // 一列：列名、值表达式、参数（NoArg 表示表达式里没有 ?）。
        sealed class Col
        {
            public string Name;
            public string Expr;
            public object Arg;
        }

        public static string Link(string flag, string no)
        {
            return flag + "50" + no;
        }

        public static string BarCode(string flag, string no)
        {
            return "||" + flag.ToLowerInvariant() + "50|" + no;
        }

        // insert into AP_Note (列…) select 值… from (select convert(money, ?) as a) v：票面金额只作一个参数，放在最后。
        public static string Sql(NoteRegPlan plan)
        {
            List<Col> cols = Cols(plan);
            StringBuilder names = new StringBuilder();
            StringBuilder values = new StringBuilder();
            foreach (Col c in cols)
            {
                names.Append(names.Length == 0 ? "" : ", ").Append(c.Name);
                values.Append(values.Length == 0 ? "" : ", ").Append(c.Expr);
            }
            return "insert into AP_Note (" + names + ") select " + values + " from (select convert(money, ?) as a) v";
        }

        public static object[] Args(NoteRegPlan plan)
        {
            List<object> args = new List<object>();
            foreach (Col c in Cols(plan))
            {
                if (!object.ReferenceEquals(c.Arg, NoArg))
                {
                    args.Add(c.Arg);
                }
            }
            args.Add(ArapReq.Money(plan.Ask.Amount));
            return args.ToArray();
        }

        public static string[] Names(NoteRegPlan plan)
        {
            List<string> names = new List<string>();
            foreach (Col c in Cols(plan))
            {
                names.Add(c.Name);
            }
            return names.ToArray();
        }

        static List<Col> Cols(NoteRegPlan plan)
        {
            NoteRegAsk ask = plan.Ask;
            List<Col> cols = new List<Col>();
            Put(cols, "cLink", Link(ask.Flag, ask.NoteNo));
            Lit(cols, "cVouchType", "N'50'");
            Put(cols, "cVouchID", ask.NoteNo);
            Put(cols, "cFlag", ask.Flag);
            Put(cols, "cBank", ask.DrawerBank);
            Put(cols, "cDeptCode", ask.Dept);
            Put(cols, "cPerson", ask.Person);
            Put(cols, "cDigest", ask.Digest);
            Put(cols, "cCode", plan.NoteKm);
            Put(cols, "cEndorser", ask.Partner);
            Put(cols, "cEndorserName", plan.PartnerName);
            Put(cols, "cDWName", plan.Drawer);
            Put(cols, "cReceiver", ask.Receiver);
            Put(cols, "cReceiveBank", ask.ReceiveBank);
            Put(cols, "cReceiveAccount", ask.ReceiveAccount);
            Put(cols, "cSettleCode", ask.SettleCode);
            Put(cols, "cBill", plan.Operator ?? "");
            Put(cols, "cexch_name", plan.Currency);
            Lit(cols, "nfrat", "1");
            Dated(cols, "dSignDate", ask.SignDate);
            Dated(cols, "dExpireDate", ask.ExpireDate);
            Dated(cols, "dReceiptDate", ask.ReceiptDate);
            Put(cols, "iVT_ID", plan.VtId > 0 ? (object)plan.VtId : null);
            Put(cols, "csysbarcode", BarCode(ask.Flag, ask.NoteNo));
            Lit(cols, "dcreatesystime", "getdate()");
            Many(cols, Face, Amount);
            Many(cols, Zero, "0");
            Lit(cols, "bsubpackage", ask.Split ? "1" : "0");
            if (ask.Split)
            {
                Add(cols, "csubnostart", "convert(bigint, ?)", ask.SubStart.ToString(CultureInfo.InvariantCulture));
                Add(cols, "csubnoend", "convert(bigint, ?)", ask.SubEnd.ToString(CultureInfo.InvariantCulture));
            }
            Many(cols, Blank, "N''");
            return cols;
        }

        static void Put(List<Col> cols, string name, object arg)
        {
            Add(cols, name, "?", arg);
        }

        static void Dated(List<Col> cols, string name, string day)
        {
            Add(cols, name, Day, day);
        }

        static void Lit(List<Col> cols, string name, string expr)
        {
            Add(cols, name, expr, NoArg);
        }

        static void Many(List<Col> cols, string[] names, string expr)
        {
            foreach (string name in names)
            {
                Lit(cols, name, expr);
            }
        }

        static void Add(List<Col> cols, string name, string expr, object arg)
        {
            Col c = new Col();
            c.Name = name;
            c.Expr = expr;
            c.Arg = arg;
            cols.Add(c);
        }

        // SQL 里 ? 的个数（自检用）。
        public static int Marks(string sql)
        {
            int n = 0;
            foreach (char ch in sql)
            {
                if (ch == '?')
                {
                    n++;
                }
            }
            return n;
        }
    }
}
