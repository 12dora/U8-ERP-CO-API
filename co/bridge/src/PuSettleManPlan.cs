using System;
using System.Collections.Generic;

namespace U8Co
{
    // 采购手工结算：按请求行算出每条结算行的数量、金额、暂估（纯函数，--selftest 直接测）。口径照 U8 的 PU_AutoSettleBillRefRD
    // 与 U8 手工结算单：
    // 配对行 iSVPrice = 结算金额（缺省 = 发票行无税金额 × 数量 / 发票数量，两位；把发票行结清的那一笔取余额，避免分摊尾差），
    //   iSVCost = 金额 / 数量（单价位数，整行结清且没给 amount 时直接用发票单价），iTax、iSum 同样按比例 / 取余额，iSVNum 按比例；
    //   iSVACost = 入库行暂估单价 fACost，iSVAPrice = 整行一次结清时入库行 iAPrice、把入库行结清的那一笔取余额、否则 fACost × 数量；
    //   入库行没有暂估单价时暂估取结算单价、金额。
    // 红蓝入库对冲行（没有发票行）：iSVCost = iSVACost = 暂估单价，iSVPrice = iSVAPrice = 暂估金额，税额、价税合计 0。
    // 红蓝发票对冲行（没有入库行）：iSVCost = 发票单价，iSVPrice = 结算金额，暂估、税额、价税合计 0。
    internal static class PuSettleManPlan
    {
        internal const decimal Eps = 0.000001m;

        internal static List<ManRow> Compute(List<ManLine> lines, Dictionary<int, ManRd> rds, Dictionary<int, ManBs> bss,
            int costDec)
        {
            Dictionary<int, decimal[]> rdUsed = new Dictionary<int, decimal[]>();
            Dictionary<int, decimal[]> bsUsed = new Dictionary<int, decimal[]>();
            List<ManRow> rows = new List<ManRow>();
            for (int i = 0; i < lines.Count; i++)
            {
                ManLine line = lines[i];
                ManRow row = new ManRow();
                row.Line = line;
                row.Rd = line.InId > 0 ? rds[line.InId] : null;
                row.Bs = line.BsId > 0 ? bss[line.BsId] : null;
                row.Qty = line.Qty;
                if (row.Bs != null)
                {
                    BillSide(row, Used(bsUsed, row.Bs.Id, row.Bs.PriorQty, row.Bs.PriorMoney, row.Bs.PriorTax), costDec);
                }
                if (row.Rd != null)
                {
                    RdSide(row, Used(rdUsed, row.Rd.Id, row.Rd.SQty, row.Rd.PriorAPrice, 0m));
                }
                rows.Add(row);
            }
            return rows;
        }

        // 已占用的 [数量, 金额, 税额]：起点是库里已有的结算，逐行累加本次请求。
        static decimal[] Used(Dictionary<int, decimal[]> map, int id, decimal qty, decimal money, decimal tax)
        {
            decimal[] used;
            if (!map.TryGetValue(id, out used))
            {
                used = new decimal[] { qty, money, tax };
                map[id] = used;
            }
            return used;
        }

        static void BillSide(ManRow row, decimal[] used, int costDec)
        {
            ManBs bs = row.Bs;
            bool whole = Same(row.Qty, bs.Qty) && used[0] == 0m;
            bool last = Same(used[0] + row.Qty, bs.Qty);
            decimal share = bs.Qty == 0m ? 0m : row.Qty / bs.Qty;
            row.Money = row.Line.HasAmount ? row.Line.Amount : last ? bs.Money - used[1] : Money(bs.Money * share);
            row.Cost = whole && !row.Line.HasAmount ? bs.Cost : Round(row.Money / row.Qty, costDec);
            if (row.Rd != null)
            {
                TaxSide(row, used, whole, last, share);
            }
            used[0] += row.Qty;
            used[1] += row.Money;
            used[2] += row.Tax;
        }

        // 配对行的税额、价税合计、件数（红蓝发票对冲行没有）。
        static void TaxSide(ManRow row, decimal[] used, bool whole, bool last, decimal share)
        {
            ManBs bs = row.Bs;
            row.Tax = last ? bs.Tax - used[2] : Money(bs.Tax * share);
            row.Sum = row.Money + row.Tax;
            row.Num = whole || bs.Num == 0m ? bs.Num : Round(bs.Num * share, 6);
        }

        static void RdSide(ManRow row, decimal[] used)
        {
            ManRd rd = row.Rd;
            bool whole = Same(row.Qty, rd.Qty) && used[0] == 0m;
            bool last = Same(used[0] + row.Qty, rd.Qty);
            if (!rd.HasACost)
            {
                row.ACost = row.Bs != null ? row.Cost : rd.UCost;
                row.APrice = row.Bs != null ? row.Money : Money(rd.UCost * row.Qty);
            }
            else
            {
                row.ACost = rd.ACost;
                row.APrice = whole ? rd.APrice : last ? rd.APrice - used[1] : Money(rd.ACost * row.Qty);
            }
            if (row.Bs == null)
            {
                row.Cost = row.ACost;
                row.Money = row.APrice;
            }
            used[0] += row.Qty;
            used[1] += row.APrice;
        }

        internal static bool Same(decimal a, decimal b)
        {
            return Math.Abs(a - b) < Eps;
        }

        internal static decimal Money(decimal value)
        {
            return Round(value, 2);
        }

        internal static decimal Round(decimal value, int digits)
        {
            return Math.Round(value, Math.Max(0, Math.Min(digits, 10)), MidpointRounding.AwayFromZero);
        }

        // 入库行回写（PU_SettleWriteBKRDS）的一组：同一入库行在本单里的数量、暂估金额、结算金额合计，按首次出现的次序。
        internal static List<ManRdSum> RdSums(List<ManRow> rows)
        {
            List<ManRdSum> list = new List<ManRdSum>();
            Dictionary<int, ManRdSum> map = new Dictionary<int, ManRdSum>();
            for (int i = 0; i < rows.Count; i++)
            {
                if (rows[i].Rd == null)
                {
                    continue;
                }
                ManRdSum sum;
                if (!map.TryGetValue(rows[i].Rd.Id, out sum))
                {
                    sum = new ManRdSum();
                    sum.Rd = rows[i].Rd;
                    map[sum.Rd.Id] = sum;
                    list.Add(sum);
                }
                sum.Qty += rows[i].Qty;
                sum.APrice += rows[i].APrice;
                sum.Money += rows[i].Money;
            }
            return list;
        }

        // 发票行在本单里的数量合计，按首次出现的次序。
        internal static List<KeyValuePair<ManBs, decimal>> BsSums(List<ManRow> rows)
        {
            List<KeyValuePair<ManBs, decimal>> list = new List<KeyValuePair<ManBs, decimal>>();
            Dictionary<int, int> at = new Dictionary<int, int>();
            for (int i = 0; i < rows.Count; i++)
            {
                ManBs bs = rows[i].Bs;
                if (bs == null)
                {
                    continue;
                }
                int k;
                if (!at.TryGetValue(bs.Id, out k))
                {
                    at[bs.Id] = list.Count;
                    list.Add(new KeyValuePair<ManBs, decimal>(bs, rows[i].Qty));
                    continue;
                }
                list[k] = new KeyValuePair<ManBs, decimal>(bs, list[k].Value + rows[i].Qty);
            }
            return list;
        }
    }

    // 闸门读出的一条采购入库行（rdrecords01 + RdRecord01）。SQty 是已结算数量 iSQuantity，PriorAPrice 是已有结算行的暂估金额合计。
    internal sealed class ManRd
    {
        public int Id;
        public int RdId;
        public string Code;
        public string InvCode;
        public decimal Qty;
        public decimal SQty;
        public bool HasACost;
        public decimal ACost;
        public decimal APrice;
        public decimal UCost;
        public decimal PriorAPrice;
        public Dictionary<string, object> Row;
    }

    // 闸门读出的一条采购发票行（PurBillVouchs + PurBillVouch）。Cost 是无税单价（扣税类别不是 0 时按 U8 折算）；
    // Prior* 是已有结算行的数量、金额、税额合计，PriorOffRows / PriorPairRows 是已有的红蓝发票对冲行、配对行行数。
    internal sealed class ManBs
    {
        public int Id;
        public int Pbvid;
        public string Code;
        public string InvCode;
        public decimal Qty;
        public decimal Money;
        public decimal Cost;
        public decimal Tax;
        public decimal Num;
        public decimal PriorQty;
        public decimal PriorMoney;
        public decimal PriorTax;
        public int PriorOffRows;
        public int PriorPairRows;
        public Dictionary<string, object> Row;
    }

    // 一条结算行（PurSettleVouchs）。Rd、Bs 至少有一个。
    internal sealed class ManRow
    {
        public ManLine Line;
        public ManRd Rd;
        public ManBs Bs;
        public decimal Qty;
        public decimal Money;
        public decimal Cost;
        public decimal ACost;
        public decimal APrice;
        public decimal Tax;
        public decimal Sum;
        public decimal Num;
    }

    // 同一入库行在本单里的合计（PU_SettleWriteBKRDS 的 @iRDQuan、@iSetMoney、@iRealSetMoney）。
    internal sealed class ManRdSum
    {
        public ManRd Rd;
        public decimal Qty;
        public decimal APrice;
        public decimal Money;
    }
}
