using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购类修改的共用部分：改表头、按 editprop 改表体、重算金额、保存事务和保存后回读。
    internal static partial class PuEditMore
    {
        // 到货单、退货单的闸门（与删除相同）：审批流、已审核、已关闭、非普通采购、已报检或已入库（含已退货）。
        internal static void GateArrival(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> row)
        {
            PurchaseCo.RefuseFlow(ctx.Conn, kind, row);
            if (CoRows.Col(row, "verifier").Length > 0 || CoRows.Col(row, "verify_date").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (CoRows.Col(row, "closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            string bus = Rows.Scalar(ctx.Conn, "select cBusType from PU_ArrivalVouch where ID=?", new object[] { id });
            if (bus == null || bus.Trim() != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持修改普通采购的单据");
            }
            PurchaseCo.RefuseArrival(ctx.Conn, id, "不能修改");
        }

        // 表头：调用方字段（须在 schema 里），editprop=M，修改人、修改日期、修改时间（schema 里有才写）。
        internal static void ApplyHead(WorkContext ctx, object dom, Dictionary<string, string> fields)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                if (rows.Count != 1)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回的表头行数不是 1");
                }
                List<string> schema = DomRows.Schema(dom);
                WriteFields(dom, rows[0], fields, schema, "head");
                DomRows.Set(dom, rows[0], "editprop", "M", schema);
                string who = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
                string day = ctx.Item.Date == null || ctx.Item.Date.Trim().Length == 0
                    ? DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) : ctx.Item.Date.Trim();
                Stamp(dom, rows[0], schema, "creviser", who);
                Stamp(dom, rows[0], schema, "cmodifydate", day);
                Stamp(dom, rows[0], schema, "cmodifytime", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture));
            }
            finally
            {
                Release(rows);
            }
        }

        static void WriteFields(object dom, object row, Dictionary<string, string> fields, List<string> schema, string at)
        {
            foreach (KeyValuePair<string, string> pair in fields)
            {
                if (Named(schema, pair.Key) == null)
                {
                    throw BridgeException.BadField(FieldPath.Join(at, pair.Key), "未知字段 " + pair.Key);
                }
                DomRows.Set(dom, row, Named(schema, pair.Key), pair.Value, schema);
            }
        }

        static void Stamp(object dom, object row, List<string> schema, string name, string value)
        {
            string found = Named(schema, name);
            if (found != null && value.Length > 0)
            {
                DomRows.Set(dom, row, found, value, schema);
            }
        }

        static string Named(List<string> schema, string name)
        {
            for (int i = 0; schema != null && i < schema.Count; i++)
            {
                if (string.Equals(schema[i], name, StringComparison.OrdinalIgnoreCase))
                {
                    return schema[i];
                }
            }
            return null;
        }

        // 表体：delete 标 D，update 写字段和数量后标 M，其余行 editprop 置空。返回每行（行主键）数量减少了多少（正数；
        // 删行按原数量，没动的行 0），供保存后核对来源累计数。
        internal static Dictionary<int, decimal> ApplyLines(object body, PuEditReq req, PuEditShape shape)
        {
            List<object> rows = DomRows.RowsOf(body);
            try
            {
                if (rows.Count == 0)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回的表体为空");
                }
                CheckOps(rows, req.Lines, shape.IdAttr);
                List<string> schema = DomRows.Schema(body);
                shape.QtyName = req.QtyName;
                Dictionary<int, decimal> drops = new Dictionary<int, decimal>();
                for (int i = 0; i < rows.Count; i++)
                {
                    int id = CoRows.AsId(DomRows.Get(rows[i], shape.IdAttr));
                    drops[id] = Touch(body, rows[i], FindOp(req.Lines, id), shape, schema);
                }
                return drops;
            }
            finally
            {
                Release(rows);
            }
        }

        static void CheckOps(List<object> rows, List<PuEditLine> ops, string idAttr)
        {
            HashSet<int> ids = new HashSet<int>();
            for (int i = 0; i < rows.Count; i++)
            {
                ids.Add(CoRows.AsId(DomRows.Get(rows[i], idAttr)));
            }
            int deletes = 0;
            for (int i = 0; i < ops.Count; i++)
            {
                if (!ids.Contains(ops[i].Id))
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i) + ".line_id", "明细行不存在");
                }
                if (ops[i].Op == "delete")
                {
                    deletes++;
                }
            }
            if (rows.Count - deletes < 1)
            {
                throw BridgeException.BadField("lines", "不能删除全部明细");
            }
        }

        static PuEditLine FindOp(List<PuEditLine> ops, int id)
        {
            for (int i = 0; id > 0 && i < ops.Count; i++)
            {
                if (ops[i].Id == id)
                {
                    return ops[i];
                }
            }
            return null;
        }

        static decimal Touch(object body, object row, PuEditLine op, PuEditShape shape, List<string> schema)
        {
            decimal old = Math.Abs(PuInv.Num(DomRows.Get(row, shape.QtyName)));
            if (op == null)
            {
                DomRows.Set(body, row, "editprop", "", schema);
                return 0m;
            }
            if (op.Op == "delete")
            {
                DomRows.Set(body, row, "editprop", "D", schema);
                return old;
            }
            WriteFields(body, row, op.Fields, schema, "lines");
            decimal drop = 0m;
            if (op.HasQty)
            {
                drop = SetQty(body, row, op.Qty, old, shape, schema);
            }
            DomRows.Set(body, row, "editprop", "M", schema);
            return drop;
        }

        // 数量只能减少；红字单（Sign −1）写负数。辅计量按行上的换算率重算件数，金额按生单公式重算。
        static decimal SetQty(object body, object row, decimal qty, decimal old, PuEditShape shape, List<string> schema)
        {
            if (qty > old)
            {
                throw BridgeException.BadField("lines." + shape.QtyName, "修改只能减少数量");
            }
            if (qty == old)
            {
                return 0m;
            }
            decimal signed = shape.Sign * qty;
            decimal wasQty = PuInv.Num(DomRows.Get(row, shape.QtyName));
            string wasNum = Named(schema, "inum") == null ? "" : DomRows.Get(row, Named(schema, "inum")).Trim();
            string newQty = signed.ToString("0.######", CultureInfo.InvariantCulture);
            DomRows.Set(body, row, shape.QtyName, newQty, schema);
            decimal rate = PuInv.Num(DomRows.Get(row, "iinvexchrate"));
            string newNum = null;
            if (rate > 0m && Named(schema, "inum") != null)
            {
                newNum = StockUnits.Price(signed / rate);
                DomRows.Set(body, row, Named(schema, "inum"), newNum, schema);
            }
            if (shape.Follow)
            {
                Follow(body, row, schema, QtyFollowers, wasQty, newQty);
                if (newNum != null && wasNum.Length > 0)
                {
                    Follow(body, row, schema, NumFollowers, PuInv.Num(wasNum), newNum);
                }
            }
            shape.Amounts(body, row, schema, signed);
            return old - qty;
        }

        static readonly string[] QtyFollowers = new string[] { "frealquantity", "fvalidquantity" };
        static readonly string[] NumFollowers = new string[] { "frealnum", "fvalidnum" };

        // 到货单、退货单的实收 / 合格数量（件数同理）：载入值等于原数量（带符号）时跟着改成新值，
        // 不等（界面里单独改过）就不动。U8 录入的到货行通常 实收 = 合格 = 数量。
        // 未覆盖：VoucherSave2 是否自己维护这几列（若自己维护，这里写入的值应与 U8 一致，测试时读回核对）。
        static void Follow(object body, object row, List<string> schema, string[] names, decimal was, string now)
        {
            for (int i = 0; i < names.Length; i++)
            {
                string name = Named(schema, names[i]);
                if (name == null)
                {
                    continue;
                }
                string text = DomRows.Get(row, name).Trim();
                if (text.Length > 0 && PuInv.Num(text) == was)
                {
                    DomRows.Set(body, row, name, now, schema);
                }
            }
        }

        // 行上的价格口径，键名与 StockGen.PoAmounts 的来源行相同：含税标志、税率、原币含税单价、原币无税单价。
        internal static Dictionary<string, object> PriceOf(object row)
        {
            Dictionary<string, object> src = new Dictionary<string, object>();
            bool tax = PuCalc.Tax(DomRows.Get(row, "btaxcost"));
            src["bTaxCost"] = tax ? "1" : "0";
            src["iPerTaxRate"] = DomRows.Get(row, "itaxrate").Trim();
            src["iTaxPrice"] = DomRows.Get(row, "ioritaxcost").Trim();
            src["iUnitPrice"] = DomRows.Get(row, "ioricost").Trim();
            if (CoRows.Col(src, tax ? "iTaxPrice" : "iUnitPrice").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "明细行没有单价，不能改数量");
            }
            return src;
        }

        // names 是生单用的 {DOM 属性, PoAmounts 键} 对照；税率和含税标志不改。
        internal static void WriteAmounts(object dom, object row, List<string> schema, Dictionary<string, string> amt, string[] names)
        {
            for (int k = 0; k < names.Length; k += 2)
            {
                if (names[k] == "itaxrate" || names[k] == "btaxcost")
                {
                    continue;
                }
                StockDom.SetCell(dom, row, names[k], amt[names[k + 1]], schema);
            }
        }

        internal static string HeadText(object dom, string name)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                return rows.Count == 0 ? "" : DomRows.Get(rows[0], name).Trim();
            }
            finally
            {
                Release(rows);
            }
        }

        // VoucherSave2(h, b, 1, "<id>") 引用 {3}。Before 在保存前加锁读来源累计数，After 在提交前核对；
        // U8 已自行提交时核对失败由 StockCall.AfterCheck 报 504（要人工核对）。
        internal static void SaveTran(WorkContext ctx, object co, object[] doms, int id, PuEditCheck check)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                check.Before(ctx.Conn);
                object[] args = new object[] { doms[0], doms[1], (short)1, id.ToString(CultureInfo.InvariantCulture) };
                object ret = ComUtil.CallRef(co, "VoucherSave2", args, new int[] { 3 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = Values.Text(ret).Trim();
                CoRows.Note(ctx.Item, "VoucherSave2 " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { check.After(conn); }, check.Label);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // 已提交；新连接上回读失败一律 504，调用方不要重投。
        internal static ApiResult AfterSaved(WorkContext ctx, VoucherKind kind, int id)
        {
            try
            {
                return EditMsg.Saved(ctx, kind, id, null, 0);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "已保存但回读失败，需要人工核对：标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
        }

        // 按键核对一列累计数：保存前用 lockSql（带 UPDLOCK, HOLDLOCK）读基准，提交前用 readSql 重读，
        // 应有 现值 − 基准 = delta[键]（没动的行是 0，也一并核对）。前后值都记进审计。
        internal static PuEditCheck SumCheck(WorkItem item, string label, string lockSql, string readSql, Dictionary<int, decimal> delta)
        {
            Dictionary<int, decimal> before = new Dictionary<int, decimal>();
            PuEditCheck check = new PuEditCheck();
            check.Label = label;
            check.Before = delegate(object conn)
            {
                foreach (KeyValuePair<int, decimal> kv in delta)
                {
                    before[kv.Key] = PuInv.Num(Rows.Scalar(conn, lockSql, new object[] { kv.Key }));
                }
            };
            check.After = delegate(object conn) { RequireSums(conn, item, label, readSql, delta, before); };
            return check;
        }

        static void RequireSums(object conn, WorkItem item, string label, string readSql,
            Dictionary<int, decimal> delta, Dictionary<int, decimal> before)
        {
            bool miss = false;
            foreach (KeyValuePair<int, decimal> kv in delta)
            {
                decimal now = PuInv.Num(Rows.Scalar(conn, readSql, new object[] { kv.Key }));
                decimal was = before[kv.Key];
                CoRows.Note(item, "回写 " + label + " " + kv.Key.ToString(CultureInfo.InvariantCulture) + " "
                    + Show(was) + "→" + Show(now) + " 应变 " + Show(kv.Value));
                if (Math.Abs(now - was - kv.Value) > 0.000001m)
                {
                    miss = true;
                }
            }
            if (miss)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有按预期回写" + label);
            }
        }

        // 行主键 → 来源键的合计变化：sign 乘在数量减少量上（累计数随之减少时传 −1）。来源键 ≤ 0 的行不核对。
        internal static Dictionary<int, decimal> BySource(List<Dictionary<string, object>> lines, string lineCol,
            string srcCol, Dictionary<int, decimal> drops, int sign)
        {
            Dictionary<int, decimal> map = new Dictionary<int, decimal>();
            for (int i = 0; lines != null && i < lines.Count; i++)
            {
                int src = CoRows.AsId(CoRows.Col(lines[i], srcCol));
                if (src <= 0)
                {
                    continue;
                }
                decimal drop;
                drops.TryGetValue(CoRows.AsId(CoRows.Col(lines[i], lineCol)), out drop);
                decimal sum;
                map.TryGetValue(src, out sum);
                map[src] = sum + sign * drop;
            }
            return map;
        }

        static string Show(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }

        static void Release(List<object> rows)
        {
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                ComUtil.ReleaseOne(rows[i]);
            }
        }
    }

    // 各类型的表体口径：行主键属性、数量属性（ApplyLines 按请求填）、红蓝（写回 U8 的数量符号）、按新数量（带符号）重算金额；
    // Follow：实收 / 合格数量随数量一起改（到货单、退货单）。
    internal sealed class PuEditShape
    {
        public string IdAttr;
        public string QtyName;
        public int Sign;
        public bool Follow;
        public Action<object, object, List<string>, decimal> Amounts;
    }

    // 保存事务里的来源累计数核对：Before 在 VoucherSave2 之前加锁读基准，After 在提交前核对。
    internal sealed class PuEditCheck
    {
        public string Label;
        public Action<object> Before;
        public Action<object> After;
    }
}
