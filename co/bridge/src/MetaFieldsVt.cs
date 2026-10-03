using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // meta/fields 用哪张 U8 单据模板（VT）。与桥写单据时用的模板一致，且不建 COM：
    // - fixed：桥写死的 ivtid（到货单、采购退货单 8169 见 PuArrDom / PuRetSave；采购发票专用 8163 见 PuInvBody；
    //   无来源采购入库 27 见 StockDomPurIn；报检单、检验单 QmSpec.Vt）。该 VT 在本账套没有模板行时退到卡片缺省。
    // - com：桥写单据时经 COM 取缺省显示模板（销售类 getDefaltVTID 见 SoSave，采购订单、请购单 GetDefaultVTID 见
    //   PurchaseEditSave / PuAppSave）。这里改查 SQL：先操作员自选模板 Voucher_UserDefaultTemplate（未覆盖：UserName
    //   存的是操作员编码还是姓名，未经实测；对不上时自然落到下一步），再卡片缺省 vouchers.DEF_ID。
    // - card：其余类型桥不设 ivtid（U8 组件按卡片缺省处理），直接取 vouchers.DEF_ID。
    // 卡片号是 U8 的标准编号（vouchers.CardNumber），与账套无关。销售类用 Kinds 的 SaCard（销售发票按专用发票 07）。
    internal sealed class VtPick
    {
        public int VtId;
        public string Card;
        public string Source;
        public List<TplRow> Rows = new List<TplRow>();
    }

    internal static class MetaFieldsVt
    {
        internal const string Fixed = "fixed";
        internal const string Com = "com";
        internal const string Card = "card";
        const string UserSql = "SELECT TOP 1 VT_ID AS v FROM Voucher_UserDefaultTemplate "
            + "WHERE CardNum=? AND UserName=? AND ISNULL(bPrint,0)=0";
        const string DefSql = "SELECT TOP 1 DEF_ID AS v FROM vouchers WHERE CardNumber=?";

        // { 类型, 卡片号, 写死的 VT（0 = 无）, 取法 }。销售类的卡片号取 Kinds.SaCard，这里只写取法。
        static readonly string[][] Table = new string[][]
        {
            new string[] { "sale_order", "", "0", Com },
            new string[] { "dispatch", "", "0", Com },
            new string[] { "sale_return", "", "0", Com },
            new string[] { "sale_invoice", "", "0", Com },
            // 存货调价单（卡片 SA18）只读，取卡片缺省。
            new string[] { InvPriceAdjustRead.KindName, "SA18", "0", Card },
            // 退货申请单（卡片 SA31）只读，取卡片缺省。
            new string[] { ReturnsApplyRead.KindName, "SA31", "0", Card },
            new string[] { "purchase_order", "88", "0", Com },
            new string[] { "purchase_requisition", "27", "0", Com },
            new string[] { "arrival", "26", "8169", Fixed },
            new string[] { "purchase_return", "26", "8169", Fixed },
            new string[] { "purchase_invoice", "25", "8163", Fixed },
            // 采购结算单：卡片 99，本期只读，取卡片缺省。
            new string[] { PuSettleRead.KindName, "99", "0", Card },
            new string[] { "purchase_in", "24", "27", Fixed },
            new string[] { "other_in", "0301", "0", Card },
            new string[] { "other_out", "0302", "0", Card },
            new string[] { "sale_out", "0303", "0", Card },
            new string[] { "transfer", "0304", "0", Card },
            new string[] { "shape_change", "0305", "0", Card },
            new string[] { "stock_check", "0307", "0", Card },
            new string[] { "transfer_request", "0324", "0", Card },
            // 货位调整单：卡片 0313（DEF_ID 113，与新增写的 VT_ID 一致）。
            new string[] { "position_adjust", "0313", "0", Card },
            new string[] { "stock_opening", "0319", "0", Card },
            new string[] { "product_in", "0411", "0", Card },
            new string[] { "material_out", "0412", "0", Card },
            // 出入库调整单只读：入库、出库调整同表，按入库调整单的卡片 0401 取缺省。
            new string[] { IaAdjustRead.KindName, "0401", "0", Card },
            new string[] { "production_order", "MO21", "0", Card },
            new string[] { "bom", "BO11", "0", Card },
            new string[] { "qm_incoming_reject", "QM05", "0", Card },
            new string[] { "qm_product_reject", "QM06", "0", Card },
            // 其他报检单、其他检验单：已保存单据的 IVTID 恒为 361 / 365（已在测试账套核对）。
            new string[] { "qm_other_inspect", "QM11", "361", Fixed },
            new string[] { "qm_other_check", "QM15", "365", Fixed },
            new string[] { "ar_receipt", "AR48", "0", Card },
            new string[] { "ap_payment", "AP49", "0", Card },
            new string[] { "ar_bill", "AR04", "0", Card },
            new string[] { "ap_bill", "AP04", "0", Card },
            // 供应商退款、客户退款：卡片 AP48 / AR49（U8 保存时写 VT_ID 8051 / 8055）。
            new string[] { "ap_refund", "AP48", "0", Card },
            new string[] { "ar_refund", "AR49", "0", Card }
        };

        // 纯查表：{ 卡片号, 写死的 VT, 取法 }；未登记的类型返回 null（该类型没有模板，字段只列名字）。
        internal static string[] Plan(VoucherKind kind)
        {
            QmSpec qm = QmSpec.Find(kind.Name);
            if (qm != null)
            {
                return new string[] { qm.VouchType, qm.Vt.ToString(CultureInfo.InvariantCulture), Fixed };
            }
            for (int i = 0; i < Table.Length; i++)
            {
                if (Table[i][0] != kind.Name)
                {
                    continue;
                }
                string card = kind.Family == "sa" && !string.IsNullOrEmpty(kind.SaCard) ? kind.SaCard : Table[i][1];
                return new string[] { card, Table[i][2], Table[i][3] };
            }
            return null;
        }

        // 按候选顺序取第一张有模板行的 VT；都没有行时 VtId 是第一个候选（可能为 0）、Rows 为空。
        internal static VtPick Pick(object conn, VoucherKind kind, string user)
        {
            VtPick pick = new VtPick();
            pick.Source = "none";
            string[] plan = Plan(kind);
            if (plan == null)
            {
                return pick;
            }
            pick.Card = plan[0];
            List<KeyValuePair<int, string>> tries = Candidates(conn, plan, user);
            for (int i = 0; i < tries.Count; i++)
            {
                List<TplRow> rows = MetaFieldsTpl.Load(conn, tries[i].Key);
                if (i == 0 || rows.Count > 0)
                {
                    pick.VtId = tries[i].Key;
                    pick.Source = tries[i].Value;
                    pick.Rows = rows;
                }
                if (rows.Count > 0)
                {
                    break;
                }
            }
            return pick;
        }

        static List<KeyValuePair<int, string>> Candidates(object conn, string[] plan, string user)
        {
            List<KeyValuePair<int, string>> list = new List<KeyValuePair<int, string>>();
            int fixedVt = ParseVt(plan[1]);
            if (fixedVt > 0)
            {
                Add(list, fixedVt, "fixed");
            }
            if (plan[2] == Com)
            {
                Add(list, UserDefault(conn, plan[0], user), "user_default");
            }
            Add(list, ParseVt(Rows.Scalar(conn, DefSql, new object[] { plan[0] })), "card_default");
            return list;
        }

        // 自选模板表各版本不一定有；查不到、查询出错都当没有。
        static int UserDefault(object conn, string card, string user)
        {
            if (string.IsNullOrEmpty(user))
            {
                return 0;
            }
            try
            {
                return ParseVt(Rows.Scalar(conn, UserSql, new object[] { card, user }));
            }
            catch (BridgeException)
            {
                return 0;
            }
            catch (System.Runtime.InteropServices.COMException)
            {
                // 表或列在该 U8 版本里不存在等：这一步只是可选的优先项，落到卡片缺省。
                return 0;
            }
        }

        static void Add(List<KeyValuePair<int, string>> list, int vt, string source)
        {
            if (vt <= 0)
            {
                return;
            }
            for (int i = 0; i < list.Count; i++)
            {
                if (list[i].Key == vt)
                {
                    return;
                }
            }
            list.Add(new KeyValuePair<int, string>(vt, source));
        }

        internal static int ParseVt(string text)
        {
            int vt;
            if (text == null || !int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out vt))
            {
                return 0;
            }
            return vt > 0 ? vt : 0;
        }
    }
}
