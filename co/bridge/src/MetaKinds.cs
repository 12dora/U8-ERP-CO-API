using System;
using System.Collections.Generic;

namespace U8Co
{
    // meta 的单据类型部分：Kinds 表 + 全局拦截名单 + 各操作的可写字段。
    internal static class MetaKinds
    {
        internal static List<object> Build()
        {
            List<object> list = new List<object>();
            foreach (VoucherKind kind in Kinds.All())
            {
                list.Add(One(kind));
            }
            return list;
        }

        static Dictionary<string, object> One(VoucherKind k)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["name"] = k.Name;
            d["title"] = Text(k.Title);
            d["family"] = Text(k.Family);
            d["sub_id"] = Text(k.SubId);
            d["verify_sub"] = Text(k.VerifySub);
            d["tables"] = Tables(k);
            d["ops"] = Ops(k);
            d["sources"] = k.Sources == null ? new string[0] : (string[])k.Sources.Clone();
            d["blocked"] = Blocked(k);
            d["writable"] = MetaWritable.Of(k);
            // 各操作的预演模式（DryRunModes）。
            d["dry_run"] = MetaDryRun.Of(k);
            return d;
        }

        static Dictionary<string, object> Tables(VoucherKind k)
        {
            Dictionary<string, object> t = new Dictionary<string, object>();
            t["head"] = Text(k.HeadTable);
            t["id"] = Text(k.IdColumn);
            t["code"] = Text(k.CodeColumn);
            t["body"] = Text(k.BodyTable);
            t["body_fk"] = Text(k.BodyFk);
            t["line_id"] = Text(k.LineIdColumn);
            t["verifier"] = Text(k.VerifierColumn);
            t["verify_date"] = Text(k.VerifyDateColumn);
            return t;
        }

        static Dictionary<string, object> Ops(VoucherKind k)
        {
            Dictionary<string, object> ops = new Dictionary<string, object>();
            ops["create"] = k.Creatable;
            ops["update"] = k.Updatable;
            ops["delete"] = k.Deletable;
            ops["verify"] = k.Verifiable;
            ops["close"] = k.Closable;
            ops["workflow"] = k.Workflow;
            ops["generate"] = k.Sources != null && k.Sources.Length > 0;
            ops["lock"] = VoucherLock.Lockable(k);
            // vouchers/verify 的 arap_verify / arap_unverify（ArapAudit）。
            ops["arap_verify"] = ArapAuditSpec.Supports(k);
            ops["arap_unverify"] = ArapAuditSpec.Supports(k);
            // arap/writeoff 的收付款单和被核销单据（ArapWriteoff）。
            ops["writeoff"] = ArapWriteoffReq.Handles(k);
            // arap/writeoff/cancel 取消核销，涉及的类型同核销（ArapUnwriteoff）。
            ops["writeoff_cancel"] = ArapWriteoffReq.Handles(k);
            // arap/writeoff/auto 自动核销，涉及的类型同核销（ArapAutoWriteoff）。
            ops["writeoff_auto"] = ArapWriteoffReq.Handles(k);
            // arap/merge 并账的单据（发票、应收单、应付单）（ArapMerge）。
            ops["arap_merge"] = ArapMergeReq.Handles(k);
            // arap/transfer 应收冲应付 / 应付冲应收的单据（发票、应收应付单、收付款单）（ArapTransfer）。
            ops["arap_transfer"] = ArapTransferReq.Handles(k);
            // arap/voucher 制单（取消制单按外部业务号，不分类型）（ArapVoucher）。
            ops["arap_voucher"] = ArapVoucherReq.Handles(k);
            return ops;
        }

        // 与 Json.Blocked 同一规则：全局名单 + 该类型的主键、单号、审核人、审核日期列（小写）。
        static string[] Blocked(VoucherKind k)
        {
            SortedSet<string> set = new SortedSet<string>(Json.BlockedList(), StringComparer.Ordinal);
            AddLower(set, k.IdColumn);
            AddLower(set, k.CodeColumn);
            AddLower(set, k.VerifierColumn);
            AddLower(set, k.VerifyDateColumn);
            string[] list = new string[set.Count];
            set.CopyTo(list);
            return list;
        }

        static void AddLower(SortedSet<string> set, string name)
        {
            if (!string.IsNullOrEmpty(name))
            {
                set.Add(name.ToLowerInvariant());
            }
        }

        internal static string Text(string value)
        {
            return value ?? "";
        }
    }
}
