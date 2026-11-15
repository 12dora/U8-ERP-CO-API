using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一张票据（AP_Note）的快照。Ufts 是 4 位小数的 money 文本（删除时在事务里按它确认票据没被改过）。
    internal sealed class NoteRow
    {
        public int Id;
        public string Code;
        public string Link;
        public string Ufts;
        public int CloseId;
        public string Km;
        public string Partner;
        public string Dept;
        public string Person;
        public bool Opening;
        public int Change;
        public decimal Amount;
        public decimal Remain;
        public int Subs;
        // 分包票据（bsubpackage）与它的子票区间（csubnostart / csubnoend，非分包为 0）。
        public bool Split;
        public long Start;
        public long End;
    }

    // 票据登记、删除的 SQL。调用方的值只进参数；表名列名都是常量。
    internal static class NotesRegSql
    {
        const string Ufts4 = "ltrim(rtrim(convert(varchar(40), convert(money, {0}.Ufts), 2)))";

        static readonly string NoteSql = "select convert(varchar(20), n.Auto_ID) as id, n.cVouchID as code, n.cLink as link, "
            + string.Format(Ufts4, "n") + " as ufts, convert(varchar(20), isnull(n.iCloseID,0)) as close_id, "
            + "isnull(n.cCode,N'') as km, isnull(n.cEndorser,N'') as partner, isnull(n.cDeptCode,N'') as dept, "
            + "isnull(n.cPerson,N'') as person, case when isnull(n.bStartFlag,0)<>0 then 1 else 0 end as opening, "
            + "convert(varchar(20), isnull(n.iChangeType,0)) as change, {AMT} as amount, {REM} as remain, "
            + "(select convert(varchar(20), count(*)) from AP_Note_Sub s where s.cLink=n.cLink) as subs, "
            + "case when isnull(n.bsubpackage,0)<>0 then 1 else 0 end as split, convert(varchar(20), n.csubnostart) as sub_start, "
            + "convert(varchar(20), n.csubnoend) as sub_end from AP_Note n where n.cFlag=? and n.cVouchType=N'50' and ";

        const string ByCodeSql = "(n.cVouchID=? or n.Auto_ID=?)";

        // 按收款单找票据：票据的 iCloseID 就是收款单主键（分包票据的收款单票据号带子票区间后缀，按号找不到）。
        const string ByReceiptSql = "n.iCloseID=?";

        // 票据号在 AP_Note（U8 按 cVouchType + cVouchID 判重，不分应收应付）和收付款单的票据号上都不能已存在。
        // 事务里复查时只锁 AP_Note 上这个票据号的范围（{L}），不对 Ap_CloseBill 加范围锁，免得挡住别人新增收付款单。
        const string TakenSql = "select top 1 N'note' from AP_Note{L} where cVouchType=N'50' and cVouchID=? "
            + "union all select top 1 N'receipt' from Ap_CloseBill where cCoVouchType=N'50' and (cNoteNo=? or cCoVouchID=?)";

        const string HoldHint = " with (updlock, holdlock)";

        // 后缀部分恰好是「数字-数字」：只有一个 -，首尾是数字，没有别的字符。
        const string SuffixRest = "{R} like N'[0-9]%-%[0-9]' and {R} not like N'%-%-%' and {R} not like N'%[^0-9-]%'";

        // 与 SuffixTakenSql 的 top 201 配套：读到 201 行说明候选超过上限。
        const int SuffixCap = 200;

        // 收款单票据号带子票区间后缀的（「票据号-起-止」，U8 登记分包票据的写法）：SQL 里按前缀 LIKE，且前缀之后（ra / rb，
        // 从第 ? 个字符起）恰好是「数字-数字」；再由 NoteSplit.TrySuffix 按区分大小写、位数精确判断。按 iID 排序，候选超过上限时拒绝。
        static readonly string SuffixTakenSql = "select top 201 t.a, t.b from (select iID, isnull(cNoteNo,N'') as a, "
            + "isnull(cCoVouchID,N'') as b, substring(isnull(cNoteNo,N''), ?, 4000) as ra, substring(isnull(cCoVouchID,N''), ?, 4000) as rb "
            + "from Ap_CloseBill where cCoVouchType=N'50' and (cNoteNo like ? escape N'!' or cCoVouchID like ? escape N'!')) t "
            + "where (t.a like ? escape N'!' and " + SuffixRest.Replace("{R}", "t.ra") + ") or (t.b like ? escape N'!' and "
            + SuffixRest.Replace("{R}", "t.rb") + ") order by t.iID";

        // 分包票据登记时的可用子票区间：一行整段（同 U8 登记）。
        const string AvailInsertSql = "insert into Ap_Note_AvailRange (cNoteLink, cavailstart, cavailend, iavailamount, "
            + "iavailamount_local) values (?,convert(bigint, ?), convert(bigint, ?), convert(money, ?), convert(money, ?))";

        const string DropAvailSql = "delete from Ap_Note_AvailRange where cNoteLink=?";

        // 刚写入的票据：同一票据号在 AP_Note 只能有这一行。
        const string NewNoteSql = "select convert(varchar(20), max(Auto_ID)) as id, convert(varchar(10), count(*)) as n "
            + "from AP_Note where cVouchType=N'50' and cVouchID=?";

        // 票据卡片模板（iVT_ID）：本系统当期票据最常用的一个，没有就用卡片 RP02 的缺省模板；都要在模板表里存在。
        const string VtIdSql = "select top 1 convert(varchar(20), x.id) from (select n.iVT_ID as id, count(*) as cnt from AP_Note n "
            + "where n.cFlag=? and isnull(n.bStartFlag,0)=0 and n.iVT_ID is not null group by n.iVT_ID "
            + "union all select v.DEF_ID, 0 from vouchers v where v.CardNumber=N'RP02') x "
            + "where exists (select 1 from vouchertemplates_base t where t.VT_ID=x.id) order by x.cnt desc";

        // 应收（应付）基本科目：pjkm 票据科目、kzkm 客户（供应商）控制科目；同一年度按币种名优先。应收取 cArCode，应付取 cApCode。
        const string KmSqlAr = "select top 1 isnull(cArCode,N'') from Ap_InputCode where cNote_f=? and iyear=? "
            + "and isnull(cArCode,N'')<>N'' order by case when cArCodeName=? then 0 else 1 end";

        const string KmSqlAp = "select top 1 isnull(cApCode,N'') from Ap_InputCode where cNote_f=? and iyear=? "
            + "and isnull(cApCode,N'')<>N'' order by case when cApCodeName=? then 0 else 1 end";

        // 登记生成的收款单（应付票据是付款单）标成票据来源（同 U8：cSrcFlag='C'、cCoVouchType='50'、来源号和票据号都是票据号，
        // 分包票据是「票据号-起-止」），表头科目 = 票据科目。单据类型 48 / 49 由 flag 定（CloseType）。
        // 票据科目是受控科目，组件保存时表头带它会被拒（「此处不应为受控科目」），U8 界面的登记不受这条限制，所以保存后补上。
        const string MarkSql = "update Ap_CloseBill set cSrcFlag=N'C', cCoVouchType=N'50', cCoVouchID=?, cNoteNo=?, cCode=? "
            + "where iID=? and cVouchType=? and cFlag=?";

        // 删除票据前去掉收付款单的票据来源标记（与 MarkSql 相反，cSrcFlag 回到手工录入的空值），之后由组件删除。
        const string UntieSql = "update Ap_CloseBill set cSrcFlag=NULL, cCoVouchType=NULL, cCoVouchID=NULL, cNoteNo=NULL "
            + "where iID=? and cVouchType=? and cSrcFlag=N'C' and cCoVouchType=N'50'";

        // 登记生成的收付款单类型：应收票据收款单 48，应付票据付款单 49。
        public static string CloseType(string flag)
        {
            return flag == "AP" ? "49" : "48";
        }

        public static void Untie(object conn, string flag, int receiptId)
        {
            GlSql.Exec(conn, UntieSql, new object[] { receiptId, CloseType(flag) });
        }

        // 票据回写收款单主键（同 U8 登记的最后一步）。
        const string TieSql = "update AP_Note set iCloseID=? where Auto_ID=? and isnull(iCloseID,0)=0";

        const string CoIdExpr = "case when isnull(n.bsubpackage,0)<>0 then n.cVouchID+N'-'+convert(nvarchar(20), n.csubnostart)"
            + "+N'-'+convert(nvarchar(20), n.csubnoend) else n.cVouchID end";

        const string TiedSql = "select convert(varchar(10), count(*)) from AP_Note n inner join Ap_CloseBill c on c.iID=n.iCloseID "
            + "where n.Auto_ID=? and c.cNoteNo=" + CoIdExpr + " and c.cCoVouchID=c.cNoteNo and c.cSrcFlag=N'C' "
            + "and c.cCoVouchType=N'50' and isnull(n.cCode,N'')<>N''";

        // 删除票据时同 U8 的 DeletePJ：先锁住并确认票据没被改过，删收款单之后删保证金、付款申请明细和票据本身。
        // 删票据带上全部前提（非期初、没换票、余额等于票面、没有处理记录、收款单关联未变），删不到就回滚。
        static readonly string HoldSql = "select convert(varchar(10), count(*)) from AP_Note" + HoldHint + " where Auto_ID=? and "
            + string.Format(Ufts4, "AP_Note") + "=?";

        const string DropDepositSql = "delete from AP_SecurityDeposit where cLink=?";

        const string DropPayDetailSql = "delete from AP_PayDetail where cNote=?";

        const string DropNoteSql = "delete from AP_Note where Auto_ID=? and cLink=? and isnull(bStartFlag,0)=0 "
            + "and isnull(iChangeType,0)=0 and iRAmount=iAmount and isnull(iCloseID,0) in (0, ?) "
            + "and not exists (select 1 from AP_Note_Sub s where s.cLink=AP_Note.cLink)";

        const string ReceiptSql = "select convert(varchar(20), iID) as id, cVouchID as code from Ap_CloseBill where iID=? "
            + "and cVouchType=?";

        const string OtherSql = "select top 1 isnull(cother,N'') from code where iyear=? and ccode=?";

        const string LeftSql = "select convert(varchar(10), count(*)) from AP_Note where Auto_ID=?";

        // 票据的收款单（按 iCloseID 精确，补零写法也算）；非分包票据另查票据号相同、票据同侧（应收 AR 48 / 应付 AP 49）的收付款单。
        // 分包票据不按「号-起-止」查，免得把另一张名为「号-起-止」的非分包票据的收款单算进来；限定同侧，免得把应收票据背书冲应付
        // 生成的付款单（49，cCoVouchType 50、cNoteNo 也是票据号）算进来。
        const string LeftReceiptSql = "select convert(varchar(10), count(*)) from Ap_CloseBill where iID=? "
            + "or (?=0 and cCoVouchType=N'50' and cNoteNo=? and cFlag=? and cVouchType=?)";

        const string LeftAvailSql = "select convert(varchar(10), count(*)) from Ap_Note_AvailRange where cNoteLink=?";

        public static NoteRow Find(object conn, string flag, string code, int id)
        {
            return One(conn, ByCodeSql, new object[] { flag, code ?? "", id });
        }

        // 收款单（48）对应的票据：按 iCloseID 精确找；没有或不止一张返回 null。
        public static NoteRow ByReceipt(object conn, string flag, int receiptId)
        {
            return One(conn, ByReceiptSql, new object[] { flag, receiptId });
        }

        static NoteRow One(object conn, string where, object[] args)
        {
            string sql = NoteSql.Replace("{AMT}", WriteoffSql.Dec("n.iAmount", 2)).Replace("{REM}", WriteoffSql.Dec("n.iRAmount", 2))
                + where;
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, args, 2);
            if (rows.Count != 1)
            {
                return null;
            }
            Dictionary<string, object> row = rows[0];
            NoteRow n = new NoteRow();
            n.Id = CoRows.AsId(CoRows.Col(row, "id"));
            n.Code = CoRows.Col(row, "code");
            n.Link = CoRows.Col(row, "link");
            n.Ufts = CoRows.Col(row, "ufts");
            n.CloseId = CoRows.AsId(CoRows.Col(row, "close_id"));
            n.Km = CoRows.Col(row, "km").Trim();
            n.Partner = CoRows.Col(row, "partner").Trim();
            n.Dept = CoRows.Col(row, "dept").Trim();
            n.Person = CoRows.Col(row, "person").Trim();
            n.Opening = CoRows.Col(row, "opening") == "1";
            n.Change = CoRows.AsId(CoRows.Col(row, "change"));
            n.Amount = WriteoffSql.Num(CoRows.Col(row, "amount"));
            n.Remain = WriteoffSql.Num(CoRows.Col(row, "remain"));
            n.Subs = CoRows.AsId(CoRows.Col(row, "subs"));
            n.Split = CoRows.Col(row, "split") == "1";
            n.Start = Long(CoRows.Col(row, "sub_start"));
            n.End = Long(CoRows.Col(row, "sub_end"));
            return n;
        }

        static long Long(string text)
        {
            long value;
            return long.TryParse((text ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value) ? value : 0;
        }

        // locked 为 true 时在事务里加更新锁读（写入前的复查，挡住同时登记同一票据号的 U8 客户端）。
        // 收款单的票据号是本票据号或「本票据号-起-止」（分包）都算已被引用。
        public static string Taken(object conn, string code, bool locked)
        {
            string sql = TakenSql.Replace("{L}", locked ? HoldHint : "");
            string taken = Rows.Scalar(conn, sql, new object[] { code, code, code });
            if (taken != null)
            {
                return taken;
            }
            string like = NoteSplit.SuffixPattern(code);
            int from = code.Length + 2;
            List<Dictionary<string, object>> rows = Rows.Query(conn, SuffixTakenSql,
                new object[] { from, from, like, like, like, like }, SuffixCap + 1);
            if (rows.Count > SuffixCap)
            {
                throw new BridgeException(409, "state_mismatch", "以票据号 " + code + " 加子票区间为票据号的收付款单超过 "
                    + SuffixCap.ToString(CultureInfo.InvariantCulture) + " 张，无法核对是否重号，请在 U8 中核对");
            }
            long start;
            long end;
            foreach (Dictionary<string, object> row in rows)
            {
                if (NoteSplit.TrySuffix(CoRows.Col(row, "a"), code, out start, out end)
                    || NoteSplit.TrySuffix(CoRows.Col(row, "b"), code, out start, out end))
                {
                    return "receipt";
                }
            }
            return null;
        }

        public static string Km(object conn, string flag, string name, int year, string currency)
        {
            string km = Rows.Scalar(conn, flag == "AP" ? KmSqlAp : KmSqlAr, new object[] { name, year, currency ?? "" });
            return km == null ? "" : km.Trim();
        }

        // 科目的受控系统（code.cother，大写；没有该科目或不受控为空串）。
        public static string Controlled(object conn, int year, string code)
        {
            string other = Rows.Scalar(conn, OtherSql, new object[] { year, code ?? "" });
            return other == null ? "" : other.Trim().ToUpperInvariant();
        }

        // 0 表示没有可用的模板（iVT_ID 写空，同期初票据）。
        public static int VtId(object conn, string flag)
        {
            return CoRows.AsId(Rows.Scalar(conn, VtIdSql, new object[] { flag }));
        }

        // 事务里写入票据行（NoteInsert），返回新主键；同一票据号读到的不是恰好一行返回 0。
        public static int Insert(object conn, NoteRegPlan plan)
        {
            GlSql.Exec(conn, NoteInsert.Sql(plan), NoteInsert.Args(plan));
            NoteRegAsk ask = plan.Ask;
            if (ask.Split)
            {
                string money = ArapReq.Money(ask.Amount);
                GlSql.Exec(conn, AvailInsertSql, new object[]
                {
                    NoteInsert.Link(ask.Flag, ask.NoteNo), ask.SubStart.ToString(CultureInfo.InvariantCulture),
                    ask.SubEnd.ToString(CultureInfo.InvariantCulture), money, money
                });
            }
            Dictionary<string, object> row = Rows.One(conn, NewNoteSql, new object[] { ask.NoteNo });
            if (row == null || CoRows.Col(row, "n") != "1")
            {
                return 0;
            }
            return CoRows.AsId(CoRows.Col(row, "id"));
        }

        // 事务里：标来源、回写票据的 iCloseID，再核对两边对上、票据金额客户与请求一致。不符返回原因，符合返回空串。
        public static string Tie(object conn, NoteRegPlan plan, int noteId, int receiptId)
        {
            if (receiptId <= 0)
            {
                return "U8 没有返回新收付款单的主键";
            }
            string no = plan.Ask.NoteNo;
            string coId = NoteSplit.CoId(no, plan.Ask.SubStart, plan.Ask.SubEnd);
            GlSql.Exec(conn, MarkSql, new object[] { coId, coId, plan.NoteKm, receiptId, CloseType(plan.Ask.Flag), plan.Ask.Flag });
            GlSql.Exec(conn, TieSql, new object[] { receiptId, noteId });
            if (Rows.Scalar(conn, TiedSql, new object[] { noteId }) != "1")
            {
                return NotesReg.CloseTitle(plan.Ask.Flag) + "与票据的关联核对不符";
            }
            NoteRow note = Find(conn, plan.Ask.Flag, no, 0);
            if (!Matches(note, plan, noteId, receiptId))
            {
                return "写入的票据与请求核对不符";
            }
            string why = NoteSplit.RangeRefusal(NotesProcSql.Avail(conn, note.Link), note);
            return why.Length == 0 ? "" : "写入的票据 " + no + " " + why;
        }

        // 写入的票据：主键、收付款单、票面与余额、子票区间、往来单位、票据科目都与请求一致，且不是期初、没有处理记录。
        internal static bool Matches(NoteRow note, NoteRegPlan plan, int noteId, int receiptId)
        {
            if (note == null || note.Id != noteId || note.CloseId != receiptId || note.Opening || note.Subs != 0)
            {
                return false;
            }
            if (note.Amount != plan.Ask.Amount || note.Remain != plan.Ask.Amount || !NoteSplit.SameRange(note, plan.Ask))
            {
                return false;
            }
            return note.Partner == plan.Ask.Partner && note.Km == plan.NoteKm;
        }

        // 事务里锁住票据并确认 ufts 没变（读出快照之后没被改过）。
        public static bool Hold(object conn, NoteRow note)
        {
            return Rows.Scalar(conn, HoldSql, new object[] { note.Id, note.Ufts ?? "" }) == "1";
        }

        // 事务里（收款单已删）：带锁复查可用子票区间仍是登记时的样子（NoteSplit.RangeRefusal），删可用区间、保证金、付款申请明细和票据，
        // 再核对票据、它的可用区间和以它为来源的收款单都不在了。不符返回原因。
        public static string Remove(object conn, NoteRow note)
        {
            string why = NoteSplit.RangeRefusal(NotesProcSql.Avail(conn, note.Link), note);
            if (why.Length > 0)
            {
                return "票据 " + note.Code + " " + why + "，没有删除";
            }
            GlSql.Exec(conn, DropAvailSql, new object[] { note.Link });
            GlSql.Exec(conn, DropDepositSql, new object[] { note.Link });
            GlSql.Exec(conn, DropPayDetailSql, new object[] { note.Link });
            GlSql.Exec(conn, DropNoteSql, new object[] { note.Id, note.Link, note.CloseId });
            if (Rows.Scalar(conn, LeftSql, new object[] { note.Id }) != "0")
            {
                return "票据状态已变化（已处理、换票或收付款单关联改变），没有删除";
            }
            if (Rows.Scalar(conn, LeftAvailSql, new object[] { note.Link }) != "0")
            {
                return "票据的可用子票区间没有删干净，没有删除";
            }
            if (Rows.Scalar(conn, LeftReceiptSql, LeftReceiptArgs(note)) != "0")
            {
                return "仍有以该票据为来源的收付款单，没有删除";
            }
            return "";
        }

        internal static object[] LeftReceiptArgs(NoteRow note)
        {
            string flag = FlagOfLink(note.Link);
            return new object[] { note.CloseId, note.Split ? 1 : 0, note.Code, flag, CloseType(flag) };
        }

        // 票据的应收 / 应付：cLink 的前两位（AR50… / AP50…）。
        internal static string FlagOfLink(string link)
        {
            return link != null && link.StartsWith("AP", StringComparison.Ordinal) ? "AP" : "AR";
        }

        public static bool Tied(object conn, int noteId)
        {
            return Rows.Scalar(conn, TiedSql, new object[] { noteId }) == "1";
        }

        // 收付款单的单号；不存在返回 null。
        public static string ReceiptCode(object conn, string flag, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, ReceiptSql, new object[] { id, CloseType(flag) });
            return row == null ? null : CoRows.Col(row, "code");
        }

        // 新连接上核对：0 票据和以它为来源的收款单都没了，1 票据还在，2 票据没了但还有收款单，-1 读不到。
        public static int Left(WorkContext ctx, NoteRow note)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                if (Rows.Scalar(conn, LeftSql, new object[] { note.Id }) != "0")
                {
                    return 1;
                }
                return Rows.Scalar(conn, LeftReceiptSql, LeftReceiptArgs(note)) == "0" ? 0 : 2;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "notes left " + ex.Message);
                return -1;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }
    }
}
