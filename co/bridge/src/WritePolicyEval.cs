using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一笔写请求的分类结果（WriteClass 由路径和请求体得出），交给 WritePolicyEval.Check。
    internal sealed class WriteRequestInfo
    {
        public string Acc;
        // 单据类型名，或无类型路由族的记号（gl、archives、arap、notes……）。
        public string Type;
        // 操作，取 WritePolicySnapshot.OpNames 之一。
        public string Op;
        // U8 操作员编码（请求的 user）。
        public string Operator;
        // 请求体的明细行数（新增、修改、生单）；没有明细为 0。
        public int Lines;
        // 收付款类单据的金额（表头金额或表体金额合计）；其他类型为 null。
        public decimal? Amount;
        // 收付款类单据新增 / 修改给了金额或汇率、但按写入方的规则解析不了；配置了金额上限时按超过上限拒绝。
        public bool AmountBad;
        public bool DryRun;
        public string Path;
    }

    // 写入策略的判定（第 1 到 6 步，先失败的一步为准）。写预演（dry_run）同样判定。
    // 第 7 步限额（WriteQuota）和第 8 步许可（LicenseHold）只在桥上另做。
    internal static class WritePolicyEval
    {
        // 金额上限只对这些收付款类单据生效。
        public static readonly string[] AmountTypes = new string[] { "ar_receipt", "ap_payment", "ar_refund", "ap_refund" };
        // 行数上限只对这些操作生效。
        static readonly string[] LineOps = new string[] { "create", "update", "generate" };

        // null 表示放行。未配置写入策略（off）时一律放行。
        public static BridgeException Check(WriteRequestInfo info, DateTime nowLocal)
        {
            if (WritePolicy.State == "off")
            {
                return null;
            }
            return Check(WritePolicy.Current, info, nowLocal);
        }

        // 按给定快照判定；snap 为 null 表示策略不可用（文件缺失、无效或从未加载成功）。
        internal static BridgeException Check(WritePolicySnapshot snap, WriteRequestInfo info, DateTime nowLocal)
        {
            if (snap == null)
            {
                return new BridgeException(503, "write_policy_unavailable", "写入策略不可用");
            }
            if (snap.IsFrozen(info.Acc))
            {
                return new BridgeException(503, "write_frozen", "写入已冻结" + (snap.FreezeReason == null ? "" : "：" + snap.FreezeReason));
            }
            if (!snap.WindowOpen(nowLocal))
            {
                return new BridgeException(503, "write_window", "当前时段不允许写入");
            }
            PolicyAccount acc = snap.Account(info.Acc);
            if (acc == null ? !snap.UnlistedAllow : !acc.Allows(info.Type, info.Op))
            {
                return new BridgeException(403, "write_not_allowed", "该账套不允许此写入").WithDetail(Detail(info));
            }
            if (acc != null && !acc.Operators.Permits(info.Operator))
            {
                return new BridgeException(403, "operator_not_allowed", "该操作员不能在此账套写入");
            }
            return CheckLimits(snap.Quota(info.Acc), info);
        }

        // 第 6 步：行数上限（新增、修改、生单）；金额上限（只对收付款类单据，按绝对值比较；金额或汇率解析不了按超限拒绝）。
        static BridgeException CheckLimits(WriteQuotaLimits quota, WriteRequestInfo info)
        {
            if (quota.MaxLines > 0 && info.Lines > quota.MaxLines && Array.IndexOf(LineOps, info.Op) >= 0)
            {
                return new BridgeException(400, "write_limit", "行数超过上限", "lines")
                    .WithDetail(Limit(quota.MaxLines, info.Lines));
            }
            if (quota.MaxAmount <= 0m || Array.IndexOf(AmountTypes, info.Type) < 0)
            {
                return null;
            }
            if (info.AmountBad)
            {
                Dictionary<string, object> detail = new Dictionary<string, object>();
                detail["max"] = quota.MaxAmount;
                return new BridgeException(400, "write_limit", "金额或汇率无法识别，按超过上限处理").WithDetail(detail);
            }
            if (info.Amount.HasValue && Math.Abs(info.Amount.Value) > quota.MaxAmount)
            {
                return new BridgeException(400, "write_limit", "金额超过上限")
                    .WithDetail(Limit(quota.MaxAmount, info.Amount.Value));
            }
            return null;
        }

        static Dictionary<string, object> Detail(WriteRequestInfo info)
        {
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["type"] = info.Type ?? "";
            detail["op"] = info.Op ?? "";
            return detail;
        }

        static Dictionary<string, object> Limit(decimal max, decimal actual)
        {
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["max"] = max;
            detail["actual"] = actual;
            return detail;
        }
    }
}
