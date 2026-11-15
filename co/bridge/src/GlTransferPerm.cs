using System;
using System.Collections.Generic;

namespace U8Co
{
    // 结转的数据权限：口径同总账读取（凭证查询、辅助余额表）。科目（总账选项「明细账查询权限控制到科目」打开时）、
    // 部门、人员、客户、供应商、项目的数据权限开着时，取数的每个科目、每一条取数余额和每一行分录（科目 + 辅助项）都要有查询权限，
    // 有一个不放行就 403 no_permission（失败即拒绝）。定义里的科目在取数之前查，余额和分录在算出之后、返回或校验之前查，
    // 预演同样；403 的消息不带金额。账套主管、该对象的数据权限管理员不受限（PermContext.Controls）。
    internal static class GlTransferPerm
    {
        const string Head = "没有结转所涉";

        // 定义里会取数或入账的科目（取数之前）。
        public static void Accounts(PermContext p, IEnumerable<string> codes)
        {
            PermContext perm = Need(p);
            foreach (string code in codes)
            {
                if (!perm.Allow(PermObj.Account, code))
                {
                    throw Deny("科目 " + code);
                }
            }
        }

        // 期间损益：每一对定义的损益科目和本年利润科目。
        public static void Pairs(PermContext p, List<string[]> pairs)
        {
            List<string> codes = new List<string>();
            foreach (string[] pair in pairs)
            {
                codes.Add(pair[2]);
                codes.Add(pair[3]);
            }
            Accounts(p, codes);
        }

        // 自定义转账：各行科目、QM 的科目和 QM 的辅助项参数。
        public static void Def(PermContext p, GlCustomDef def, GlTransferCodes codes)
        {
            List<string> list = new List<string>();
            foreach (GlTransferDef row in def.Rows)
            {
                list.Add(row.Code);
            }
            foreach (GlFormula f in def.Formulas)
            {
                foreach (GlQm qm in f.Atoms)
                {
                    list.Add(qm.Code);
                    string[] aux = new string[GlTransferBal.AuxCols.Length];
                    int kind = qm.Aux.Length > 0 ? GlTransferCustomCalc.Kind(codes, qm.Code) : -1;
                    if (kind >= 0)
                    {
                        aux[kind] = qm.Aux;
                        Check(p, qm.Code, aux, codes);
                    }
                }
            }
            Accounts(p, list);
        }

        // 取数的余额行（科目及其辅助项）。
        public static void Rows(PermContext p, List<GlTransferBalRow> rows, GlTransferCodes codes)
        {
            foreach (GlTransferBalRow row in rows)
            {
                Check(p, row.Code, row.Aux, codes);
            }
        }

        // 要生成的全部分录。
        public static void Lines(PermContext p, GlTransferPlan plan, GlTransferCodes codes)
        {
            foreach (GlTransferVoucher v in plan.Vouchers)
            {
                foreach (GlLine line in v.Draft.Lines)
                {
                    string[] aux = new string[]
                    {
                        line.Dept, line.Person, line.Customer, line.Supplier, line.ItemClass, line.Item
                    };
                    Check(p, line.Account, aux, codes);
                }
            }
        }

        // 一个科目 + 辅助项（下标同 GlTransferBal.AuxCols，空的不查）。
        internal static void Check(PermContext p, string account, string[] aux, GlTransferCodes codes)
        {
            string why = Problem(Need(p), account, aux, codes);
            if (why != null)
            {
                throw Deny(why);
            }
        }

        // 不放行时返回说明（对象和编码），放行返回 null。
        internal static string Problem(PermContext p, string account, string[] aux, GlTransferCodes codes)
        {
            if (!p.Allow(PermObj.Account, account))
            {
                return "科目 " + account;
            }
            string[] objs = new string[] { PermObj.Department, PermObj.Person, PermObj.Customer, PermObj.Vendor };
            string[] names = new string[] { "部门", "人员", "客户", "供应商" };
            for (int i = 0; i < objs.Length; i++)
            {
                string code = At(aux, i);
                if (code.Length > 0 && !p.Allow(objs[i], code))
                {
                    return names[i] + " " + code;
                }
            }
            string item = At(aux, 5);
            if (item.Length == 0)
            {
                return null;
            }
            string cls = At(aux, 4);
            if (cls.Length == 0)
            {
                cls = codes == null ? "" : codes.ItemClass(account);
            }
            return p.AllowItem(cls, item) ? null : "项目 " + cls + ":" + item;
        }

        static string At(string[] aux, int i)
        {
            if (aux == null || i >= aux.Length || aux[i] == null)
            {
                return "";
            }
            return aux[i].Trim();
        }

        // 没有权限快照时一律拒绝（不应发生：GlTransfer.Run 先取）。
        static PermContext Need(PermContext p)
        {
            if (p == null)
            {
                throw new BridgeException(500, "internal", "缺少权限快照");
            }
            return p;
        }

        static BridgeException Deny(string what)
        {
            return new BridgeException(403, "no_permission", Head + what + "的数据权限，不能结转（同总账查询的数据权限口径）");
        }
    }
}
