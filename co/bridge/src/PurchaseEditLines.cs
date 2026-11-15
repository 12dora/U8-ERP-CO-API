using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static partial class PurchaseEdit
    {
        static void FillNewLines(object body, List<Dictionary<string, string>> lines, PuDraft draft)
        {
            List<object> existing = DomRows.RowsOf(body);
            if (existing != null && existing.Count > 0)
            {
                throw new BridgeException(500, "internal", "空白模板行数不正确");
            }
            List<string> schema = DomRows.Schema(body);
            for (int i = 0; i < lines.Count; i++)
            {
                object row = DomRows.AddRow(body);
                Paint(draft, Mark(Stroke(body, row, null, lines[i], true, "A"), schema));
                DomRows.Set(body, row, "ivouchrowno", (i + 1).ToString(CultureInfo.InvariantCulture), schema);
            }
        }

        static void ApplyUpdate(WorkContext ctx, object[] doms, int id, Dictionary<string, string> headFields, List<PuOp> ops)
        {
            object headRow = OnlyRow(doms[0]);
            ApplyHead(doms[0], headRow, headFields);
            StampEdit(ctx, doms[0], headRow);
            PuDraft draft = new PuDraft();
            draft.Conn = ctx.Conn;
            draft.Tax = TaxOf(headRow, headFields);
            draft.Arrive = ArriveOf(headRow, headFields);
            draft.Exch = ExchOf(headRow, headFields);
            draft.FlatChanged = PuFields.Contains(headFields, "nflat");
            // 已有订单上新增的行先跟订单表头已存的税率；表头税率为空（且请求没给）才取存货档案税率。
            draft.InvTax = !PuFields.Has(headFields, "itaxrate") && Clean(DomRows.Get(headRow, "itaxrate")).Length == 0;
            draft.PoId = id.ToString(CultureInfo.InvariantCulture);
            draft.Code = Clean(DomRows.Get(headRow, "cpoid"));
            ApplyLines(doms[1], ops, draft);
        }

        static void ApplyLines(object body, List<PuOp> ops, PuDraft draft)
        {
            List<object> rows = DomRows.RowsOf(body);
            if (rows == null)
            {
                rows = new List<object>();
            }
            if (rows.Count == 0)
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回的表体为空");
            }
            CheckOps(rows, ops);
            List<string> schema = DomRows.Schema(body);
            for (int i = 0; i < rows.Count; i++)
            {
                Touch(body, rows[i], ops, draft, schema);
            }
            int no = NextNo(body);
            for (int i = 0; i < ops.Count; i++)
            {
                if (ops[i].Op != "add")
                {
                    continue;
                }
                object row = DomRows.AddRow(body);
                Paint(draft, Mark(Stroke(body, row, null, ops[i].Fields, true, "A"), schema));
                DomRows.Set(body, row, "ivouchrowno", no.ToString(CultureInfo.InvariantCulture), schema);
                DomRows.Set(body, row, "poid", draft.PoId, schema);
                if (draft.Code != null && draft.Code.Length > 0)
                {
                    DomRows.Set(body, row, "cpoid", draft.Code, schema);
                }
                ClearNew(body, row, schema);
                no++;
            }
        }

        static void Touch(object body, object row, List<PuOp> ops, PuDraft draft, List<string> schema)
        {
            PuOp hit = FindOp(ops, LineId(row));
            if (hit != null && hit.Op == "delete")
            {
                DomRows.Set(body, row, "editprop", "D", schema);
                return;
            }
            if (hit != null)
            {
                Paint(draft, Mark(Stroke(body, row, row, hit.Fields, NeedUnit(hit.Fields), "M"), schema));
                return;
            }
            if (draft.FlatChanged)
            {
                Paint(draft, Mark(Stroke(body, row, row, null, false, "M"), schema));
                return;
            }
            DomRows.Set(body, row, "editprop", "", schema);
        }

        static PuPaint Mark(PuPaint paint, List<string> schema)
        {
            paint.Schema = schema;
            return paint;
        }

        static bool NeedUnit(Dictionary<string, string> fields)
        {
            if (fields == null)
            {
                return false;
            }
            return PuFields.Has(fields, "cinvcode") || PuFields.Contains(fields, "iquantity")
                || PuFields.Contains(fields, "cunitid") || PuFields.Contains(fields, "inum");
        }

        static void Paint(PuDraft draft, PuPaint paint)
        {
            if (paint.Schema == null)
            {
                paint.Schema = DomRows.Schema(paint.Dom);
            }
            Dictionary<string, string> calc = Working(paint.Source, paint.Fields, draft.Tax, draft.Arrive);
            InvTax(draft, paint, calc);
            if (paint.Units)
            {
                bool hadUnit = PuFields.Contains(calc, "cunitid") || PuFields.Contains(calc, "inum");
                DropStaleUnit(paint.Source, calc, paint.Fields);
                PuFields.ApplyUnit(draft.Conn, calc, paint.Fields);
                if (hadUnit && !PuFields.Contains(calc, "cunitid") && !PuFields.Contains(calc, "inum"))
                {
                    ClearUnit(paint.Dom, paint.Row, paint.Schema);
                }
            }
            PuCalc.Apply(calc, draft.Exch);
            WriteRow(paint.Dom, paint.Row, calc, paint.Schema);
            DomRows.Set(paint.Dom, paint.Row, "editprop", paint.Prop, paint.Schema);
        }

        // 新增行没给 ipertaxrate、请求表头也没给 itaxrate（修改时订单表头已存的税率也为空）时，税率取存货档案的
        // iTaxRate（同请购单、无来源采购入库）；档案没填仍跟表头税率。
        static void InvTax(PuDraft draft, PuPaint paint, Dictionary<string, string> calc)
        {
            if (!draft.InvTax || paint.Source != null || PuFields.Has(paint.Fields, "ipertaxrate"))
            {
                return;
            }
            string inv = Pick(calc, "cinvcode", "").Trim();
            if (inv.Length == 0)
            {
                return;
            }
            decimal rate;
            if (PuCalc.TryDec(Rows.Scalar(draft.Conn, PuCalc.InvTaxSql, new object[] { inv }), out rate) && rate >= 0m && rate < 1000m)
            {
                calc["ipertaxrate"] = PuCalc.Text(rate);
            }
        }

        static PuPaint Stroke(object dom, object row, object source, Dictionary<string, string> fields, bool units, string prop)
        {
            PuPaint paint = new PuPaint();
            paint.Dom = dom;
            paint.Row = row;
            paint.Source = source;
            paint.Fields = fields;
            paint.Units = units;
            paint.Prop = prop;
            return paint;
        }

        static Dictionary<string, string> Working(object row, Dictionary<string, string> fields, string tax, string arrive)
        {
            Dictionary<string, string> calc = FreshCalc(row);
            FillMissing(calc, "ipertaxrate", Blank(tax));
            FillMissing(calc, "darrivedate", Blank(arrive));
            CopyFields(calc, fields);
            StampTax(calc, fields);
            return calc;
        }

        static Dictionary<string, string> FreshCalc(object row)
        {
            if (row == null)
            {
                return new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            }
            return FromRow(row);
        }

        static string Blank(string text)
        {
            if (text == null)
            {
                return "";
            }
            return text;
        }

        static void FillMissing(Dictionary<string, string> calc, string key, string value)
        {
            if (!PuFields.Has(calc, key) && value.Length > 0)
            {
                calc[key] = value;
            }
        }

        static void CopyFields(Dictionary<string, string> calc, Dictionary<string, string> fields)
        {
            if (fields == null)
            {
                return;
            }
            foreach (KeyValuePair<string, string> pair in fields)
            {
                calc[pair.Key] = pair.Value;
            }
        }

        static void StampTax(Dictionary<string, string> calc, Dictionary<string, string> fields)
        {
            if (fields != null && PuFields.Has(fields, "itaxprice"))
            {
                calc["btaxcost"] = "1";
                return;
            }
            if (fields != null && PuFields.Has(fields, "iunitprice"))
            {
                calc["btaxcost"] = "0";
                return;
            }
            if (!PuFields.Has(calc, "btaxcost"))
            {
                calc["btaxcost"] = "0";
            }
        }

        static void CheckOps(List<object> rows, List<PuOp> ops)
        {
            int deletes = 0;
            int adds = 0;
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < ops.Count; i++)
            {
                if (ops[i].Op == "add")
                {
                    adds++;
                    continue;
                }
                if (!seen.Add(ops[i].Id))
                {
                    throw new BridgeException(400, "bad_request", "明细行重复");
                }
                if (FindRow(rows, ops[i].Id) == null)
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在");
                }
                if (ops[i].Op == "delete")
                {
                    deletes++;
                }
            }
            if (deletes > 0 && rows.Count - deletes + adds < 1)
            {
                throw new BridgeException(400, "bad_request", "不能删除全部明细");
            }
        }

        // 空的存货或辅计量当作没传，避免被当成修改或必填失败。
        static void DropBlankCodes(object[] lines)
        {
            if (lines == null)
            {
                return;
            }
            for (int i = 0; i < lines.Length; i++)
            {
                DropCode(lines[i] as Dictionary<string, object>, "cinvcode");
                DropCode(lines[i] as Dictionary<string, object>, "cassunit");
            }
        }

        static void DropCode(Dictionary<string, object> map, string name)
        {
            if (map == null)
            {
                return;
            }
            string found = BlankKey(map, name);
            if (found != null)
            {
                map.Remove(found);
            }
        }

        static string BlankKey(Dictionary<string, object> map, string name)
        {
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (!string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                if (BlankValue(pair.Value))
                {
                    return pair.Key;
                }
                return null;
            }
            return null;
        }

        static bool BlankValue(object value)
        {
            if (value == null || value is DBNull)
            {
                return true;
            }
            string text = value as string;
            if (text == null)
            {
                return false;
            }
            return text.Trim().Length == 0;
        }
    }

    sealed class PuPaint
    {
        public object Dom;
        public object Row;
        public object Source;
        public Dictionary<string, string> Fields;
        public bool Units;
        public string Prop;
        public List<string> Schema;
    }
}
