using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 退货单的红字 DOM。实测：自己拼的红字行（拷原发货行或订单参照行、挂 isosid）U8 Save 报
    // 「第1行存货…退货数量不能大于应发货数量!」；VT 10 的 GetNegaVouchData 做出的红字行不挂订单行，
    // 表体 autoid / autoid2 / dlid 是原发货单的主键，数量是原行的全数（负）。
    internal static partial class SaleGen
    {
        // 未覆盖：getDefaltVTID 按卡片 03 没给模板时的退货单模板号。
        const string RedVtidFallback = "75";
        // 实测（XE 跟踪）：挂上 isosid / csocode / cordercode / iorderrowno 后 U8 回写订单行 fretquantity、iFHQuantity。
        // false 时不挂订单行（同 GetNegaVouchData 的原样），此时不核对订单行回写。
        static readonly bool RetLinkSo = true;
        // 实测保存成功的 DOM 上这三列写 0（原行的累计不能带到新行）。
        static readonly string[] RetLineZero = new string[] { "iretquantity", "fretqtywkp", "fretqtyykp" };
        static readonly string[] RetHeadClear = new string[] {
            "ufts", "cverifier", "dverifydate", "dverifysystime", "ccloser", "dhclosedate", "dhclosesystime",
            "cmodifier", "dmoddate", "dmodifysystime", "dcreatesystime", "sbvid", "csbvcode", "iprintcount",
            "ccurrentauditor", "iverifystate", "ireturncount", "cchanger", "cchangememo", "caccounter",
            "csysbarcode", "outid", "iflowid", "cflowname"
        };
        // 原单主键、时间戳和各种累计（出库、开票、退货、签收、质检、收款）。
        static readonly string[] RetLineClear = new string[] {
            "autoid", "autoid2", "idlsid", "dlid", "ufts", "corufts", "cbsysbarcode", "cbarcode", "body_outid",
            "isettlequantity", "isettlenum", "foutquantity", "foutnum", "iretquantity", "fsumsignquantity",
            "fsumsignnum", "fensettlequan", "fensettlesum", "fretqtywkp", "fretqtyykp", "fretsum", "fretykpsum",
            "iqaquantity", "iqanum", "cconfirmer", "dconfirmdate", "isaleoutid", "icoridlsid", "fxjquantity",
            "fretqtywkp", "fretqtyykp",
            "fxjnum", "imoneysum", "iexchsum", "fsumpaymoney", "cscloser", "dsclosedate", "dsclosesystime"
        };
        static readonly string[] RetSoLink = new string[] { "isosid", "csocode", "cordercode", "iorderrowno" };
        // 按数量比例缩放的列（原行全数 → 本次退货数）。未覆盖：funsign* 是否由 U8 自己重算。
        static readonly string[] RetScaled = new string[] {
            "inum", "imoney", "itax", "isum", "inatmoney", "inattax", "inatsum", "idiscount", "inatdiscount",
            "funsignquantity", "funsignnum"
        };
        // 原行主键可能出现在这些列上，按顺序找。
        static readonly string[] RetOrigKeys = new string[] { "autoid2", "idlsid", "autoid" };

        static void NegaDoms(WorkContext ctx, object co, object[] doms, int sourceId)
        {
            object[] args = new object[] { "05", doms[0], doms[1], sourceId, "", true };
            object ret = ComUtil.CallRef(co, "GetNegaVouchData", args, new int[] { 0, 1, 2, 3, 4, 5 });
            CoRows.Swap(doms, 0, args[1]);
            CoRows.Swap(doms, 1, args[2]);
            string err = Values.Text(args[4]).Trim();
            CoRows.Note(ctx.Item, "GetNegaVouchData " + Values.Text(ret) + " " + err);
            if (!Values.Flag(ret))
            {
                throw new BridgeException(409, "u8_rejected", err.Length > 0 ? err : "U8 没有生成红字发货单");
            }
            if (DomRows.RowsOf(doms[0]).Count < 1)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有生成红字发货单");
            }
        }

        // 只留请求的行（按原发货行主键），顺序照请求。
        static void FillReturnLines(RetJob job, object co, object[] doms)
        {
            Dictionary<int, RetLine> want = new Dictionary<int, RetLine>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                want[job.Lines[i].LineId] = job.Lines[i];
            }
            List<RetLine> order = KeepRequested(doms[1], want);
            for (int i = 0; i < order.Count; i++)
            {
                FillReturnLine(job, co, doms, order[i], i);
            }
        }

        static List<RetLine> KeepRequested(object body, Dictionary<int, RetLine> want)
        {
            List<RetLine> order = new List<RetLine>();
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                RetLine line = MatchLine(rows[i], want);
                if (line == null || order.Contains(line))
                {
                    DomRows.RemoveRow(rows[i]);
                    continue;
                }
                order.Add(line);
            }
            if (order.Count != want.Count)
            {
                throw new BridgeException(409, "u8_rejected", "U8 生成的红字发货单缺少请求的发货行");
            }
            return order;
        }

        static RetLine MatchLine(object row, Dictionary<int, RetLine> want)
        {
            for (int k = 0; k < RetOrigKeys.Length; k++)
            {
                RetLine line;
                if (want.TryGetValue(CoRows.AsId(DomRows.Get(row, RetOrigKeys[k])), out line))
                {
                    return line;
                }
            }
            return null;
        }

        static void FillReturnLine(RetJob job, object co, object[] doms, RetLine line, int index)
        {
            List<string> schema = DomRows.Schema(doms[1]);
            object row = At(doms[1], index);
            ClearAttrs(doms[1], row, RetLineClear, schema);
            ClearAttrs(doms[1], row, RetSoLink, schema);
            for (int i = 0; i < RetLineZero.Length; i++)
            {
                DomRows.Set(doms[1], row, RetLineZero[i], "0", schema);
            }
            ScaleNega(doms[1], row, line.Qty, schema);
            ReturnMoney(job.Ctx, co, doms, row);
            row = At(doms[1], index);
            ApplyFields(doms[1], row, line.Fields, false);
            PinReturnLine(job, doms[1], row, line, index);
        }

        static void ClearAttrs(object dom, object row, string[] names, List<string> schema)
        {
            for (int i = 0; i < names.Length; i++)
            {
                DomRows.Set(dom, row, names[i], null, schema);
            }
        }

        // 原行全数（负）→ 本次 −qty：数量写准，辅数量、金额和未签收数同比例（数量 6 位、金额 2 位）。
        static void ScaleNega(object body, object row, decimal qty, List<string> schema)
        {
            decimal full = Dec(DomRows.Get(row, "iquantity"));
            DomRows.Set(body, row, "iquantity", QtyText(-qty), schema);
            if (full == 0m)
            {
                return;
            }
            decimal ratio = -qty / full;
            if (ratio < 0m)
            {
                ratio = -ratio;
            }
            for (int i = 0; i < RetScaled.Length; i++)
            {
                decimal value;
                if (!TryDec(DomRows.Get(row, RetScaled[i]), out value))
                {
                    continue;
                }
                int places = RetScaled[i].StartsWith("i", StringComparison.Ordinal) && RetScaled[i] != "inum" ? 2 : 6;
                decimal scaled = Math.Round(value * ratio, places, MidpointRounding.AwayFromZero);
                DomRows.Set(body, row, RetScaled[i], scaled.ToString(places == 2 ? "0.00" : "0.######",
                    CultureInfo.InvariantCulture), schema);
            }
        }

        // 单行 BodyCheck：先数量，再单价键（有含税单价用它，否则无税单价）。U8 拒绝只记审计，保留按比例算出的金额。
        static void ReturnMoney(WorkContext ctx, object co, object[] doms, object row)
        {
            string msg = SaleCalc.Check(co, doms[0], doms[1], row, "iquantity", false);
            if (msg.Length > 0)
            {
                CoRows.Note(ctx.Item, "BodyCheck iquantity " + msg);
            }
            string price = DomRows.Get(row, "itaxunitprice").Trim().Length > 0 ? "itaxunitprice" : "iunitprice";
            if (DomRows.Get(row, price).Trim().Length == 0)
            {
                return;
            }
            msg = SaleCalc.Check(co, doms[0], doms[1], row, price, false);
            if (msg.Length > 0)
            {
                CoRows.Note(ctx.Item, "BodyCheck " + price + " " + msg);
            }
        }

        // 实测保存成功的 DOM：icorid = 原 iDLsID、ccorcode = 原单号，挂订单时 isosid、csocode = cordercode = 订单号、
        // iorderrowno = 订单行号。U8 按 icorid 回写原行 iRetQuantity 与 fretqtywkp / fretqtyykp。
        static void PinReturnLine(RetJob job, object body, object row, RetLine line, int index)
        {
            List<string> schema = DomRows.Schema(body);
            DomRows.Set(body, row, "icorid", Id(line.LineId), schema);
            if (job.DlCode.Length > 0)
            {
                DomRows.Set(body, row, "ccorcode", job.DlCode, schema);
            }
            // K12：参照退货申请单时挂 irtnappid / crtnappcode（SaleGenApplyRet.cs）。
            PinApply(body, row, line, schema);
            if (Linked(line))
            {
                DomRows.Set(body, row, "isosid", Id(line.SoLine), schema);
                PinSoText(body, row, "csocode", line.SoCode, schema);
                PinSoText(body, row, "cordercode", line.SoCode, schema);
                PinSoText(body, row, "iorderrowno", line.SoRow, schema);
            }
            DomRows.Set(body, row, "irowno", (index + 1).ToString(CultureInfo.InvariantCulture), schema);
            DomRows.Set(body, row, "editprop", "A", schema);
        }

        static bool Linked(RetLine line)
        {
            return RetLinkSo && line.SoLine > 0;
        }

        static void PinSoText(object body, object row, string name, string value, List<string> schema)
        {
            if (value != null && value.Length > 0)
            {
                DomRows.Set(body, row, name, value, schema);
            }
        }
    }
}
