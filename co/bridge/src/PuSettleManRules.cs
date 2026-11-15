using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购手工结算的状态规则（PuSettleManGate 读出入库行、发票行之后调用；纯函数，--selftest 直接测）。
    // 主键不存在 400（明细行不存在），状态不符 409 state_mismatch。规则见 PuSettleManGate 的类注释。
    internal static class PuSettleManRules
    {
        internal static void Check(ManPlan plan, Dictionary<int, ManRd> rds, Dictionary<int, ManBs> bss)
        {
            string ven = null;
            for (int i = 0; i < plan.Lines.Count; i++)
            {
                ManLine line = plan.Lines[i];
                ManRd rd = Find(rds, line.InId, line, PuSettleManReq.InKey, "采购入库单行");
                ManBs bs = Find(bss, line.BsId, line, PuSettleManReq.BillKey, "采购发票行");
                Decimals(line, plan.QuanDec);
                if (rd != null)
                {
                    GateRd(rd, line, plan.Date);
                    ven = SameVendor(ven, CoRows.Col(rd.Row, "cVenCode"));
                }
                if (bs != null)
                {
                    GateBs(bs, line, plan.Date);
                    ven = SameVendor(ven, CoRows.Col(bs.Row, "cVenCode"));
                }
                if (rd != null && bs != null && !SameCode(rd.InvCode, bs.InvCode))
                {
                    throw State(Where(line) + "入库行与发票行不是同一存货");
                }
            }
            Remaining(plan.Lines, rds, bss);
            Netting(plan.Lines, rds, bss);
            Mixing(plan.Lines, bss);
        }

        // 同一发票行不混用红蓝发票对冲（没有入库行，税额 0）和配对（税额按数量分摊、结清那一笔取余额）：混用时税额怎么分
        // 没有依据（对冲占的数量不带税，余额会全落到配对行上），一律拒绝。本单里混用 400；与已有结算混用 409。
        static void Mixing(List<ManLine> lines, Dictionary<int, ManBs> bss)
        {
            Dictionary<int, bool> pairOf = new Dictionary<int, bool>();
            for (int i = 0; i < lines.Count; i++)
            {
                ManLine line = lines[i];
                if (line.BsId == 0)
                {
                    continue;
                }
                bool pair = line.InId > 0;
                bool seen;
                if (pairOf.TryGetValue(line.BsId, out seen) && seen != pair)
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Item("lines", line.Index), PuSettleManReq.BillKey),
                        "同一发票行不能在一张结算单里既做红蓝发票对冲又与入库行配对（税额无法分摊），请分开结算");
                }
                pairOf[line.BsId] = pair;
                ManBs bs = bss[line.BsId];
                if (pair ? bs.PriorOffRows > 0 : bs.PriorPairRows > 0)
                {
                    throw State(Where(line) + (pair ? "发票行已做过红蓝发票对冲，不能再与入库行配对结算（税额无法分摊）"
                        : "发票行已与入库行配对结算过，不能再做红蓝发票对冲（税额无法分摊）"));
                }
            }
        }

        static T Find<T>(Dictionary<int, T> map, int id, ManLine line, string key, string what) where T : class
        {
            if (id <= 0)
            {
                return null;
            }
            T found;
            if (!map.TryGetValue(id, out found))
            {
                throw BridgeException.BadField(FieldPath.Join(FieldPath.Item("lines", line.Index), key), what + "不存在");
            }
            return found;
        }

        static void Decimals(ManLine line, int quanDec)
        {
            if (PuSettleManPlan.Round(line.Qty, quanDec) != line.Qty)
            {
                throw BridgeException.BadField(FieldPath.Join(FieldPath.Item("lines", line.Index), PuSettleManReq.QtyKey),
                    "quantity 的小数位超过账套的数量位数（" + quanDec.ToString(CultureInfo.InvariantCulture) + "）");
            }
            if (line.HasAmount && PuSettleManPlan.Money(line.Amount) != line.Amount)
            {
                throw BridgeException.BadField(FieldPath.Join(FieldPath.Item("lines", line.Index), PuSettleManReq.AmountKey),
                    "amount 最多两位小数");
            }
        }

        static void GateRd(ManRd rd, ManLine line, string date)
        {
            Dictionary<string, object> row = rd.Row;
            if (CoRows.Col(row, "cBusType") != "普通采购")
            {
                throw State(Where(line) + "只支持普通采购的入库单结算");
            }
            if (CoRows.Col(row, "cHandler").Length == 0)
            {
                throw State(Where(line) + "采购入库单未审核");
            }
            if (rd.Qty == 0m || Math.Sign(line.Qty) != Math.Sign(rd.Qty))
            {
                throw State(Where(line) + "数量的正负与入库行不一致");
            }
            if (string.CompareOrdinal(date, CoRows.Col(row, "RdDate")) < 0)
            {
                throw State(Where(line) + "结算日期早于入库日期");
            }
        }

        static void GateBs(ManBs bs, ManLine line, string date)
        {
            Dictionary<string, object> row = bs.Row;
            if (CoRows.Col(row, "cBusType") != "普通采购")
            {
                throw State(Where(line) + "只支持普通采购的发票结算");
            }
            if (CoRows.Col(row, "First") != "0" || CoRows.Col(row, "Orig") != "0")
            {
                throw State(Where(line) + "期初发票不支持结算");
            }
            if (CoRows.Col(row, "Paid") != "0")
            {
                throw State(Where(line) + "现付发票不支持结算");
            }
            if (!PuSettleManGate.IsRmb(CoRows.Col(row, "Exch")))
            {
                throw State(Where(line) + "只支持人民币发票结算");
            }
            GateBill(bs, line, date);
        }

        static void GateBill(ManBs bs, ManLine line, string date)
        {
            Dictionary<string, object> row = bs.Row;
            string type = CoRows.Col(row, "cPBVBillType");
            if (type != "01" && type != "02")
            {
                throw State(Where(line) + "只支持采购专用发票和普通发票");
            }
            if (CoRows.Col(row, "cVerifier").Length == 0)
            {
                throw State(Where(line) + "发票未复核");
            }
            if (CoRows.Col(row, "cPBVVerifier").Length > 0)
            {
                throw State(Where(line) + "发票已应付审核，请先取消应付审核");
            }
            if (CoRows.Col(row, "UpSo") != "RD" && CoRows.Col(row, "UpSo").Length > 0)
            {
                throw State(Where(line) + "只支持参照采购入库单开具的发票行");
            }
            if (bs.Qty == 0m || Math.Sign(line.Qty) != Math.Sign(bs.Qty))
            {
                throw State(Where(line) + "数量的正负与发票行不一致");
            }
            if (string.CompareOrdinal(date, CoRows.Col(row, "BillDate")) < 0)
            {
                throw State(Where(line) + "结算日期早于发票日期");
            }
        }

        // 同一入库行 / 发票行在本单里的数量合计不超过它的未结算数量（同号，按绝对值比）。
        static void Remaining(List<ManLine> lines, Dictionary<int, ManRd> rds, Dictionary<int, ManBs> bss)
        {
            Dictionary<int, decimal> rdQty = new Dictionary<int, decimal>();
            Dictionary<int, decimal> bsQty = new Dictionary<int, decimal>();
            for (int i = 0; i < lines.Count; i++)
            {
                ManLine line = lines[i];
                if (line.InId > 0 && Over(rdQty, line.InId, line.Qty, rds[line.InId].Qty - rds[line.InId].SQty))
                {
                    throw State(Where(line) + "结算数量超过入库行的未结算数量");
                }
                if (line.BsId > 0 && Over(bsQty, line.BsId, line.Qty, bss[line.BsId].Qty - bss[line.BsId].PriorQty))
                {
                    throw State(Where(line) + "结算数量超过发票行的未结算数量");
                }
            }
        }

        static bool Over(Dictionary<int, decimal> sums, int id, decimal qty, decimal left)
        {
            decimal sum;
            sums.TryGetValue(id, out sum);
            sum += qty;
            sums[id] = sum;
            return Math.Abs(sum) > Math.Abs(left) + PuSettleManPlan.Eps || (left != 0m && Math.Sign(left) != Math.Sign(sum))
                || left == 0m;
        }

        // 红蓝入库对冲行（只有入库行）、红蓝发票对冲行（只有发票行）各按存货合计数量为 0。
        static void Netting(List<ManLine> lines, Dictionary<int, ManRd> rds, Dictionary<int, ManBs> bss)
        {
            Dictionary<string, decimal> rdNet = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, decimal> bsNet = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < lines.Count; i++)
            {
                ManLine line = lines[i];
                if (line.BsId == 0)
                {
                    Add(rdNet, rds[line.InId].InvCode, line.Qty);
                }
                else if (line.InId == 0)
                {
                    Add(bsNet, bss[line.BsId].InvCode, line.Qty);
                }
            }
            RequireZero(rdNet, "红蓝入库对冲");
            RequireZero(bsNet, "红蓝发票对冲");
        }

        static void Add(Dictionary<string, decimal> net, string code, decimal qty)
        {
            string key = (code ?? "").Trim();
            decimal sum;
            net.TryGetValue(key, out sum);
            net[key] = sum + qty;
        }

        static void RequireZero(Dictionary<string, decimal> net, string what)
        {
            foreach (KeyValuePair<string, decimal> pair in net)
            {
                if (!PuSettleManPlan.Same(pair.Value, 0m))
                {
                    throw State(what + "的行在存货 " + pair.Key + " 上数量合计不为 0（" + pair.Value.ToString(CultureInfo.InvariantCulture) + "）");
                }
            }
        }

        static string SameVendor(string ven, string next)
        {
            string code = (next ?? "").Trim();
            if (ven != null && !string.Equals(ven, code, StringComparison.OrdinalIgnoreCase))
            {
                throw State("入库单、发票不是同一供应商（" + ven + "、" + code + "）");
            }
            return code;
        }

        static bool SameCode(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        static string Where(ManLine line)
        {
            return "第 " + (line.Index + 1).ToString(CultureInfo.InvariantCulture) + " 行：";
        }

        static BridgeException State(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
