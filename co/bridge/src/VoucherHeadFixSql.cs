using System;
using System.Collections.Generic;

namespace U8Co
{
    // 历史发货单表头修正的受控 SQL：一个事务里 UPDLOCK, ROWLOCK 读发货单表头，校验 expect / 收货地址 / 业务员，
    // 收集下游并判断（VoucherHeadFixDown.Plan），预演到此回滚，否则 UPDATE 表头和下游，回读整组核对。
    // 表头只改 caddcode、cPersonCode，不动 cShipAddress、修改人、日期、审核人等其余列。
    internal static class VoucherHeadFixSql
    {
        const string HeadSql = "SELECT LTRIM(RTRIM(ISNULL(cDLCode,''))) AS code,"
            + " LTRIM(RTRIM(ISNULL(cCusCode,''))) AS cCusCode, LTRIM(RTRIM(ISNULL(cDepCode,''))) AS cDepCode,"
            + " LTRIM(RTRIM(ISNULL(cPersonCode,''))) AS cPersonCode, LTRIM(RTRIM(ISNULL(caddcode,''))) AS caddcode,"
            + " LTRIM(RTRIM(ISNULL(cSTCode,''))) AS cSTCode, LTRIM(RTRIM(ISNULL(cVerifier,''))) AS cVerifier, CONVERT(varchar(10), dDate, 23) AS ddate FROM DispatchList WITH (UPDLOCK, ROWLOCK) WHERE DLID=?";
        // 地址存在性检查加键范围锁，挡住并发删除这条地址（CusDeliverAdd 的删除走 customer_address_delete）。
        const string AddrSql = "SELECT LTRIM(RTRIM(cAddCode)) AS code FROM CusDeliverAdd WITH (UPDLOCK, HOLDLOCK, ROWLOCK)"
            + " WHERE cCusCode=? AND cAddCode=?";
        const string PersonSql = "SELECT LTRIM(RTRIM(ISNULL(cPersonCode,''))) AS code,"
            + " LTRIM(RTRIM(ISNULL(cDepCode,''))) AS dept, CONVERT(varchar(10), dPValidDate, 23) AS vfrom,"
            + " CONVERT(varchar(10), dPInValidDate, 23) AS vto FROM Person WHERE cPersonCode=?";

        public static ApiResult Run(WorkContext ctx, HeadFixAsk ask)
        {
            // 带 undo 是回滚模式：审计 detail 标 mode=rollback。
            string mode = ask.HasUndo ? " mode=rollback" : "";
            return ArcCustomerRow.Transact(ctx, "head_fix dispatch " + ask.Id.ToString() + mode,
                delegate(object conn) { return Apply(conn, ctx, ask); });
        }

        static ApiResult Apply(object conn, WorkContext ctx, HeadFixAsk ask)
        {
            Dictionary<string, object> head = Rows.One(conn, HeadSql, new object[] { ask.Id });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "发货单不存在");
            }
            VoucherHeadFix.Permit(ctx, head, ask);
            CheckAudited(head);
            string code = GlSql.Col(head, "code");
            Fill(ask, head);
            List<HeadFixField> changing = ask.Changing();
            if (changing.Count == 0)
            {
                // 回滚模式下表头已经是目标值而 undo 还列着行：状态不对，不能悄悄当成无事可做。
                HeadFixUndo.RefuseIdle(ask);
                // 没有要改的：预演也返回正常的预演响应（changed=false），不写。
                if (DryRun.Active)
                {
                    DryRun.StopWith(ctx, VoucherHeadFix.DryBody(ask, code, false, new List<HeadFixRow>()));
                }
                return ApiResult.Ok(VoucherHeadFix.Body(ask, code, false, new List<HeadFixRow>()));
            }
            Validate(conn, head, changing);
            // 正向调用要拒绝它自己的回滚会拒绝的修改（原值已不合法就改不回去）。
            if (!ask.HasUndo)
            {
                CheckReversible(conn, ctx, head, changing);
            }
            // 带 undo 是回滚模式：只动 undo 列出的行；否则按规则发现下游。
            List<HeadFixRow> plan = ask.HasUndo ? HeadFixDb.Plan(conn, ask, changing)
                : VoucherHeadFixDown.Plan(conn, ask.Id, changing);
            // 回滚请求最多带 500 个 id：写入会超过的，现在就拒绝，保证改得了就回滚得了。
            HeadFixUndo.CheckSize(plan);
            if (DryRun.Active)
            {
                DryRun.StopWith(ctx, VoucherHeadFix.DryBody(ask, code, true, plan));
            }
            Write(conn, ask, changing, plan);
            Confirm(conn, ask, changing, plan);
            return ApiResult.Ok(VoucherHeadFix.Body(ask, code, true, plan));
        }

        // 只修已审核的发货单（未审核的 U8 自己允许改）：审核人为空 → 409 not_audited。正向与回滚模式都先于其余校验。
        internal static void CheckAudited(Dictionary<string, object> head)
        {
            if (GlSql.Col(head, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "not_audited", "发货单未审核，请用 vouchers/update 修改");
            }
        }

        // 读到的当前值写进字段，并核对 expect（空串 = 当前为 NULL / 空）；不符 409，消息列出字段、期望值、实际值。
        static void Fill(HeadFixAsk ask, Dictionary<string, object> head)
        {
            List<string> bad = new List<string>();
            for (int i = 0; i < ask.Fields.Count; i++)
            {
                HeadFixField f = ask.Fields[i];
                f.Old = GlSql.Col(head, f.Column);
                if (!HeadFixPlan.Same(f.Old, f.Expect))
                {
                    bad.Add(f.Key + " 期望 " + Show(f.Expect) + "，实际 " + Show(f.Old));
                }
            }
            if (bad.Count > 0)
            {
                throw new BridgeException(409, "state_mismatch", "发货单表头与 expect 不符：" + string.Join("；", bad.ToArray()));
            }
        }

        static string Show(string value)
        {
            return value.Length == 0 ? "空" : value;
        }

        // 只校验会变的字段。收货地址 "" 是清空，不查；校验通过后把值换成档案里的写法（查找不分大小写）。
        // 当前业务员为空时不能改业务员：业务员不能写成空，改了就无法按 expect 回滚。
        static void Validate(object conn, Dictionary<string, object> head, List<HeadFixField> changing)
        {
            for (int i = 0; i < changing.Count; i++)
            {
                CheckPersonNotEmpty(changing[i]);
                if (changing[i].Key == VoucherHeadFix.AddrKey && changing[i].New.Length > 0)
                {
                    CheckAddr(conn, head, changing[i]);
                }
                else if (changing[i].Key == VoucherHeadFix.PersonKey)
                {
                    CheckPerson(conn, head, changing[i]);
                }
            }
        }

        // 业务员不能写成空，当前为空时改了就无法按 expect 回滚：409 person_empty。
        internal static void CheckPersonNotEmpty(HeadFixField f)
        {
            if (f.Key == VoucherHeadFix.PersonKey && f.Old.Length == 0)
            {
                throw new BridgeException(409, "person_empty", "发货单当前业务员为空，不能改业务员（改后无法回滚）");
            }
        }

        static void CheckAddr(object conn, Dictionary<string, object> head, HeadFixField f)
        {
            string customer = GlSql.Col(head, "cCusCode");
            Dictionary<string, object> row = Rows.One(conn, AddrSql, new object[] { customer, f.New });
            BridgeException fault = HeadFixRevert.AddrFault(row, customer, f.New);
            if (fault != null)
            {
                throw fault;
            }
            f.New = GlSql.Col(row, "code");
        }

        // 业务员必须存在、在发货日期有效、部门与发货单一致（部门不改）。
        static void CheckPerson(object conn, Dictionary<string, object> head, HeadFixField f)
        {
            Dictionary<string, object> row = Rows.One(conn, PersonSql, new object[] { f.New });
            BridgeException fault = HeadFixRevert.PersonFault(row, f.New, GlSql.Col(head, "ddate"), GlSql.Col(head, "cDepCode"));
            if (fault != null)
            {
                throw fault;
            }
            f.New = GlSql.Col(row, "code");
        }

        // 按回滚调用的规则校验改前的值：原收货地址（非空）仍在档案里；原业务员存在、有效、部门一致、操作员有数据权限（HeadFixRevert）。
        static void CheckReversible(object conn, WorkContext ctx, Dictionary<string, object> head, List<HeadFixField> changing)
        {
            string customer = GlSql.Col(head, "cCusCode");
            PermContext p = null;
            for (int i = 0; i < changing.Count; i++)
            {
                HeadFixField f = changing[i];
                bool addr = f.Key == VoucherHeadFix.AddrKey;
                Dictionary<string, object> row = null;
                if (addr && f.Old.Length > 0)
                {
                    row = Rows.One(conn, AddrSql, new object[] { customer, f.Old });
                }
                else if (!addr)
                {
                    row = Rows.One(conn, PersonSql, new object[] { f.Old });
                    p = PermCheck.Of(ctx);
                }
                BridgeException fault = HeadFixRevert.Original(f, row, customer, GlSql.Col(head, "ddate"),
                    GlSql.Col(head, "cDepCode"), p);
                if (fault != null)
                {
                    throw fault;
                }
            }
        }

        // 有效期列为空按有效；日期是 yyyy-MM-dd，按字符串比较。
        internal static bool ValidOn(string from, string to, string date)
        {
            if (date.Length == 0)
            {
                return true;
            }
            if (from.Length > 0 && string.CompareOrdinal(from, date) > 0)
            {
                return false;
            }
            return to.Length == 0 || string.CompareOrdinal(to, date) >= 0;
        }

        static void Write(object conn, HeadFixAsk ask, List<HeadFixField> changing, List<HeadFixRow> plan)
        {
            List<object> args = new List<object>();
            string set = HeadFixPlan.SetClause(changing, args);
            args.Add(ask.Id);
            GlSql.Exec(conn, "UPDATE DispatchList SET " + set + " WHERE DLID=?", args.ToArray());
            VoucherHeadFixDown.Write(conn, plan);
        }

        // 同一事务内重读表头和被改的下游，必须与计划完全一致，否则 504（结果未知，事务随之回滚）。
        static void Confirm(object conn, HeadFixAsk ask, List<HeadFixField> changing, List<HeadFixRow> plan)
        {
            Dictionary<string, object> head = Rows.One(conn, HeadSql, new object[] { ask.Id });
            bool ok = head != null;
            for (int i = 0; ok && i < changing.Count; i++)
            {
                ok = HeadFixPlan.Same(GlSql.Col(head, changing[i].Column), changing[i].New);
            }
            if (!ok || !VoucherHeadFixDown.Confirm(conn, plan))
            {
                throw new BridgeException(504, "outcome_unknown",
                    "发货单 " + ask.Id.ToString() + " 已写入，但回读与写入不一致，结果未知");
            }
        }
    }
}
