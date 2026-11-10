using System;
using System.Collections.Generic;

namespace U8Co
{
    // 销售订单修改的计量组（存货、计量单位、换算率、数量、件数）：改了其中任一个都要走组件的 BodyCheck 重算，
    // 保存后由 SalePinsGroup 核对请求值原样落库、数量 = 件数 × 换算率。
    internal static partial class SaleEdit
    {
        static bool NeedsCalc(LineOp op)
        {
            return op.Num || op.Qty || op.Group || op.Inv || op.TaxPrice || op.UnitPrice || op.TaxRate;
        }

        // 换了存货：清掉原行的单位组、单位、换算率和件数，按新存货重填。只换了计量单位：清掉原行的换算率和件数
        // （调用方给了的除外），按单位档案重填换算率、按数量重算件数（SoUnits）。都没换就保留原行的单位和换算率。
        static void RenewUnits(EditBag bag, object row, LineOp op, Dictionary<string, string> clean)
        {
            bool inv = op.Inv && !SameInv(clean, row);
            if (!inv && !NewUnit(clean, row))
            {
                return;
            }
            bool seeded = false;
            if (inv)
            {
                DropAttrs(bag.Doms[1], row);
            }
            else
            {
                DropRate(bag.Doms[1], row, clean);
                seeded = SeedInv(bag.Schema, clean, row);
            }
            SeedQty(clean, row);
            FillUnits(bag, op, clean);
            if (seeded)
            {
                clean.Remove("cinvcode");
            }
        }

        static void FillUnits(EditBag bag, LineOp op, Dictionary<string, string> clean)
        {
            List<Dictionary<string, string>> one = new List<Dictionary<string, string>>();
            one.Add(clean);
            bool keepPre = HasKey(op.Fields, "dpremodate");
            bool keepPreDate = HasKey(op.Fields, "dpredate");
            SoUnits.Fill(bag.Ctx.Conn, bag.Schema, one, bag.HeadVals);
            if (!keepPre)
            {
                clean.Remove("dPreMoDate");
            }
            if (!keepPreDate)
            {
                clean.Remove("dPreDate");
            }
        }

        static bool NewUnit(Dictionary<string, string> clean, object row)
        {
            string sent = SentCode(clean, "cunitid");
            return sent.Length > 0 && !string.Equals(sent, DomRows.Get(row, "cunitid").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        // 换单位时原行的换算率、件数作废；调用方这次给了的留下。
        static void DropRate(object dom, object row, Dictionary<string, string> clean)
        {
            if (SentCode(clean, "iinvexchrate").Length == 0)
            {
                DomRows.Set(dom, row, "iInvExchRate", null);
            }
            if (SentCode(clean, "inum").Length == 0)
            {
                DomRows.Set(dom, row, "inum", null);
            }
        }

        // SoUnits 按存货档案找单位组和换算率，存货没变时借用原行的存货编码，填完再拿掉（不重写存货）。
        static bool SeedInv(Dictionary<string, string> schema, Dictionary<string, string> clean, object row)
        {
            string inv = DomRows.Get(row, "cinvcode").Trim();
            string canon;
            if (inv.Length == 0 || SentCode(clean, "cinvcode").Length > 0 || !schema.TryGetValue("cinvcode", out canon))
            {
                return false;
            }
            clean[canon] = inv;
            return true;
        }

        static void DropAttrs(object dom, object row)
        {
            for (int i = 0; i < UnitAttrs.Length; i++)
            {
                DomRows.Set(dom, row, UnitAttrs[i], null);
            }
        }

        static bool SameInv(Dictionary<string, string> clean, object row)
        {
            string sent = SentCode(clean, "cinvcode");
            if (sent.Length == 0)
            {
                return true;
            }
            return string.Equals(sent, DomRows.Get(row, "cinvcode").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        static string SentCode(Dictionary<string, string> clean, string name)
        {
            string value;
            if (clean == null || !clean.TryGetValue(name, out value) || value == null)
            {
                return "";
            }
            return value.Trim();
        }

        static void SeedQty(Dictionary<string, string> clean, object row)
        {
            string have;
            if (clean.TryGetValue("iquantity", out have) && have != null && have.Trim().Length > 0)
            {
                return;
            }
            string qty = DomRows.Get(row, "iquantity").Trim();
            if (qty.Length > 0)
            {
                clean["iquantity"] = qty;
            }
        }

        // 数量优先：给了数量按数量算（U8 按换算率回写件数，件数也给了时保存后核对两者都原样落库）；
        // 只给件数按件数（U8 回写数量）；只改换算率、单位或存货按行上的数量重算件数和金额。
        static void TouchCalc(object co, object[] doms, object row, LineOp op)
        {
            SalePrices flags = SaleCalc.Flags(op.TaxPrice, op.UnitPrice, op.TaxRate);
            if (op.Num && !op.Qty)
            {
                SaleCalc.RecalcNum(co, doms[0], doms[1], row, flags);
                return;
            }
            SaleCalc.Recalc(co, doms[0], doms[1], row, flags);
        }
    }
}
