using System;
using System.Collections.Generic;

namespace U8Co
{
    // 银行账户的受控 SQL 写法（唯一写法，见 ArcPartnerBank）：一个事务里先 UPDLOCK, HOLDLOCK 锁住上级客户（供应商）行
    // 和它的全部账户，按 ArcPartnerBank.Plan 的规则只改目标这一行，目标设为默认时把其余账户清成 0，并照 U8 把上级档案的
    // 开户银行、银行账号、所属银行编码改成默认账户的值；删掉最后一个账户时，上级档案上的账号若就是它则清空这三列。
    // 提交后在新连接上回读核对，不符 504 outcome_unknown。未覆盖：U8 客户端删除最后一个账户时是否也清上级三列。
    internal static class ArcPartnerBankSql
    {
        public static ApiResult Run(WorkContext ctx, ArcReq req)
        {
            object conn = ctx.Conn;
            PartnerSide side = ArcPartner.SideOf(req.Kind.Name);
            BankPlan plan;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                LockPartner(conn, side, ArcPartnerRun.Parts(req)[0]);
                plan = ArcPartnerBank.Plan(conn, req, true);
                Apply(conn, plan);
                Sync(conn, plan);
                ArcDryRun.Set(req.Kind.Name, req.Code, req.Op, ArcDryRun.Row(conn, req.Kind, req.Code));
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.Commit(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
            CoRows.Note(ctx.Item, "银行账户 " + req.Op + " " + side.BankTable + " " + plan.Partner);
            if (ArcPartnerRun.Fresh(ctx, req, delegate(object fresh) { return ArcPartnerBank.Matches(fresh, plan); }))
            {
                return ArcPartnerRun.Done(req);
            }
            throw new BridgeException(504, "outcome_unknown", "银行账户 " + req.Code + " 已提交，但回读与写入不一致，结果未知，请先 get 核对，不要直接重发");
        }

        static void LockPartner(object conn, PartnerSide side, string partner)
        {
            string sql = "SELECT " + side.CodeCol + " FROM " + side.Table + " WITH (UPDLOCK, HOLDLOCK) WHERE " + side.CodeCol + "=?";
            if (Rows.One(conn, sql, new object[] { partner }) == null)
            {
                throw ArcReq.Bad(side.Label + " " + partner + " 不存在");
            }
        }

        static void Apply(object conn, BankPlan plan)
        {
            PartnerSide side = plan.Side;
            string key = " WHERE " + side.CodeCol + "=? AND cAccountNum=?";
            if (plan.Deleted)
            {
                GlSql.Exec(conn, "DELETE FROM " + side.BankTable + key, new object[] { plan.Partner, plan.Account });
                return;
            }
            string[] cols = Writable(side);
            List<object> args = new List<object>();
            if (Find(plan.Before, plan.Account))
            {
                string set = string.Join("=?, ", cols) + "=?";
                Values(args, cols, plan.Target);
                args.Add(plan.Partner);
                args.Add(plan.Account);
                GlSql.Exec(conn, "UPDATE " + side.BankTable + " SET " + set + key, args.ToArray());
            }
            else
            {
                args.Add(plan.Partner);
                args.Add(plan.Account);
                Values(args, cols, plan.Target);
                string marks = "?, ?";
                for (int i = 0; i < cols.Length; i++)
                {
                    marks += ", ?";
                }
                GlSql.Exec(conn, "INSERT INTO " + side.BankTable + " (" + side.CodeCol + ", cAccountNum, " + string.Join(", ", cols)
                    + ") VALUES (" + marks + ")", args.ToArray());
            }
            if (plan.Target["bDefault"] == "1")
            {
                GlSql.Exec(conn, "UPDATE " + side.BankTable + " SET bDefault=0 WHERE " + side.CodeCol + "=? AND cAccountNum<>? AND bDefault=1",
                    new object[] { plan.Partner, plan.Account });
            }
        }

        // 除两列主键外可写的列，bDefault 按整数传。
        static string[] Writable(PartnerSide side)
        {
            string[] all = ArcPartnerBank.Columns(side);
            List<string> cols = new List<string>();
            for (int i = 0; i < all.Length; i++)
            {
                if (all[i] != "cAccountNum")
                {
                    cols.Add(all[i]);
                }
            }
            return cols.ToArray();
        }

        static void Values(List<object> args, string[] cols, Dictionary<string, string> row)
        {
            for (int i = 0; i < cols.Length; i++)
            {
                string value;
                row.TryGetValue(cols[i], out value);
                if (cols[i] == "bDefault")
                {
                    args.Add(value == "1" ? 1 : 0);
                }
                else
                {
                    args.Add(string.IsNullOrEmpty(value) ? null : value);
                }
            }
        }

        static bool Find(List<Dictionary<string, string>> rows, string account)
        {
            return ArcPartnerBank.Find(rows, account) >= 0;
        }

        // 目标成了默认账户：上级三列改成它的值（U8：update Customer set cCusBank=…, cCusAccount=…, cCusBankCode=…）。
        // 删掉了最后一个账户：上级档案上的账号就是它时清空三列。
        static void Sync(object conn, BankPlan plan)
        {
            PartnerSide side = plan.Side;
            string set = "UPDATE " + side.Table + " SET " + side.BankCol + "=?, " + side.AccountCol + "=?, " + side.BankCodeCol + "=? WHERE "
                + side.CodeCol + "=?";
            if (!plan.Deleted && plan.Target["bDefault"] == "1")
            {
                GlSql.Exec(conn, set, new object[] { Text(plan.Target, "cBranch"), plan.Account, Text(plan.Target, "cBank"), plan.Partner });
                return;
            }
            if (plan.Deleted && plan.After.Count == 0)
            {
                GlSql.Exec(conn, set + " AND " + side.AccountCol + "=?", new object[] { null, null, null, plan.Partner, plan.Account });
            }
        }

        static string Text(Dictionary<string, string> row, string col)
        {
            string value;
            row.TryGetValue(col, out value);
            return string.IsNullOrEmpty(value) ? null : value;
        }
    }
}
