using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一张凭证的表头状态。GL_accvouch 没有表头表，这些列在每一行上重复。
    internal sealed class GlHead
    {
        public int Lines;
        public string Maker;
        public string Checker;
        public string AuditDate;
        public string Cashier;
        public bool Posted;
        public int Flag;
        public bool Mixed;
        public int Marks;
        public string OutSys;
        public string OutNo;
        public string Date;
    }

    internal static class GlState
    {
        internal const string KeyWhere = " WHERE iyear=? AND iperiod=? AND csign=? AND ino_id=?";

        // mixed=1 表示各行的作废、审核、出纳状态不一致（不该出现，出现就不写）。
        const string HeadCols = "SELECT COUNT(*) n, MAX(ISNULL(cbill,'')) maker, MAX(ISNULL(ccheck,'')) checker,"
            + " CONVERT(varchar(10), MAX(daudit_date), 23) audit_date, MAX(ISNULL(ccashier,'')) cashier,"
            + " MAX(ISNULL(ibook,0)) posted, MAX(ISNULL(iflag,0)) flag,"
            + " CASE WHEN MIN(ISNULL(iflag,0))<>MAX(ISNULL(iflag,0)) OR MIN(ISNULL(ccheck,''))<>MAX(ISNULL(ccheck,''))"
            + " OR MIN(ISNULL(ccashier,''))<>MAX(ISNULL(ccashier,'')) THEN 1 ELSE 0 END mixed,"
            + " MAX(ISNULL(iflagbank,0)) + MAX(ISNULL(iflagPerson,0)) marks, MAX(ISNULL(coutsysname,'')) outsys,"
            + " MAX(ISNULL(coutno_id,'')) outno, CONVERT(varchar(10), MIN(dbill_date), 23) d FROM GL_accvouch";

        public static GlHead Read(object conn, GlKey key, bool lockRows)
        {
            string sql = HeadCols + (lockRows ? " WITH (UPDLOCK, HOLDLOCK)" : "") + KeyWhere;
            Dictionary<string, object> row = Rows.One(conn, sql, GlSql.KeyArgs(key));
            if (row == null || GlSql.Int(row, "n") == 0)
            {
                return null;
            }
            GlHead head = new GlHead();
            head.Lines = GlSql.Int(row, "n");
            head.Maker = GlSql.Col(row, "maker");
            head.Checker = GlSql.Col(row, "checker");
            head.AuditDate = GlSql.Col(row, "audit_date");
            head.Cashier = GlSql.Col(row, "cashier");
            head.Posted = GlSql.Int(row, "posted") != 0;
            head.Flag = GlSql.Int(row, "flag");
            head.Mixed = GlSql.Int(row, "mixed") != 0;
            head.Marks = GlSql.Int(row, "marks");
            head.OutSys = GlSql.Col(row, "outsys");
            head.OutNo = GlSql.Col(row, "outno");
            head.Date = GlSql.Col(row, "d");
            return head;
        }

        public static GlHead Need(object conn, GlKey key, bool lockRows)
        {
            GlHead head = Read(conn, key, lockRows);
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "凭证不存在：" + key.Text());
            }
            return head;
        }

        public static int SignSeq(object conn, string sign)
        {
            string seq = Rows.Scalar(conn, "SELECT CONVERT(varchar(12), isignseq) s FROM dsign WHERE csign=?",
                new object[] { sign });
            int value;
            if (seq == null || !int.TryParse(seq.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
            {
                throw GlReq.Bad("凭证类别 " + sign + " 不存在");
            }
            return value;
        }

        public static void PeriodOpen(object conn, int year, int period)
        {
            string flag = Rows.Scalar(conn, "SELECT CONVERT(varchar(1), ISNULL(bflag,0)) f FROM GL_mend WHERE iyear=? AND iperiod=?",
                new object[] { year, period });
            if (flag == null)
            {
                throw Refuse("总账没有 " + year.ToString(CultureInfo.InvariantCulture) + " 年第 "
                    + period.ToString(CultureInfo.InvariantCulture) + " 期");
            }
            if (flag.Trim() != "0")
            {
                throw Refuse("该期间总账已结账");
            }
        }

        // U8 的凭证界面和 EAI 编辑凭证时在 GL_mvcontrol 里留锁。
        public static void NotLocked(object conn, GlKey key)
        {
            Dictionary<string, object> row = Rows.One(conn,
                "SELECT TOP 1 ISNULL(cuser,'') u FROM GL_mvcontrol WHERE (iyear=? OR iyear IS NULL) AND iperiod=? AND csign=? AND ino_id=?",
                GlSql.KeyArgs(key));
            if (row != null)
            {
                string user = GlSql.Col(row, "u");
                throw Refuse("凭证正被其他人编辑" + (user.Length > 0 ? "（" + user + "）" : ""));
            }
        }

        // 外部系统（应收、应付、存货核算等）生成的凭证只能在来源模块处理。
        public static void Manual(GlHead head)
        {
            if (head.OutSys.Length > 0 && !head.OutSys.Equals("GL", StringComparison.OrdinalIgnoreCase))
            {
                throw Refuse("凭证由 " + head.OutSys + " 系统生成，请在来源模块处理");
            }
        }

        // manualOnly：修改、作废、取消作废、删除只收总账自己做的凭证；审核、出纳签字也处理外部系统凭证（U8 月末照样审）。
        public static void Writable(object conn, GlKey key, GlHead head, bool manualOnly)
        {
            if (head.Mixed)
            {
                throw Refuse("凭证各行状态不一致，请在 U8 客户端处理");
            }
            if (head.Posted)
            {
                throw Refuse("凭证已记账");
            }
            if (manualOnly)
            {
                Manual(head);
            }
            PeriodOpen(conn, key.Year, key.Period);
            NotLocked(conn, key);
        }

        public static void NotReversed(object conn, GlHead head)
        {
            if (head.OutNo.Length == 0)
            {
                return;
            }
            if (Rows.Scalar(conn, "SELECT TOP 1 'x' x FROM GL_accvouch WHERE cblueoutno_id=?", new object[] { head.OutNo }) != null)
            {
                throw Refuse("凭证已被红字冲销");
            }
        }

        // 选项「允许修改、作废他人填制的凭证」（bProofModify）。
        public static void OwnOrAllowed(object conn, GlHead head, string user)
        {
            if (head.Maker.Length == 0 || head.Maker == user || Option(conn, "bProofModify", false))
            {
                return;
            }
            throw Refuse("总账选项「允许修改、作废他人填制的凭证」（bProofModify）未开启，不能处理他人填制的凭证（制单人 "
                + head.Maker + "）");
        }

        // U8 功能权限（只读 UFSYSTEM）：本人直接持有，或所属角色持有该功能或 admin。
        // 与读路由共用 PermLoad 的判断（请求年度或建账年度；账套主管按不晚于请求年度的最近 admin 年度），
        // 旧写法只认请求年度，这里是它的超集。凭证所在的会计年度不参与。写路由每次现读，不走权限缓存。
        public static void Permit(WorkContext ctx, string op)
        {
            string code;
            string name;
            AuthOf(op, out code, out name);
            int year;
            if (ctx.Item.Year == null || !int.TryParse(ctx.Item.Year.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out year))
            {
                throw GlReq.Bad("年度无效");
            }
            PermCheck.Require(ctx, code, name);
        }

        static void AuthOf(string op, out string code, out string name)
        {
            switch (op)
            {
                case "delete":
                    code = "GL0202";
                    name = "凭证整理";
                    return;
                case "sign":
                case "unsign":
                    code = "GL0203";
                    name = "出纳签字";
                    return;
                case "verify":
                case "unverify":
                    code = "GL0204";
                    name = "审核凭证";
                    return;
                default:
                    code = "GL0201";
                    name = "填制凭证";
                    return;
            }
        }

        public static bool HasCashLine(object conn, GlKey key)
        {
            string sql = "SELECT TOP 1 'x' x FROM GL_accvouch v JOIN code c ON c.iyear=v.iyear AND c.ccode=v.ccode"
                + " WHERE v.iyear=? AND v.iperiod=? AND v.csign=? AND v.ino_id=? AND (c.bcash=1 OR c.bbank=1)";
            return Rows.Scalar(conn, sql, GlSql.KeyArgs(key)) != null;
        }

        // 总账选项在 AccInformation（cSysID='GL'）；缺行时用调用方给的保守值。
        public static bool Option(object conn, string name, bool fallback)
        {
            string value = Rows.Scalar(conn, "SELECT cValue FROM AccInformation WHERE cSysID='GL' AND cName=?", new object[] { name });
            if (value == null || value.Trim().Length == 0)
            {
                return fallback;
            }
            string text = value.Trim();
            return text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
        }

        public static string Operator(WorkContext ctx)
        {
            string name = ctx.OperatorName;
            if (name == null || name.Trim().Length == 0)
            {
                throw new BridgeException(500, "internal", "登录对象没有操作员姓名");
            }
            return name.Trim();
        }

        public static string LoginDate(WorkContext ctx)
        {
            string date = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (date.Length == 0)
            {
                throw GlReq.Bad("缺少登录日期 date");
            }
            return date;
        }

        // 请求里的 year 是账套库的起始年度（UFDATA_<账套号>_<起始年度>），会计年度取登录日期的年份。
        public static int LoginYear(WorkContext ctx)
        {
            DateTime day;
            string text = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw GlReq.Bad("登录日期无效");
            }
            return day.Year;
        }

        public static Dictionary<string, object> StateOf(GlHead head)
        {
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = head.Checker.Length > 0;
            state["checker"] = head.Checker;
            state["audit_date"] = head.AuditDate;
            state["signed"] = head.Cashier.Length > 0;
            state["cashier"] = head.Cashier;
            state["posted"] = head.Posted;
            state["void"] = head.Flag == 1;
            state["error"] = head.Flag == 2;
            return state;
        }

        public static BridgeException Refuse(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
