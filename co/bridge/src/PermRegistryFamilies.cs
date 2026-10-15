using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 按角色分族的只读报表（质量、生产、库龄、采购、销售、收付款与现金流、账务核对，ReportsFamilies）的权限规则。
    // 功能 id 取自 UFSystem 的 UA_Auth_base / UA_Auth_lang（UAP 报表授权其「_01」查询视图），数据权限对象与列同各报表的主表。
    // 同一单据口径的报表直接用同一份对象：产成品入库日报同产成品入库单、生产订单完工状况与用料分析同生产订单、
    // 销售订单执行统计同销售订单（ProductInObjs、ProductionOrderObjs、SaleOrderObjs，不抄列清单）。
    // 来料 / 产品、应收 / 应付各分两条规则，键带后缀（SplitKey，由请求体里的 kind / side 定）。
    // 存货金额另有一条遮蔽规则 mask:amount（MayViewAmount）：没有它的操作员照给数量，金额置空并标 masked_fields。
    internal static partial class PermRegistry
    {
        // 能看存货金额的功能 id（收发存汇总、库龄的金额列用，规则 mask:amount）。
        internal const string AmountAuth = "IA3011_01";
        internal const string AmountRule = "mask:amount";

        // 按请求体字段分两条规则的报表：{ 报表名, 字段, 取该值时的后缀, 其余（含没带字段）时的后缀 }。
        // 往来报表按 side 分应收 / 应付，质量统计按 kind 分来料 / 产品，收付款汇总与到期票据按 side。
        static readonly string[][] Splits = new string[][]
        {
            new string[] { "arap_balance", "side", "ap", "ar" },
            new string[] { "arap_aging", "side", "ap", "ar" },
            new string[] { "arap_detail", "side", "ap", "ar" },
            new string[] { "receipts_payments", "side", "ap", "ar" },
            new string[] { "notes_due", "side", "ap", "ar" },
            new string[] { "qm_indicators", "kind", "product", "incoming" },
            new string[] { "qm_pass_rate", "kind", "product", "incoming" }
        };

        static string[] SplitRow(string name)
        {
            for (int i = 0; i < Splits.Length; i++)
            {
                if (Splits[i][0] == name)
                {
                    return Splits[i];
                }
            }
            return null;
        }

        // 分两条规则的报表的规则键 report:<name>:<后缀>；不是这些报表返回 null。
        static string SplitKey(string name, WorkItem item)
        {
            string[] row = SplitRow(name);
            if (row == null)
            {
                return null;
            }
            return "report:" + name + ":" + (BodyText(item, row[1]) == row[2] ? row[2] : row[3]);
        }

        // 一张报表可能的规则后缀（自检用）：登记在 Splits 里的取其两个值，其余按应收 / 应付（核销记录、期初余额等）。
        internal static string[] KeySuffixes(string name)
        {
            string[] row = SplitRow(name);
            return row == null ? new string[] { "ar", "ap" } : new string[] { row[3], row[2] };
        }

        // 该操作员能否看存货金额：账套主管，或持有 IA3011_01（mask:amount）。库存汇总、库龄在组装响应前问一次。
        internal static bool MayViewAmount(WorkContext ctx)
        {
            PermRule rule = ForKey(AmountRule);
            return rule != null && PermCheck.Of(ctx).HasAny(rule.Auths);
        }

        // 金额被遮时在响应体里标 masked_fields: ["amount"]（与已有的字段名并集、排序去重）；金额字段本身由调用方置 null。
        internal static void NoteAmountMasked(Dictionary<string, object> body)
        {
            List<string> names = new List<string>();
            object old;
            if (body.TryGetValue(PermMask.Key, out old))
            {
                IEnumerable list = old as IEnumerable;
                if (list != null)
                {
                    foreach (object one in list)
                    {
                        names.Add(Convert.ToString(one));
                    }
                }
            }
            if (!names.Contains("amount"))
            {
                names.Add("amount");
            }
            names.Sort(StringComparer.Ordinal);
            body[PermMask.Key] = names;
        }

        // 产成品入库单的数据权限对象（与 voucher:product_in 同一份 Rd()）：产成品入库日报共用。
        static PermObj[] ProductInObjs()
        {
            return Rd("product_in", "产成品入库单", ProductInAuth, "rdrecords10", false, false).Objs;
        }

        static PermRule[] FamilyReportRules()
        {
            return JoinRules(MaskRules(), QmReportRules(), MfgStockReportRules(), PuSaReportRules(), FinAcctReportRules());
        }

        static PermRule[] JoinRules(params PermRule[][] parts)
        {
            List<PermRule> all = new List<PermRule>();
            for (int i = 0; i < parts.Length; i++)
            {
                all.AddRange(parts[i]);
            }
            return all.ToArray();
        }

        // 字段遮蔽用的规则：只有功能 id，没有路由。
        static PermRule[] MaskRules()
        {
            return new PermRule[] { R(AmountRule, "存货金额", A(AmountAuth)) };
        }

        static PermRule[] QmReportRules()
        {
            return new PermRule[]
            {
                R("report:qm_indicators:incoming", "来料指标检验统计表", A("QM040125_01"),
                    PermObj.Opt(PermObj.Vendor, "CVENCODE"), PermObj.H(PermObj.Inventory, "CINVCODE")),
                R("report:qm_indicators:product", "产品指标检验统计表", A("QM040329_01"), PermObj.H(PermObj.Inventory, "CINVCODE")),
                R("report:qm_pass_rate:incoming", "来料检验统计分析表", A("QM040101_01"),
                    PermObj.Opt(PermObj.Vendor, "CVENCODE"), PermObj.H(PermObj.Inventory, "CINVCODE")),
                R("report:qm_pass_rate:product", "产品检验统计分析表", A("QM040301_01"), PermObj.H(PermObj.Inventory, "CINVCODE"))
            };
        }

        // 生产三张（产成品入库日报、生产订单完工状况、生产订单用料）和库龄分析。
        static PermRule[] MfgStockReportRules()
        {
            return new PermRule[]
            {
                R("report:production_daily", "产成品入库日报", A(ProductInAuth), ProductInObjs()),
                R("report:mo_progress", "生产订单完工状况表", A("MO04005_01"), ProductionOrderObjs()),
                R("report:mo_consumption", "生产订单用料分析", A("MO04005_01", "MO03005Q"), ProductionOrderObjs()),
                R("report:stock_age", "库龄分析", A("ST020205_01", "ST020204_01"),
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.H(PermObj.Inventory, "cInvCode"))
            };
        }

        // 采购两张和销售三张。
        static PermRule[] PuSaReportRules()
        {
            return new PermRule[]
            {
                R("report:purchase_analysis", "采购综合统计表", A("PU050307_01"),
                    PermObj.Opt(PermObj.Vendor, "cVenCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                R("report:arrival_otif", "采购时效性统计表", A("PU050314_01"),
                    PermObj.H(PermObj.Vendor, "cVenCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                R("report:open_orders", "销售订单执行统计表", A("SA030105_01"), SaleOrderObjs()),
                R("report:sales_price_history", "单据历史价格查询", A("SA031209", "SA040401_01"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                R("report:customer_churn", "销售统计表", A("SA040401_01"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.Opt(PermObj.Person, "cPersonCode"))
            };
        }

        // 收付款与现金流三张（收付款、到期票据按应收 / 应付各一条）和账务核对三张。
        static PermRule[] FinAcctReportRules()
        {
            return new PermRule[]
            {
                R("report:receipts_payments:ar", "收款单查询", A("AR0601031"), PermObj.H(PermObj.Customer, "cDwCode"),
                    PermObj.Opt(PermObj.Department, "cDeptCode"), PermObj.Opt(PermObj.Person, "cPerson")),
                R("report:receipts_payments:ap", "付款单查询", A("AP0601031"), PermObj.H(PermObj.Vendor, "cDwCode"),
                    PermObj.Opt(PermObj.Department, "cDeptCode"), PermObj.Opt(PermObj.Person, "cPerson")),
                R("report:notes_due:ar", "应收票据到期分析", A("AR212013", "AR2231"), PermObj.Opt(PermObj.Customer, "cDwCode")),
                R("report:notes_due:ap", "应付票据到期分析", A("AP212013", "AP2231"), PermObj.Opt(PermObj.Vendor, "cDwCode")),
                R("report:cash_flow", "现金流量统计表", A("GL1302", "GL1301")),
                R("report:close_check", "月末结账检查", CloseStatusAuths()),
                R("report:ia_anomalies", "存货核算检查", A("IA3017_01", AmountAuth),
                    PermObj.Opt(PermObj.Vendor, "cVenCode"), PermObj.H(PermObj.Inventory, "cInvCode"),
                    PermObj.Opt(PermObj.Warehouse, "cWhCode")),
                R("report:cost_unit", "完工产品成本汇总表", A("CA0504_01", "CA0512"), PermObj.H(PermObj.Inventory, "InvCode"))
            };
        }
    }
}
