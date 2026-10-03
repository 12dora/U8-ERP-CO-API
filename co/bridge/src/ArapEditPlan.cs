using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 修改后的一行。Row 是 GetVouchData 载入的 z:row（新增行为 null）；Op 为 ""（不动）、update、delete、add。
    // Line 的金额、税率、iType 是修改后的值，Line.Fields 只含调用方这次送来、要写进 DOM 的字段。
    // AmountGiven：这次送了金额或税率，才重写金额与税额拆分；只改备注、部门等时保留 U8 行上的税额列。
    internal sealed class ArapEditRow
    {
        public object Row;
        public string Op;
        public ArapLine Line;
        public bool TypeGiven;
        public bool AmountGiven;
        public Dictionary<string, string> Refs;
        // 修改前行上的币种和金额（新增行为空），表头原币按差额调整用（ArapLineFx.ForEdit）；At 是该行在请求 lines 里的下标，-1 为不动的行。
        public string OldCurrency;
        public decimal OldAmt;
        public decimal OldAmtF;
        public int At = -1;
    }

    // Input.Head：修改后的表头（新增行的缺省和档案检查用）；Input.Lines：保留下来的行，Sum / SumF 是它们的合计。
    internal sealed class ArapEditPlan
    {
        public ArapInput Input;
        public Dictionary<string, string> HeadSet;
        public List<ArapEditRow> Rows;
        public string OldDate;
        // 载入时的表头本币、原币合计（外币单据只改备注等时原样保留）。
        public decimal OldSum;
        public decimal OldSumF;
    }

    internal static class ArapEditBuild
    {
        const string HeadRefs = "cdwcode,cdeptcode,cperson,csscode,ccode,citem_class,citemcode,cdigest";
        const string CloseRefs = "ckm,cdepcode,cpersoncode,cxmclass,cxm";
        const string VouchRefs = "ccode,cdeptcode,cperson,citem_class,citemcode";

        // home：账套本位币（WorkContext.HomeCurrency）。
        public static ArapEditPlan Build(ArapSpec spec, VoucherKind kind, ArapEditReq req, object headRow, List<object> body, string home)
        {
            ArapEditPlan plan = new ArapEditPlan();
            plan.HeadSet = req.Head;
            plan.Input = HeadInput(req, headRow, plan, home);
            plan.Rows = new List<ArapEditRow>();
            Dictionary<int, ArapEditOp> byId = Targets(req, body, kind.LineIdColumn);
            for (int i = 0; i < body.Count; i++)
            {
                ArapEditOp op;
                byId.TryGetValue(LineId(body[i], kind.LineIdColumn), out op);
                ArapEditRow r = Existing(spec, plan.Input, body[i], op);
                r.At = op == null ? -1 : req.Ops.IndexOf(op);
                plan.Rows.Add(r);
            }
            for (int i = 0; i < req.Ops.Count; i++)
            {
                if (req.Ops[i].Op == "add")
                {
                    ArapEditRow r = Added(spec, plan.Input, req.Ops[i]);
                    r.At = i;
                    plan.Rows.Add(r);
                }
            }
            Totals(plan);
            return plan;
        }

        // 档案检查（ArapRefs.Check）的输入：修改后的表头和保留行的科目、部门、业务员、项目。
        public static ArapInput CheckInput(ArapEditPlan plan)
        {
            ArapInput check = new ArapInput();
            check.Head = plan.Input.Head;
            check.Date = plan.Input.Date;
            check.Currency = plan.Input.Currency;
            check.Rate = plan.Input.Rate;
            check.Lines = new List<ArapLine>();
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                if (plan.Rows[i].Op != "delete")
                {
                    ArapLine line = new ArapLine();
                    line.Fields = plan.Rows[i].Refs;
                    check.Lines.Add(line);
                }
            }
            return check;
        }

        static ArapInput HeadInput(ArapEditReq req, object headRow, ArapEditPlan plan, string home)
        {
            ArapInput input = new ArapInput();
            input.Home = home;
            input.Head = Pick(headRow, HeadRefs);
            foreach (KeyValuePair<string, string> pair in req.Head)
            {
                input.Head[pair.Key] = pair.Value;
            }
            plan.OldDate = Day(DomRows.Get(headRow, "dVouchDate"));
            string date;
            input.Date = req.Head.TryGetValue("dvouchdate", out date) ? date : plan.OldDate;
            string currency = DomRows.Get(headRow, "cexch_name").Trim();
            input.Currency = currency.Length == 0 ? home : currency;
            input.Rate = Rate(DomRows.Get(headRow, "iExchRate"));
            plan.OldSum = Dec(DomRows.Get(headRow, "iAmount"));
            plan.OldSumF = Dec(DomRows.Get(headRow, "iAmount_f"));
            if (input.Rate <= 0m)
            {
                if (ArapReq.IsForeign(input))
                {
                    throw new BridgeException(409, "state_mismatch", "单据汇率无效，不能修改");
                }
                input.Rate = 1m;
            }
            input.Lines = new List<ArapLine>();
            return input;
        }

        static Dictionary<int, ArapEditOp> Targets(ArapEditReq req, List<object> body, string lineCol)
        {
            HashSet<int> have = new HashSet<int>();
            for (int i = 0; i < body.Count; i++)
            {
                have.Add(LineId(body[i], lineCol));
            }
            Dictionary<int, ArapEditOp> byId = new Dictionary<int, ArapEditOp>();
            for (int i = 0; i < req.Ops.Count; i++)
            {
                ArapEditOp op = req.Ops[i];
                if (op.Op == "add")
                {
                    continue;
                }
                if (!have.Contains(op.LineId))
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在");
                }
                byId[op.LineId] = op;
            }
            return byId;
        }

        static ArapEditRow Existing(ArapSpec spec, ArapInput input, object row, ArapEditOp op)
        {
            ArapEditRow r = new ArapEditRow();
            r.Row = row;
            r.Op = op == null ? "" : op.Op;
            ArapLine line = Loaded(spec, row, r.Op == "update" ? op.Fields : null);
            r.Line = line;
            r.OldCurrency = line.Currency;
            r.OldAmt = line.Amt;
            r.OldAmtF = line.AmtF;
            if (r.Op == "update")
            {
                Revise(spec, input, r);
            }
            r.Refs = Pick(row, spec.Close ? CloseRefs : VouchRefs);
            foreach (KeyValuePair<string, string> pair in line.Fields)
            {
                r.Refs[pair.Key] = pair.Value;
            }
            return r;
        }

        // 载入行上现有的金额、税率、iType；Fields 是这次要改的字段（不改为空）。
        static ArapLine Loaded(ArapSpec spec, object row, Dictionary<string, string> fields)
        {
            ArapLine line = new ArapLine();
            line.Fields = fields == null
                ? new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
                : new Dictionary<string, string>(fields, StringComparer.OrdinalIgnoreCase);
            line.Amt = Dec(DomRows.Get(row, spec.Close ? "iAmt" : "iAmount"));
            line.AmtF = Dec(DomRows.Get(row, spec.Close ? "iAmt_f" : "iAmount_f"));
            line.TaxRate = spec.Close ? 0m : Dec(DomRows.Get(row, "iTaxRate"));
            line.Type = spec.Close && DomRows.Get(row, "iType").Trim() == "1" ? 1 : 0;
            // 载入形态的标记：外币单据里行币种不同于表头时 AmtF 是本币（ArapLineFx.LoadedOrig 折回原币）。
            line.Currency = spec.Close ? null : DomRows.Get(row, "cexch_name").Trim();
            return line;
        }

        // 修改行：给了金额才按新增的规则重算（本位币本币必填；外币原币必填、本币可折算）；税率、iType 给了才换。
        static void Revise(ArapSpec spec, ArapInput input, ArapEditRow r)
        {
            ArapLine line = r.Line;
            Dictionary<string, string> f = line.Fields;
            string local = spec.Close ? ArapReq.Take(f, "iamt") : ArapReq.OneOf(f, "iamount", "iamt");
            string foreign = spec.Close ? ArapReq.Take(f, "iamt_f") : ArapReq.OneOf(f, "iamount_f", "iamt_f");
            if (local.Length > 0 || foreign.Length > 0)
            {
                ArapReq.Amounts(input, line, local, foreign, spec.Close ? "iAmt" : "iAmount");
                line.Currency = null;
                r.AmountGiven = true;
            }
            string rate = ArapReq.Take(f, "itaxrate");
            if (rate.Length > 0)
            {
                line.TaxRate = ArapReq.TaxRate(rate);
                r.AmountGiven = true;
            }
            string type = ArapReq.Take(f, "itype");
            if (type.Length > 0)
            {
                line.Type = ArapReq.LineType(type);
                r.TypeGiven = true;
            }
        }

        // 新旧单据日期是否跨月（按 yyyy-MM 比）。
        public static bool CrossMonth(ArapEditPlan plan)
        {
            return string.CompareOrdinal(plan.OldDate, 0, plan.Input.Date, 0, 7) != 0;
        }

        // 修改后单据日期的月份（Ap_CloseBill.iPeriod 的值）。
        public static int NewMonth(ArapEditPlan plan)
        {
            return int.Parse(plan.Input.Date.Substring(5, 2), CultureInfo.InvariantCulture);
        }

        static ArapEditRow Added(ArapSpec spec, ArapInput input, ArapEditOp op)
        {
            ArapEditRow r = new ArapEditRow();
            r.Op = "add";
            r.Line = ArapReq.LineFor(spec, input, op.Raw);
            r.Refs = new Dictionary<string, string>(r.Line.Fields, StringComparer.OrdinalIgnoreCase);
            return r;
        }

        static void Totals(ArapEditPlan plan)
        {
            ArapInput input = plan.Input;
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                ArapEditRow r = plan.Rows[i];
                if (r.Op == "delete")
                {
                    continue;
                }
                input.Sum += r.Line.Amt;
                input.SumF += r.Line.AmtF;
                input.Lines.Add(r.Line);
            }
            if (input.Lines.Count == 0)
            {
                throw new BridgeException(400, "bad_request", "不能删除全部明细");
            }
        }

        static Dictionary<string, string> Pick(object row, string csv)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            string[] keys = csv.Split(',');
            for (int i = 0; i < keys.Length; i++)
            {
                string value = DomRows.Get(row, keys[i]).Trim();
                if (value.Length > 0)
                {
                    map[keys[i]] = value;
                }
            }
            return map;
        }

        static int LineId(object row, string lineCol)
        {
            int id = CoRows.AsId(DomRows.Get(row, lineCol).Trim());
            if (id <= 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 返回的明细行缺少主键");
            }
            return id;
        }

        // DOM 里的日期可能带时间（yyyy-MM-ddTHH:mm:ss），取前 10 位。
        static string Day(string text)
        {
            string day = text == null ? "" : text.Trim();
            if (day.Length < 10)
            {
                throw new BridgeException(409, "u8_rejected", "U8 返回的单据日期无法识别");
            }
            day = day.Substring(0, 10);
            ArapReq.CheckDate(day);
            return day;
        }

        // iExchRate 是 float 列，读出来是 7.0999999999999996 这类二进制近似值；按 double 的 15 位有效数字还原成 7.1，
        // 与 U8 自己算 round(原币 × 汇率, 2) 时用的值一致（实测按近似值算，2.15 × 汇率得 15.26，U8 要 15.27，报「借贷不平」）。
        internal static decimal Rate(string text)
        {
            decimal value = Dec(text);
            return value == 0m ? 0m : (decimal)(double)value;
        }

        internal static decimal Dec(string text)
        {
            string raw = text == null ? "" : text.Trim();
            if (raw.Length == 0)
            {
                return 0m;
            }
            decimal value;
            if (!decimal.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                throw new BridgeException(409, "u8_rejected", "U8 返回的金额无法识别 " + raw);
            }
            return value;
        }
    }
}
