using System;
using System.Collections.Generic;

namespace U8Co
{
    // 销售发票参照发货单的来源核对（单张是 n = 1 的同一路径）：每张来源都是蓝字（cVouchType 05、非退货）、非期初、
    // 已审核、未关闭；多张时必须同一客户 cCusCode、同一业务类型 cBusType、同一币种 cexch_name、同一汇率 iExchRate、
    // 同一表头税率 iTaxRate（发票表头只有一套，照第一张来源抄）。行：属于所在来源发货单、请求内不重复、行未关闭、
    // 数量不超过该行剩余可开票数量（iQuantity - iSettleQuantity - fretqtywkp）。
    // U8 界面「参照多张发货单」另有哪些分组条件未在测试账套上实测（docs/limitations.md），这里按上列从严。
    internal static partial class SaleGen
    {
        const string InvDispSql = "select cDLCode, cSOCode, cVerifier, cVouchType, bReturnFlag, cCloser, bFirst, "
            + "cCusCode, cBusType, cexch_name, iExchRate, iTaxRate from DispatchList where DLID=?";
        // {列, 是否按数值比较, 不一致时的说明}
        static readonly string[][] InvParty = new string[][] {
            new string[] { "cCusCode", "", "客户" },
            new string[] { "cBusType", "", "业务类型" },
            new string[] { "cexch_name", "", "币种" },
            new string[] { "iExchRate", "n", "汇率" },
            new string[] { "iTaxRate", "n", "表头税率" }
        };

        static ShipLine[] PlanInvoices(object conn, InvSource[] sources, InvHead want)
        {
            List<ShipLine> plan = new List<ShipLine>();
            Dictionary<int, bool> seen = new Dictionary<int, bool>();
            Dictionary<string, object> first = null;
            for (int s = 0; s < sources.Length; s++)
            {
                Dictionary<string, object> disp = InvDisp(conn, sources[s].Id);
                if (first == null)
                {
                    first = disp;
                    want.DlCode = CoRows.Col(disp, "cDLCode");
                    want.SoCode = CoRows.Col(disp, "cSOCode");
                }
                else
                {
                    SameParty(first, disp, s);
                }
                Dictionary<int, Dictionary<string, object>> body = IndexBy(conn, DispLineSql, sources[s].Id, "iDLsID");
                object[] lines = sources[s].Lines;
                for (int i = 0; i < lines.Length; i++)
                {
                    ShipLine line = ReadInvLine(lines[i], body, seen);
                    line.DlId = sources[s].Id;
                    line.DlCode = CoRows.Col(disp, "cDLCode");
                    plan.Add(line);
                }
            }
            return plan.ToArray();
        }

        static Dictionary<string, object> InvDisp(object conn, int id)
        {
            Dictionary<string, object> disp = Rows.One(conn, InvDispSql, new object[] { id });
            if (disp == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(disp, "cVouchType") != "05" || CoRows.FlagOf(disp, "bReturnFlag"))
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字发货单");
            }
            if (CoRows.FlagOf(disp, "bFirst"))
            {
                throw new BridgeException(400, "bad_request", "不支持期初发货单");
            }
            if (CoRows.Col(disp, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(disp, "cCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            return disp;
        }

        // 纯比较（--selftest 用）：第 index 张来源与第一张的客户、业务类型、币种、汇率、表头税率逐项比较，不一致 400。
        internal static void SameParty(Dictionary<string, object> first, Dictionary<string, object> other, int index)
        {
            for (int i = 0; i < InvParty.Length; i++)
            {
                string a = CoRows.Col(first, InvParty[i][0]);
                string b = CoRows.Col(other, InvParty[i][0]);
                bool same = InvParty[i][1] == "n" ? Dec(a) == Dec(b) : string.Equals(a.Trim(), b.Trim(), StringComparison.OrdinalIgnoreCase);
                if (!same)
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Item(SaleInvSources.Field, index), "id"),
                        "参照的发货单必须是同一" + InvParty[i][2] + "：第 1 张 " + Shown(a) + "，这一张 " + Shown(b));
                }
            }
        }

        static string Shown(string value)
        {
            string text = value == null ? "" : value.Trim();
            return text.Length == 0 ? "空" : text;
        }

        static ShipLine ReadInvLine(object raw, Dictionary<int, Dictionary<string, object>> body, Dictionary<int, bool> seen)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw new BridgeException(400, "bad_request", "表体行必须是对象");
            }
            CheckKeys(map, false, InvBlueLineKeys, false);
            int lineId = CoRows.AsId(RawKey(map, "source_line_id"));
            Dictionary<string, object> src;
            if (lineId <= 0 || !body.TryGetValue(lineId, out src))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (seen.ContainsKey(lineId))
            {
                throw new BridgeException(400, "bad_request", "明细行重复");
            }
            seen[lineId] = true;
            if (CoRows.Col(src, "cSCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            decimal qty = QtyOf(RawKey(map, "quantity"));
            // 已开票加未开票退货（生成的 bneedbill=0 退货单回写 fretqtywkp）。
            if (qty > Remain(src, "Billed"))
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            ShipLine line = new ShipLine();
            line.LineId = lineId;
            line.Qty = qty;
            line.Fields = map;
            return line;
        }

        // 给 SalePins.ForInvoice：每行的来源发货行、数量和请求字段。
        static List<InvPinLine> PinLines(ShipLine[] plan)
        {
            List<InvPinLine> lines = new List<InvPinLine>();
            for (int i = 0; i < plan.Length; i++)
            {
                InvPinLine one = new InvPinLine();
                one.SrcLine = plan[i].LineId;
                one.Qty = plan[i].Qty;
                one.Fields = plan[i].Fields;
                lines.Add(one);
            }
            return lines;
        }

        // 请求用 sources 时响应另带 source_ids（按请求顺序）；source_id 是第一张。
        static ApiResult WithSources(WorkItem item, ApiResult result, InvSource[] sources)
        {
            if (item != null && item.GenSources != null && result != null && result.Body != null)
            {
                result.Body["source_ids"] = SaleInvSources.Ids(sources);
            }
            return result;
        }
    }
}
