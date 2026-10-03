using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 发货单修改新增行（op=add）。新行参照本单来源销售订单的行（source_line_id = iSOsID），同生单：
    // 订单已审核、未关闭，行未关闭，客户同发货单，可发数量（iQuantity − iFHQuantity，再算上本次其他行的数量差）够。
    // 写 DOM 时照生单从 sale_RefSOVouch_B 取行（SaleGen.EditAddLine），editprop=A，行号接在原有行之后。
    // 未覆盖：VoucherSave 状态 1 带 A 行是否按新行数量加订单行 iFHQuantity（核对同修改：SaleGen.EditCheck）。
    internal static partial class SaleEditMore
    {
        const decimal Eps = 0.000001m;
        const string AddCusSql = "select cCusCode as cus from DispatchList where DLID=?";
        const string AddOrdersSql = "select distinct d.ID as id from SO_SODetails d "
            + "inner join DispatchLists x on x.iSOsID=d.iSOsID where x.DLID=?";
        const string AddLineSql = "select d.ID as id, d.cInvCode as inv, convert(varchar(20), d.iRowNo) as rowno, "
            + "d.cSCloser as lcloser, convert(varchar(40), isnull(d.iQuantity,0)) as q, "
            + "convert(varchar(40), isnull(d.iFHQuantity,0)) as fh, m.cSOCode as code, m.cCusCode as cus, "
            + "m.cVerifier as verifier, m.cCloser as closer from SO_SODetails d inner join SO_SOMain m on m.ID=d.ID where d.iSOsID=?";

        // 新增行：必须有 source_line_id、iquantity、cwhcode，另可带 cbatch、cmemo、cfree1–10、cdefine22–37；不能带 line_id。
        static SaleEditRow ReadAdd(SaleEditPlan plan, Dictionary<string, object> map, List<SaleEditRow> adds, int at)
        {
            if (plan.Kind.Name != "dispatch")
            {
                throw BridgeException.BadField("lines.op", "该单据类型修改不能新增行");
            }
            if (Raw(map, "line_id") != null)
            {
                throw BridgeException.BadField("lines.line_id", "新增行不能带 line_id");
            }
            SaleEditRow row = new SaleEditRow();
            row.Added = true;
            row.At = at;
            row.SoLine = AddSource(map, adds);
            row.Src = row.SoLine;
            Dictionary<string, object> fields = Clean(plan.Kind, false, WithoutSource(map));
            object qty;
            if (!fields.TryGetValue("iquantity", out qty))
            {
                throw BridgeException.BadField("lines.iquantity", "新增行必须填数量");
            }
            fields.Remove("iquantity");
            decimal value;
            if (!StockUnits.Dec(Cell(qty), out value) || value <= 0m)
            {
                throw BridgeException.BadField("lines.iquantity", "数量必须大于 0");
            }
            row.New = value;
            row.Qty = true;
            row.Fields = Texts(fields);
            string wh;
            if (!row.Fields.TryGetValue("cwhcode", out wh) || wh.Trim().Length == 0)
            {
                throw BridgeException.BadField("lines.cwhcode", "必须指定仓库");
            }
            return row;
        }

        static int AddSource(Dictionary<string, object> map, List<SaleEditRow> adds)
        {
            int soLine = CoRows.AsId(Raw(map, "source_line_id"));
            if (soLine <= 0)
            {
                throw BridgeException.BadField("lines.source_line_id", "新增行必须填来源订单行 source_line_id");
            }
            for (int i = 0; i < adds.Count; i++)
            {
                if (adds[i].SoLine == soLine)
                {
                    throw BridgeException.BadField("lines.source_line_id", "来源订单行重复");
                }
            }
            return soLine;
        }

        static Dictionary<string, object> WithoutSource(Dictionary<string, object> map)
        {
            Dictionary<string, object> rest = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (!string.Equals(pair.Key, "source_line_id", StringComparison.OrdinalIgnoreCase))
                {
                    rest[pair.Key] = pair.Value;
                }
            }
            return rest;
        }

        // 登录前用 SQL 核对新增行的来源；提交前在事务里按锁定的累计发货数再核对一次（RecheckAdds）。
        static void CheckAdds(object conn, SaleEditPlan plan)
        {
            List<SaleEditRow> adds = AddsOf(plan);
            if (adds.Count == 0)
            {
                return;
            }
            HashSet<int> orders = OrdersOf(conn, plan.Id);
            string cus = CoRows.Col(Rows.One(conn, AddCusSql, new object[] { plan.Id }), "cus");
            for (int i = 0; i < adds.Count; i++)
            {
                try
                {
                    CheckAdd(conn, adds[i], orders, cus);
                    RequireRoom(plan, adds[i], adds[i].SoQty - adds[i].SoFh);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", adds[i].At);
                }
            }
        }

        static HashSet<int> OrdersOf(object conn, int id)
        {
            HashSet<int> orders = new HashSet<int>();
            List<Dictionary<string, object>> found = Rows.Query(conn, AddOrdersSql, new object[] { id }, 500);
            for (int i = 0; i < found.Count; i++)
            {
                orders.Add(CoRows.AsId(CoRows.Col(found[i], "id")));
            }
            if (orders.Count == 0)
            {
                throw BridgeException.BadField("lines.source_line_id", "发货单没有来源销售订单，不能新增行");
            }
            return orders;
        }

        static void CheckAdd(object conn, SaleEditRow row, HashSet<int> orders, string cus)
        {
            Dictionary<string, object> found = Rows.One(conn, AddLineSql, new object[] { row.SoLine });
            if (found == null)
            {
                throw BridgeException.BadField("lines.source_line_id", "销售订单行不存在");
            }
            if (!orders.Contains(CoRows.AsId(CoRows.Col(found, "id"))))
            {
                throw BridgeException.BadField("lines.source_line_id", "只能新增本发货单来源销售订单的行");
            }
            if (!string.Equals(CoRows.Col(found, "cus"), cus, StringComparison.OrdinalIgnoreCase))
            {
                throw BridgeException.BadField("lines.source_line_id", "销售订单的客户与发货单不同");
            }
            if (CoRows.Col(found, "verifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "销售订单未审核");
            }
            if (CoRows.Col(found, "closer").Length > 0 || CoRows.Col(found, "lcloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "销售订单已关闭");
            }
            row.Inv = CoRows.Col(found, "inv");
            row.OrderRow = CoRows.Col(found, "rowno");
            row.SoCode = CoRows.Col(found, "code");
            row.SoQty = PuInv.Num(CoRows.Col(found, "q"));
            row.Old = 0m;
            row.SoFh = PuInv.Num(CoRows.Col(found, "fh"));
        }

        // 订单行可发 left = iQuantity − iFHQuantity；本次修改在该订单行上的数量差（含新增行，原有行减少的数量可以挪给新行）不能超过它。
        static void RequireRoom(SaleEditPlan plan, SaleEditRow add, decimal left)
        {
            decimal delta = 0m;
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                SaleEditRow row = plan.Rows[i];
                if (row.SoLine == add.SoLine)
                {
                    delta = delta + row.New - row.Old;
                }
            }
            if (delta > left + Eps)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
        }

        // 事务里 SaleGen.EditBase 已 UPDLOCK 读出订单行累计发货数（So 槽 {fretquantity, iFHQuantity, 应变}），按它再核一次。
        static void RecheckAdds(SaleEditPlan plan, SaleEditBack back)
        {
            List<SaleEditRow> adds = AddsOf(plan);
            for (int i = 0; i < adds.Count; i++)
            {
                decimal[] slot;
                if (!back.So.TryGetValue(adds[i].SoLine, out slot))
                {
                    throw new BridgeException(409, "state_mismatch", "销售订单行不存在");
                }
                if (slot[1] + slot[2] > adds[i].SoQty + Eps)
                {
                    throw new BridgeException(409, "state_mismatch", "超过可生单数量");
                }
            }
        }

        static List<SaleEditRow> AddsOf(SaleEditPlan plan)
        {
            List<SaleEditRow> adds = new List<SaleEditRow>();
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                if (plan.Rows[i].Added)
                {
                    adds.Add(plan.Rows[i]);
                }
            }
            return adds;
        }

        // 写 DOM：原有行之后逐个追加，行号取原有行的最大 irowno 往后排，新行写上本单 dlid。
        static void AddLines(WorkContext ctx, object co, object[] doms, SaleEditPlan plan)
        {
            List<SaleEditRow> adds = AddsOf(plan);
            if (adds.Count == 0)
            {
                return;
            }
            int rowNo = MaxRowNo(doms[1]);
            List<string> schema = DomRows.Schema(doms[1]);
            for (int i = 0; i < adds.Count; i++)
            {
                rowNo = rowNo + 1;
                SaleGen.EditAddLine(ctx, co, doms, adds[i], rowNo);
                List<object> rows = DomRows.RowsOf(doms[1]);
                SetIf(doms[1], rows[rows.Count - 1], schema, "dlid", plan.Id.ToString(CultureInfo.InvariantCulture));
            }
        }

        static int MaxRowNo(object body)
        {
            int max = 0;
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                int n;
                if (int.TryParse(DomRows.Get(rows[i], "irowno").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n) && n > max)
                {
                    max = n;
                }
            }
            return max > 0 ? max : rows.Count;
        }

        // 保存后的新行：不在原有行里、订单行和数量对得上请求的新增行，一行对一行。
        static void RequireAdded(WorkItem item, SaleEditPlan plan, List<Dictionary<string, object>> now)
        {
            HashSet<int> taken = new HashSet<int>();
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                if (!plan.Rows[i].Added)
                {
                    taken.Add(plan.Rows[i].LineId);
                }
            }
            List<SaleEditRow> adds = AddsOf(plan);
            for (int i = 0; i < adds.Count; i++)
            {
                int hit = FindNew(now, taken, adds[i]);
                if (hit == 0)
                {
                    CoRows.Note(item, "回写核对 新增行 订单行" + adds[i].SoLine.ToString(CultureInfo.InvariantCulture)
                        + " 数量 " + Num(adds[i].New) + " 没有找到");
                    throw new BridgeException(409, "u8_rejected", "U8 保存的明细与请求不一致");
                }
                taken.Add(hit);
            }
        }

        static int FindNew(List<Dictionary<string, object>> now, HashSet<int> taken, SaleEditRow add)
        {
            for (int i = 0; i < now.Count; i++)
            {
                int id = CoRows.AsId(CoRows.Col(now[i], "id"));
                if (taken.Contains(id) || CoRows.AsId(CoRows.Col(now[i], "so")) != add.SoLine)
                {
                    continue;
                }
                if (Math.Abs(PuInv.Num(CoRows.Col(now[i], "q")) - add.New) <= Eps)
                {
                    return id;
                }
            }
            return 0;
        }
    }
}
