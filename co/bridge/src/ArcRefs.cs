using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 计量单位组、结算方式等七类档案和原因码删除前的检查：有下级（分级档案）、被档案或单据引用，桥在调用 U8 之前 409。
    // 引用表只列主要的档案和单据（表名列名是常量），U8 自己的 ArchIsUsed 可能还会拒绝更多，原文 409 带回。
    // 未覆盖：客户 / 供应商银行账户的 cBank 是银行档案编码。
    internal static class ArcRefs
    {
        const string ChildSql = "SELECT TOP 1 {0} FROM {1} WHERE {0} LIKE ? ESCAPE '\\' AND {0}<>?";

        static readonly Dictionary<string, string[]> Refs = Build();
        static readonly Dictionary<string, string> Sql = Compile();

        // 标签、表、列成组。
        static Dictionary<string, string[]> Build()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>();
            map[ArcKindRwMore.UnitGroup] = new string[]
            {
                "计量单位", "ComputationUnit", "cGroupCode", "存货档案", "Inventory", "cGroupCode"
            };
            map[ArcKindRwMore.Settle] = new string[]
            {
                "收付款单", "Ap_CloseBill", "cSSCode", "销售订单", "SO_SOMain", "cSSCode", "发货单", "DispatchList", "cSSCode",
                "销售发票", "SaleBillVouch", "cSSCode", "往来明细", "Ap_Detail", "cSSCode"
            };
            map[ArcKindRwMore.Rd] = new string[]
            {
                "采购入库单", "RdRecord01", "cRdCode", "其他入库单", "RdRecord08", "cRdCode", "其他出库单", "RdRecord09", "cRdCode",
                "产成品入库单", "RdRecord10", "cRdCode", "材料出库单", "RdRecord11", "cRdCode", "销售出库单", "RdRecord32", "cRdCode",
                "发货单", "DispatchList", "cRdCode", "销售发票", "SaleBillVouch", "cRdCode", "领料申请单", "MaterialAppVouch", "cRdCode",
                "采购类型", "PurchaseType", "cRdCode", "销售类型", "SaleType", "cRdCode"
            };
            map[ArcKindRwMore.PurchaseType] = new string[]
            {
                "采购订单", "PO_Pomain", "cPTCode", "到货单", "PU_ArrivalVouch", "cPTCode", "采购发票", "PurBillVouch", "cPTCode",
                "采购入库单", "RdRecord01", "cPTCode", "请购单", "PU_AppVouch", "cPTCode", "委外订单", "OM_MOMain", "cPTCode"
            };
            map[ArcKindRwMore.SaleType] = new string[]
            {
                "销售订单", "SO_SOMain", "cSTCode", "发货单", "DispatchList", "cSTCode", "销售发票", "SaleBillVouch", "cSTCode",
                "销售出库单", "RdRecord32", "cSTCode", "报价单", "SA_QuoMain", "cSTCode"
            };
            map[ArcKindRwMore.District] = new string[] { "客户档案", "Customer", "cDCCode", "供应商档案", "Vendor", "cDCCode" };
            map[ArcKindRwMore.AaBank] = new string[]
            {
                "本单位开户银行", "Bank", "cBankCode", "客户档案", "Customer", "cCusBankCode", "供应商档案", "Vendor", "cVenBankCode",
                "客户银行账户", "CustomerBank", "cBank", "供应商银行账户", "VendorBank", "cBank", "人员档案", "hr_hi_person", "cPsnBankCode"
            };
            // 原因码（ArcReason）：不良品处理单、检验单、报检单的不良 / 退货原因，发货单、销售发票、退货申请、销售结算的原因
            // （U8 表结构中列名为 CREASONCODE / cReasonCode / CRETURNREASONCODE 的业务表，不含 History 表；生产、车间的 ReasonCode 未核实，不列）。
            map[ArcReason.Name] = new string[]
            {
                "不良品处理单", "QMREJECTVOUCHERS", "CREASONCODE", "不良品处理单", "QMREJECTVOUCHER", "CRETURNREASONCODE",
                "检验单", "QMCHECKVOUCHER", "CREASONCODE", "检验单", "QMCHECKVOUCHER", "CRETURNREASONCODE",
                "报检单", "QMINSPECTVOUCHERS", "CRETURNREASONCODE", "发货单", "DispatchLists", "cReasonCode",
                "销售发票", "SaleBillVouchs", "cReasonCode", "退货申请单", "SA_ReturnsApplyDetail", "cReasonCode",
                "销售结算", "SA_SettleVouchs", "cReasonCode"
            };
            return map;
        }

        // SELECT TOP 1 u.x FROM (SELECT TOP 1 N'<标签>' AS x FROM <表> WHERE <列>=? UNION ALL …) u
        static Dictionary<string, string> Compile()
        {
            Dictionary<string, string> sql = new Dictionary<string, string>();
            foreach (KeyValuePair<string, string[]> pair in Refs)
            {
                StringBuilder sb = new StringBuilder("SELECT TOP 1 u.x FROM (");
                string[] r = pair.Value;
                for (int i = 0; i + 2 < r.Length; i += 3)
                {
                    if (i > 0)
                    {
                        sb.Append(" UNION ALL ");
                    }
                    sb.Append("SELECT TOP 1 N'").Append(r[i]).Append("' AS x FROM ").Append(r[i + 1])
                        .Append(" WHERE ").Append(r[i + 2]).Append("=?");
                }
                sql[pair.Key] = sb.Append(") u").ToString();
            }
            return sql;
        }

        internal static bool Covers(string archive)
        {
            return archive != null && Refs.ContainsKey(archive);
        }

        // 引用该编码的第一类档案或单据的名称；没有引用返回 null。
        internal static string Used(object conn, string archive, string code)
        {
            string sql;
            if (!Sql.TryGetValue(archive, out sql))
            {
                return null;
            }
            int n = Refs[archive].Length / 3;
            object[] args = new object[n];
            for (int i = 0; i < n; i++)
            {
                args[i] = code;
            }
            return Rows.Scalar(conn, sql, args);
        }

        public static void CheckDelete(object conn, ArcReq req)
        {
            ArcKind k = req.Kind;
            if (!Covers(k.Name))
            {
                return;
            }
            if (k.Grade != null)
            {
                string child = Rows.Scalar(conn, string.Format(ChildSql, k.Key, k.Table),
                    new object[] { ArcRead.Like(req.Code, false), req.Code });
                if (child != null)
                {
                    throw ArcGuard.State("档案 " + req.Code + " 有下级 " + child.Trim() + "，不能删除");
                }
            }
            string used = Used(conn, k.Name, req.Code);
            if (used != null)
            {
                throw ArcGuard.State("档案 " + req.Code + " 已被" + used + "使用，不能删除");
            }
        }
    }
}
