using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户专管业务员的受控 SQL：一个事务里 UPDLOCK, ROWLOCK 读 Customer，校验 expect / 业务员 / 停用日期，
    // 按规则改 cCusPPerson（及合格的 cCusDepart），同步 Customer_Auth，写 cModifyPerson、dModifyDate，回读核对。
    internal static class ArcPersonSql
    {
        const string LockSql = "SELECT LTRIM(RTRIM(ISNULL(cCusPPerson,''))) AS person,"
            + " LTRIM(RTRIM(ISNULL(cCusDepart,''))) AS dept,"
            + " CONVERT(varchar(20), CONVERT(bigint, pubufts)) AS ts"
            + " FROM Customer WITH (UPDLOCK, ROWLOCK) WHERE cCusCode=?";
        const string PersonSql = "SELECT LTRIM(RTRIM(ISNULL(cPersonCode,''))) AS code,"
            + " LTRIM(RTRIM(ISNULL(cDepCode,''))) AS dept,"
            + " CONVERT(varchar(10), dPInValidDate, 23) AS ended"
            + " FROM Person WHERE cPersonCode=?";
        const string UpdatePerson = "UPDATE Customer SET cCusPPerson=?, cModifyPerson=?,"
            + " dModifyDate=CONVERT(datetime, CONVERT(date, ?, 23))"
            + " WHERE cCusCode=? AND pubufts=CONVERT(binary(8), CONVERT(bigint, ?))";
        const string UpdateBoth = "UPDATE Customer SET cCusPPerson=?, cCusDepart=?, cModifyPerson=?,"
            + " dModifyDate=CONVERT(datetime, CONVERT(date, ?, 23))"
            + " WHERE cCusCode=? AND pubufts=CONVERT(binary(8), CONVERT(bigint, ?))";

        public static ApiResult Run(WorkContext ctx, ArcPersonAsk ask)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                ApiResult result = Apply(conn, ctx, ask);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.Commit(conn);
                open = false;
                CoRows.Note(ctx.Item, "customer_person " + ask.Code);
                return result;
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        static ApiResult Apply(object conn, WorkContext ctx, ArcPersonAsk ask)
        {
            ArcPersonSnap snap = Load(conn, ask, ctx.Item.Date);
            Gate(ask, snap);
            if (DryRun.Active)
            {
                DryRun.StopWith(ctx, ArcPerson.DryBody(ask, snap));
            }
            if (Same(snap.Person, ask.Person))
            {
                return ApiResult.Ok(ArcPerson.OkBody(ask, snap, false));
            }
            Write(conn, ctx, ask, snap);
            Confirm(conn, ask, snap);
            return ApiResult.Ok(ArcPerson.OkBody(ask, snap, true));
        }

        static ArcPersonSnap Load(object conn, ArcPersonAsk ask, string date)
        {
            Dictionary<string, object> row = Rows.One(conn, LockSql, new object[] { ask.Code });
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            ArcPersonSnap snap = new ArcPersonSnap();
            snap.Person = Cell(row, "person");
            snap.Dept = Cell(row, "dept");
            snap.Ts = Cell(row, "ts");
            NeedPerson(conn, ask.Person, date, snap);
            if (snap.PersonCode.Length > 0)
            {
                ask.Person = snap.PersonCode;
            }
            snap.OldDept = OldDept(conn, snap.Person);
            string note = "";
            if (ArcPerson.FollowDept(snap.Dept, snap.OldDept, snap.NewDept) && !Same(snap.Dept, snap.NewDept))
            {
                note = ArcPersonAuth.RejectNote(conn, snap.NewDept, date);
            }
            ArcPerson.DecideFollow(snap, note);
            return snap;
        }

        static void NeedPerson(object conn, string code, string date, ArcPersonSnap snap)
        {
            Dictionary<string, object> row = Rows.One(conn, PersonSql, new object[] { code });
            if (row == null)
            {
                throw new BridgeException(409, "u8_rejected", "业务员不存在：" + code);
            }
            snap.NewDept = Cell(row, "dept");
            snap.PersonCode = Cell(row, "code");  // 按 U8 人员档案的写法写入（查找不区分大小写）
            string ended = Cell(row, "ended");
            if (date != null && date.Length > 0 && ended.Length > 0
                && string.CompareOrdinal(ended, date) <= 0)
            {
                throw new BridgeException(409, "u8_rejected", "业务员已停用：" + code);
            }
        }

        static string OldDept(object conn, string person)
        {
            if (person == null || person.Length == 0)
            {
                return "";
            }
            Dictionary<string, object> row = Rows.One(conn, PersonSql, new object[] { person });
            return row == null ? "" : Cell(row, "dept");
        }

        static void Gate(ArcPersonAsk ask, ArcPersonSnap snap)
        {
            if (!ask.HasExpect)
            {
                return;
            }
            if (Same(ask.Expect, snap.Person))
            {
                return;
            }
            string current = snap.Person.Length == 0 ? "空" : snap.Person;
            throw new BridgeException(409, "state_mismatch", "当前专管业务员是" + current);
        }

        static void Write(object conn, WorkContext ctx, ArcPersonAsk ask, ArcPersonSnap snap)
        {
            string who = Modifier(ctx);
            string date = ctx.Item.Date;
            if (snap.Follow)
            {
                GlSql.Exec(conn, UpdateBoth, new object[]
                {
                    ask.Person, snap.NewDept, who, date, ask.Code, snap.Ts
                });
                ArcPersonAuth.Apply(conn, ask.Code, snap.NewDept);
                return;
            }
            GlSql.Exec(conn, UpdatePerson, new object[] { ask.Person, who, date, ask.Code, snap.Ts });
        }

        static void Confirm(object conn, ArcPersonAsk ask, ArcPersonSnap snap)
        {
            Dictionary<string, object> row = Rows.One(conn, LockSql, new object[] { ask.Code });
            if (row == null)
            {
                throw Unknown(ask.Code);
            }
            string person = Cell(row, "person");
            string dept = Cell(row, "dept");
            string wantDept = snap.Follow ? snap.NewDept : snap.Dept;
            if (Same(person, ask.Person) && Same(dept, wantDept))
            {
                if (snap.Follow && !ArcPersonAuth.Matches(conn, ask.Code, snap.NewDept))
                {
                    throw Unknown(ask.Code);
                }
                return;
            }
            if (Cell(row, "ts") == snap.Ts)
            {
                throw new BridgeException(409, "state_mismatch", "客户 " + ask.Code + " 已被他人修改");
            }
            throw Unknown(ask.Code);
        }

        static string Modifier(WorkContext ctx)
        {
            string name = ctx.OperatorName;
            if (name != null && name.Trim().Length > 0)
            {
                return name.Trim();
            }
            string code = ctx.Item == null ? null : ctx.Item.Operator;
            return code ?? "";
        }

        static string Cell(Dictionary<string, object> row, string name)
        {
            return (GlSql.Col(row, name) ?? "").Trim();
        }

        static bool Same(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        static BridgeException Unknown(string code)
        {
            return new BridgeException(504, "outcome_unknown",
                "客户 " + code + " 已写入专管业务员，但回读与写入不一致，结果未知");
        }
    }
}
