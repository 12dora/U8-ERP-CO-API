using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户专管业务员的受控 SQL：一个事务里 UPDLOCK, ROWLOCK 读 Customer，校验 expect / 业务员 / 停用日期，
    // 按规则改 cCusPPerson（及合格的 cCusDepart），同步 Customer_Auth，写 cModifyPerson、dModifyDate，回读核对。
    internal static class ArcPersonSql
    {
        static readonly string LockSql = ArcCustomerRow.LockSql(
            "LTRIM(RTRIM(ISNULL(cCusPPerson,''))) AS person, LTRIM(RTRIM(ISNULL(cCusDepart,''))) AS dept");
        const string PersonSql = "SELECT LTRIM(RTRIM(ISNULL(cPersonCode,''))) AS code,"
            + " LTRIM(RTRIM(ISNULL(cDepCode,''))) AS dept,"
            + " CONVERT(varchar(10), dPInValidDate, 23) AS ended"
            + " FROM Person WHERE cPersonCode=?";
        const string SetPerson = "cCusPPerson=?";
        const string SetBoth = "cCusPPerson=?, cCusDepart=?";

        public static ApiResult Run(WorkContext ctx, ArcPersonAsk ask)
        {
            return ArcCustomerRow.Transact(ctx, "customer_person " + ask.Code,
                delegate(object conn) { return Apply(conn, ctx, ask); });
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
            Dictionary<string, object> row = ArcCustomerRow.Need(conn, LockSql, ask.Code);
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
            if (snap.Follow)
            {
                ArcCustomerRow.Update(conn, ctx, SetBoth, new object[] { ask.Person, snap.NewDept }, ask.Code, snap.Ts);
                ArcPersonAuth.Apply(conn, ask.Code, snap.NewDept);
                return;
            }
            ArcCustomerRow.Update(conn, ctx, SetPerson, new object[] { ask.Person }, ask.Code, snap.Ts);
        }

        static void Confirm(object conn, ArcPersonAsk ask, ArcPersonSnap snap)
        {
            Dictionary<string, object> row = ArcCustomerRow.Lock(conn, LockSql, ask.Code);
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
                throw ArcCustomerRow.Concurrent(ask.Code);
            }
            throw Unknown(ask.Code);
        }

        static string Cell(Dictionary<string, object> row, string name)
        {
            return ArcCustomerRow.Cell(row, name);
        }

        static bool Same(string a, string b)
        {
            return ArcCustomerRow.Same(a, b);
        }

        static BridgeException Unknown(string code)
        {
            return ArcCustomerRow.Unknown(code, "专管业务员");
        }
    }
}
