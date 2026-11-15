using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 并账的闸门：在事务里、写之前。U8 并账界面查：并账金额不超过余额、
    // 金额合法、同币种、余额不为 0、期间未结账；桥另查单据存在且已应收（应付）审核、不受审批流控制、
    // 采购发票没有网络锁、并账日期不早于单据日期和系统启用日期、并入单位存在且未停用。
    // 单据或并入、并出单位不存在 404；line_id 不属于本单 400；审批流 409 workflow_enabled；其余 409 state_mismatch。
    // U8 的 LockVouch（Locked_By_Other）同核销不查不写（docs/limitations.md）。
    internal static class ArapMergeGate
    {
        public static MergePlan Plan(object conn, MergeAsk ask, string date, string acc, string user)
        {
            MergePlan plan = new MergePlan();
            plan.Flag = ask.Flag;
            plan.Date = date;
            plan.Digest = ask.Digest;
            plan.User = user;
            plan.From = ArapMergeSql.Partner(conn, ask.Flag, ask.From, null, "并出" + plan.Partner);
            plan.To = ArapMergeSql.Partner(conn, ask.Flag, ask.To, date, "并入" + plan.Partner);
            OpenDay(conn, plan, acc);
            string local = WriteoffSql.LocalCurrency(conn);
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                try
                {
                    Doc(conn, plan, ask.Lines[i], local);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Under(ex, FieldPath.Item("lines", i));
                }
            }
            if (plan.Lines.Count > ArapMergePlan.MaxLines)
            {
                throw ArapMergePlan.State("一次并账超过 500 行，请分批并账");
            }
            foreach (MergeLine line in plan.Lines)
            {
                line.ToBefore = ArapMergeSql.Balance(conn, plan.Flag, line, plan.To);
            }
            return plan;
        }

        // 并账日期（登录日期）不早于应收（应付）系统启用日期；所在会计期间按 UFSYSTEM..UA_Period 定，GL_mend 上该期未结账。
        static void OpenDay(object conn, MergePlan plan, string acc)
        {
            DateTime day = ArapWriteoff.Day(plan.Date);
            DateTime start;
            if (WriteoffSql.StartDate(conn, plan.Flag, out start) && day < start)
            {
                throw ArapMergePlan.State("并账日期早于" + plan.Side + "系统启用日期 "
                    + start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            }
            int[] period = WriteoffSql.PeriodOf(conn, acc, plan.Date);
            if (period == null)
            {
                throw ArapMergePlan.State("并账日期不在 U8 的会计期间内");
            }
            if (WriteoffSql.Closed(conn, plan.Flag, period[0], period[1]))
            {
                throw ArapMergePlan.State(plan.Side + "已结账");
            }
            plan.Year = period[0];
            plan.Period = period[1];
        }

        // 一张单据：表头（带锁）、状态、币种、行，然后按请求分配到行上。
        static void Doc(object conn, MergePlan plan, MergeAskLine ask, string local)
        {
            WriteoffKind kind = WriteoffKind.OfType(plan.Flag, ask.Type);
            Dictionary<string, object> head = kind == null ? null : Rows.One(conn, kind.CodeSql, new object[] { ask.Code, ask.Type });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", Title(ask.Type) + " " + ask.Code + " 不存在");
            }
            string code = CoRows.Col(head, "code").Trim();
            string what = kind.Title + " " + code;
            Check(conn, plan, kind, head, what);
            SameCurrency(plan, head, local, what);
            int id = CoRows.AsId(CoRows.Col(head, "id"));
            if (ask.Line > 0 && !WriteoffSql.LineOf(conn, kind, ask.Line, id))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在", "line_id");
            }
            List<MergeLine> open = ArapMergeSql.OpenLines(conn, plan.Flag, ask.Type, code, plan.From);
            foreach (MergeLine line in ArapMergePlan.Allocate(ask, open, what))
            {
                line.DocId = id;
                line.Kind = kind;
                plan.Lines.Add(line);
            }
            plan.Heads.Add(head);
        }

        // 已应收（应付）审核（表头审核人、往来明细上有审核行）、不受审批流控制、没有网络锁、日期不晚于并账日期。
        static void Check(object conn, MergePlan plan, WriteoffKind kind, Dictionary<string, object> head, string what)
        {
            string vtype = CoRows.Col(head, "vtype");
            if (CoRows.Col(head, "auditor").Length == 0
                || WriteoffSql.Partner(conn, plan.Flag, vtype, CoRows.Col(head, "code").Trim()) == null)
            {
                throw ArapMergePlan.State(what + " 未" + plan.Side + "审核");
            }
            if (CoRows.FlagOf(head, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", what + " 受审批流控制，不能通过接口并账");
            }
            if (CoRows.FlagOf(head, "locked"))
            {
                throw ArapMergePlan.State(what + " 正被其他操作锁定");
            }
            string vdate = CoRows.Col(head, "vdate");
            if (vdate.Length > 0 && string.CompareOrdinal(vdate, plan.Date) > 0)
            {
                throw ArapMergePlan.State("并账日期早于" + what + " 的日期 " + vdate);
            }
        }

        // 一次并账的单据同一币种（U8 并账界面先选币种再列单据）；表头币种为空按本位币。
        static void SameCurrency(MergePlan plan, Dictionary<string, object> head, string local, string what)
        {
            string cur = CoRows.Col(head, "cur");
            cur = cur.Length == 0 ? local : cur;
            if (plan.Currency == null)
            {
                plan.Currency = cur;
                return;
            }
            if (!string.Equals(plan.Currency, cur, StringComparison.Ordinal))
            {
                throw ArapMergePlan.State(what + " 的币种与其他单据不一致，一次并账只能是同一币种");
            }
        }

        static string Title(string type)
        {
            switch (type)
            {
                case "26":
                case "27":
                    return "销售发票";
                case "01":
                case "02":
                    return "采购发票";
                case "R0":
                    return "应收单";
                default:
                    return "应付单";
            }
        }
    }
}
