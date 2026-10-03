using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 客户银行账户（customer_bank，CustomerBank）、供应商银行账户（vendor_bank，VendorBank）：一次一行。
    // 表主键是（客户或供应商编码, cAccountNum），编码写成 "<客户编码>:<银行账号>"。
    // 默认账户规则同 U8 界面（实测）：每个客户至多一个 bDefault=1；
    // 把一个账户设为默认时其余账户清成 0；第一个账户必须是默认；默认账户不能取消默认，也不能在还有其他账户时删除
    // （先把另一个账户设为默认）。之后上级档案的开户银行、银行账号、所属银行编码跟默认账户走。
    // 单据（收付款单 cBankAccount、发票 ccusaccount 等）里的账号是文字副本，不引用本表，删除不查单据。
    // 写法只有受控 SQL（ArcPartnerBankSql）：一个事务里按上面的规则改本表并同步上级三列。
    // EAI 对上级客户 diffedit 附账户子节点时 U8 按整条客户档案校验（「所属行业是默认值，不可为空！」），
    // 供应商回读不符；EAI 改账户时还会整批删了重插，丢其他账户的列。所以不走 EAI。
    internal static class ArcPartnerBank
    {
        const int MaxRows = 500;

        // 可写标签与列长度（字符）。列名随客户 cCus… / 供应商 cVen…，见 ArcPartner.Bank。
        static readonly string[] Limits = new string[]
        {
            "branch", "100", "bank_code", "5", "account_name", "60", "province", "20", "city", "20",
            "cbb_dep_id", "60", "branch_id", "60", "branch_id_sec", "5"
        };

        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            Check(ctx.Conn, req);
            return ArcPartnerBankSql.Run(ctx, req);
        }

        // 字段：长度；default 是布尔；新增必须给 branch（开户银行）；bank_code 给了就必须在银行档案（AA_Bank）里。
        static void Check(object conn, ArcReq req)
        {
            for (int i = 0; i + 1 < Limits.Length; i += 2)
            {
                ArcPartnerRun.Text(req, Limits[i], int.Parse(Limits[i + 1], CultureInfo.InvariantCulture));
            }
            string flag = req.Fields.Get("default");
            if (flag != null && flag != "0" && flag != "1")
            {
                throw ArcReq.Bad("字段 default 必须是布尔或 0 / 1", "fields.default");
            }
            CheckBranch(req);
            string bank = req.Fields.Get("bank_code");
            if (!string.IsNullOrEmpty(bank) && Rows.Scalar(conn, "SELECT cBankCode FROM AA_Bank WHERE cBankCode=?", new object[] { bank }) == null)
            {
                throw ArcReq.Bad("所属银行 " + bank + " 不在银行档案里", "fields.bank_code");
            }
        }

        static void CheckBranch(ArcReq req)
        {
            string branch = req.Fields.Get("branch");
            if (req.Op == "create" && (branch == null || branch.Trim().Length == 0))
            {
                throw ArcReq.Bad("缺少字段 branch（开户银行）", "fields.branch");
            }
            if (req.Op == "update" && branch != null && branch.Trim().Length == 0)
            {
                throw ArcReq.Bad("branch（开户银行）不能为空", "fields.branch");
            }
        }

        // 列名：cBank, cBranch, cAccountNum, cAccountName, bDefault, 省、市、联行号三列。
        internal static string[] Columns(PartnerSide side)
        {
            string p = side.Prefix;
            return new string[]
            {
                "cBank", "cBranch", "cAccountNum", "cAccountName", "bDefault",
                p + "Prinvince", p + "City", p + "CBBDepId", p + "BranchId", p + "BranchIdSec"
            };
        }

        // 该客户的全部账户，按 id 顺序。locked 时 UPDLOCK, HOLDLOCK（受控 SQL 的事务里）。
        internal static List<Dictionary<string, string>> Load(object conn, PartnerSide side, string partner, bool locked)
        {
            string sql = "SELECT " + string.Join(", ", Columns(side)) + " FROM " + side.BankTable
                + (locked ? " WITH (UPDLOCK, HOLDLOCK)" : "") + " WHERE " + side.CodeCol + "=? ORDER BY id";
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { partner }, MaxRows + 1);
            if (rows.Count > MaxRows)
            {
                throw ArcGuard.State(side.Label + " " + partner + " 的银行账户超过 " + MaxRows.ToString(CultureInfo.InvariantCulture) + " 个，请在 U8 客户端处理");
            }
            List<Dictionary<string, string>> list = new List<Dictionary<string, string>>();
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, string> row = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (KeyValuePair<string, object> cell in rows[i])
                {
                    row[cell.Key] = cell.Value as string;
                }
                row["bDefault"] = row.ContainsKey("bDefault") && row["bDefault"] == "1" ? "1" : "0";
                list.Add(row);
            }
            return list;
        }

        // 按默认账户规则算出写入后该客户应有的全部账户（BankPlan.After）。
        internal static BankPlan Plan(object conn, ArcReq req, bool locked)
        {
            BankPlan plan = Start(req);
            plan.Before = Load(conn, plan.Side, plan.Partner, locked);
            Decide(plan, req);
            return plan;
        }

        internal static BankPlan Start(ArcReq req)
        {
            BankPlan plan = new BankPlan();
            plan.Side = ArcPartner.SideOf(req.Kind.Name);
            string[] parts = ArcPartnerRun.Parts(req);
            plan.Partner = parts[0];
            plan.Account = parts[1];
            return plan;
        }

        // 纯逻辑（自检也调）：按 Before 和请求算 After、Target。
        internal static void Decide(BankPlan plan, ArcReq req)
        {
            int at = Find(plan.Before, plan.Account);
            if (req.Op == "create")
            {
                Add(plan, req, at);
            }
            else if (at < 0)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            else if (req.Op == "update")
            {
                Change(plan, req, at);
            }
            else
            {
                Remove(plan, at);
            }
        }

        static void Add(BankPlan plan, ArcReq req, int at)
        {
            if (at >= 0)
            {
                throw ArcGuard.State("档案编码已存在：" + req.Code);
            }
            string flag = req.Fields.Get("default");
            if (plan.Before.Count == 0 && flag == "0")
            {
                throw ArcGuard.State(plan.Side.Label + " " + plan.Partner + " 还没有银行账户，第一个账户必须是默认账户");
            }
            Dictionary<string, string> row = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            row["cAccountNum"] = plan.Account;
            row["bDefault"] = flag ?? (plan.Before.Count == 0 ? "1" : "0");
            Apply(req, row);
            plan.Target = row;
            plan.After = Copy(plan.Before);
            plan.After.Add(row);
            Settle(plan);
        }

        static void Change(BankPlan plan, ArcReq req, int at)
        {
            plan.After = Copy(plan.Before);
            Dictionary<string, string> row = plan.After[at];
            if (row["bDefault"] == "1" && req.Fields.Get("default") == "0")
            {
                throw ArcGuard.State("默认账户不能取消默认：请把另一个账户设为默认");
            }
            Apply(req, row);
            plan.Target = row;
            Settle(plan);
        }

        static void Remove(BankPlan plan, int at)
        {
            if (plan.Before[at]["bDefault"] == "1" && plan.Before.Count > 1)
            {
                throw ArcGuard.State("默认账户不能删除：请先把另一个账户设为默认");
            }
            plan.After = Copy(plan.Before);
            plan.After.RemoveAt(at);
            plan.Deleted = true;
        }

        // 目标账户设为默认时，其余账户清成 0（U8：update … set bDefault=0 where … and cAccountNum<>…）。
        static void Settle(BankPlan plan)
        {
            if (plan.Target["bDefault"] != "1")
            {
                return;
            }
            for (int i = 0; i < plan.After.Count; i++)
            {
                if (!object.ReferenceEquals(plan.After[i], plan.Target))
                {
                    plan.After[i]["bDefault"] = "0";
                }
            }
        }

        static void Apply(ArcReq req, Dictionary<string, string> row)
        {
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                row[req.Map.Column(tags[i])] = req.Fields.Get(tags[i]);
            }
        }

        static List<Dictionary<string, string>> Copy(List<Dictionary<string, string>> rows)
        {
            List<Dictionary<string, string>> list = new List<Dictionary<string, string>>();
            for (int i = 0; i < rows.Count; i++)
            {
                list.Add(new Dictionary<string, string>(rows[i], StringComparer.OrdinalIgnoreCase));
            }
            return list;
        }

        internal static int Find(List<Dictionary<string, string>> rows, string account)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                string got;
                if (rows[i].TryGetValue("cAccountNum", out got) && string.Equals((got ?? "").TrimEnd(), account, StringComparison.OrdinalIgnoreCase))
                {
                    return i;
                }
            }
            return -1;
        }

        // 回读核对：该客户的每一个账户（全部列）都与 After 一致——目标行是写入的值，其他行只有默认标志可能被清成 0；
        // 多一行、少一行或任何一列不同都算不符（调用方 504）。
        internal static bool Matches(object conn, BankPlan plan)
        {
            List<Dictionary<string, string>> now = Load(conn, plan.Side, plan.Partner, false);
            if (now.Count != plan.After.Count)
            {
                return false;
            }
            for (int i = 0; i < plan.After.Count; i++)
            {
                int at = Find(now, plan.After[i]["cAccountNum"]);
                if (at < 0 || !SameCols(now[at], plan.After[i]))
                {
                    return false;
                }
            }
            return true;
        }

        // 两边出现过的每一列都比较（缺的列当空）：新增行里调用方没给的列在库里也应为空。
        static bool SameCols(Dictionary<string, string> got, Dictionary<string, string> want)
        {
            HashSet<string> cols = new HashSet<string>(got.Keys, StringComparer.OrdinalIgnoreCase);
            cols.UnionWith(want.Keys);
            foreach (string col in cols)
            {
                if (!string.Equals(Cell(got, col), Cell(want, col), StringComparison.Ordinal))
                {
                    return false;
                }
            }
            return true;
        }

        static string Cell(Dictionary<string, string> row, string col)
        {
            string value;
            row.TryGetValue(col, out value);
            return (value ?? "").Trim();
        }
    }

    // 一次写入前后该客户（供应商）的全部银行账户（列名 → 值，bDefault 为 "1" / "0"）。Target 是新增或修改后的目标行，删除时为 null。
    internal sealed class BankPlan
    {
        public PartnerSide Side;
        public string Partner;
        public string Account;
        public List<Dictionary<string, string>> Before;
        public List<Dictionary<string, string>> After;
        public Dictionary<string, string> Target;
        public bool Deleted;

        // 写入后的默认账户，没有返回 null。
        public Dictionary<string, string> DefaultRow()
        {
            for (int i = 0; i < After.Count; i++)
            {
                if (After[i]["bDefault"] == "1")
                {
                    return After[i];
                }
            }
            return null;
        }
    }
}
