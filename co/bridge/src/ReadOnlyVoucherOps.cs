using System;
using System.Collections.Generic;

namespace U8Co
{
    // 只读例外 type=vouchers 的可开放 (op, 单据类型) 登记表，与 API readonly_voucher_ops.VOUCHER_OPS 同一份内容。
    // 一行一个 op：路径恰为 /u8co/v1/vouchers/<op>；Kinds 是配置项 voucher 可列的单据类型（请求体 type 须等于规则的
    // voucher，区分大小写）；ClassOps 是该路径按 action 映射后的写入分类 op（WriteClass），不在里面的 action 不放行
    // （verify 覆盖审核与弃审，close 只覆盖关闭、不连带放行打开）；Fixed 为 true 时只有一种单据类型，配置可省 voucher；
    // Source 不为 null 时生单来源 source_type 须是它（省略 source_type 即缺省来源，见 Kinds.GenerateFrom）。
    // 新增一行就是开放一种 (op, 单据类型)；登记表之外的 op 配置加载即报错。
    internal static class ReadOnlyVoucherOps
    {
        internal const string Root = "/u8co/v1/vouchers/";
        const string SaleOrder = "sale_order";
        const string Dispatch = "dispatch";
        const string SourceField = "source_type";

        internal sealed class Row
        {
            public string Op;
            public string[] Kinds;
            public string[] ClassOps;
            public bool Fixed;
            public string Source;
        }

        internal static readonly Row[] Rows = new Row[]
        {
            // 历史发货单表头修正、销售订单合同号回写：单据类型固定，voucher 可省。
            Make(VoucherHeadFix.Op, new string[] { VoucherHeadFix.Kind }, new string[] { VoucherHeadFix.Op }, true, null),
            Make(VoucherSoContract.Op, new string[] { VoucherSoContract.Kind }, new string[] { VoucherSoContract.Op }, true, null),
            Make("create", new string[] { SaleOrder }, new string[] { "create" }, false, null),
            Make("update", new string[] { SaleOrder }, new string[] { "update" }, false, null),
            Make("close", new string[] { SaleOrder }, new string[] { "close" }, false, null),
            Make("verify", new string[] { SaleOrder, Dispatch }, new string[] { "verify", "unverify" }, false, null),
            // 发货单参照销售订单生单。
            Make("generate", new string[] { Dispatch }, new string[] { "generate" }, false, SaleOrder),
        };

        static Row Make(string op, string[] kinds, string[] classOps, bool fixedKind, string source)
        {
            Row row = new Row();
            row.Op = op;
            row.Kinds = kinds;
            row.ClassOps = classOps;
            row.Fixed = fixedKind;
            row.Source = source;
            return row;
        }

        // 登记表里的 op（配置校验和报错用，顺序同登记表）。
        internal static string[] Ops()
        {
            string[] ops = new string[Rows.Length];
            for (int i = 0; i < Rows.Length; i++)
            {
                ops[i] = Rows[i].Op;
            }
            return ops;
        }

        internal static Row Find(string op)
        {
            for (int i = 0; i < Rows.Length; i++)
            {
                if (string.Equals(Rows[i].Op, op, StringComparison.Ordinal))
                {
                    return Rows[i];
                }
            }
            return null;
        }

        // 规则实际放行的单据类型：配置了就用配置的（须在该行里）；单据类型固定的 op 省略时取那一种。
        internal static string VoucherOf(string op, string voucher)
        {
            Row row = Find(op);
            if (row == null)
            {
                return null;
            }
            if (voucher != null)
            {
                return Array.IndexOf(row.Kinds, voucher) >= 0 ? voucher : null;
            }
            return row.Fixed ? row.Kinds[0] : null;
        }

        // 路径恰为 vouchers/<op>、请求体 type 等于规则的单据类型、写入分类 op 在该行里、生单来源对得上才放行。
        internal static bool Covers(ReadOnlyExceptionRule rule, string path, string type, string classOp,
            Dictionary<string, object> body)
        {
            Row row = rule == null ? null : Find(rule.Op);
            string kind = row == null ? null : VoucherOf(rule.Op, rule.Voucher);
            if (kind == null || rule.Archives != null || !string.Equals(path, Root + row.Op, StringComparison.Ordinal))
            {
                return false;
            }
            return string.Equals(type, kind, StringComparison.Ordinal) && Array.IndexOf(row.ClassOps, classOp) >= 0
                && SourceOk(row, body);
        }

        static bool SourceOk(Row row, Dictionary<string, object> body)
        {
            object raw;
            if (row.Source == null || body == null || !body.TryGetValue(SourceField, out raw))
            {
                return true;
            }
            return string.Equals(raw as string, row.Source, StringComparison.Ordinal);
        }
    }
}
