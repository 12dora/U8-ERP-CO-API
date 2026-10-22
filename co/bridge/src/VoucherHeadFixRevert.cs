using System;
using System.Collections.Generic;

namespace U8Co
{
    // 历史发货单表头修正的「改后能不能回滚」检查与新值校验共用的纯判断（不连库，自检覆盖）。
    // 正向调用（不带 undo）要拒绝它自己的回滚会拒绝的修改：改之前先按回滚调用的规则校验改前的值——
    // 原收货地址（非空时）仍在客户的地址档案里，原业务员存在、在发货日期有效、部门与发货单一致、操作员对他有数据权限；
    // 否则 409 not_reversible，消息写明原因。行数据来自 CusDeliverAdd / Person（null = 不存在）。
    internal static class HeadFixRevert
    {
        public const string Code = "not_reversible";

        // 收货地址：档案里没有这个编码 → 409 address_not_found。
        public static BridgeException AddrFault(Dictionary<string, object> row, string customer, string addr)
        {
            return row == null
                ? new BridgeException(409, "address_not_found", "客户 " + customer + " 没有收货地址 " + addr) : null;
        }

        // 业务员：必须存在、在发货日期有效（有效期列为空按有效）、部门等于发货单部门（部门不改）。
        public static BridgeException PersonFault(Dictionary<string, object> row, string code, string date, string want)
        {
            if (row == null)
            {
                return new BridgeException(409, "person_invalid", "业务员不存在：" + code);
            }
            if (!VoucherHeadFixSql.ValidOn(GlSql.Col(row, "vfrom"), GlSql.Col(row, "vto"), date))
            {
                return new BridgeException(409, "person_invalid", "业务员 " + code + " 在发货日期 " + date + " 不在有效期内");
            }
            string dept = GlSql.Col(row, "dept");
            if (!HeadFixPlan.Same(dept, want))
            {
                return new BridgeException(409, "person_department_mismatch",
                    "业务员 " + code + " 的部门 " + Show(dept) + " 与发货单部门 " + Show(want) + " 不一致");
            }
            return null;
        }

        // 操作员对业务员的数据权限（p 为空表示不控制）。
        public static BridgeException PermFault(PermContext p, string person)
        {
            return PermCheck.Allow(p, PermObj.Person, person)
                ? null : new BridgeException(403, "no_permission", PermCheck.DeniedRow);
        }

        // 一个字段的原值（改前的值）能不能回滚：row 是该字段对应的档案行（地址行或人员行，null = 不存在）；
        // 原收货地址为空不查；原业务员另查操作员的数据权限。能回滚返回 null，否则是 409 not_reversible。
        public static BridgeException Original(HeadFixField f, Dictionary<string, object> row, string customer,
            string date, string dept, PermContext p)
        {
            BridgeException fault;
            if (f.Key == VoucherHeadFix.AddrKey)
            {
                fault = f.Old.Length > 0 ? AddrFault(row, customer, f.Old) : null;
            }
            else
            {
                fault = PersonFault(row, f.Old, date, dept) ?? PermFault(p, f.Old);
            }
            return fault == null ? null : NotReversible(f.Key, f.Old, fault);
        }

        // 把原值的校验失败改写成 409 not_reversible：「改后无法回滚：<字段> 原值 <值>：<原因>」。
        public static BridgeException NotReversible(string key, string original, BridgeException fault)
        {
            return new BridgeException(409, Code, "改后无法回滚：" + key + " 原值 " + original + "：" + fault.Message);
        }

        static string Show(string value)
        {
            return (value ?? "").Length == 0 ? "空" : value;
        }
    }
}
