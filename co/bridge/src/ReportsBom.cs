using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 物料清单展开：bom_bom（版本）+ bom_parent（母件）+ bom_opcomponent（子件），存货编码在 bas_part.InvCode。
    // 只取主 BOM（BomType=1）、已审核（Status=3）、as_of 落在版本生效区间内的版本；同一存货编码有多个物料
    // （自由项不同）时先取不带自由项的物料，再取版本号最大的，最后按 PartId、BomId 定序，结果确定。
    // 编码比较不分大小写（与账套库的排序规则一致）。
    // 多层在桥里逐层展开：每一层只查一次还没查过的母件（每个存货编码只查一次），路径在内存里展开，
    // 行数到 limit 就停并标 truncated，不会因为共用半成品在数据库里指数膨胀。路径上已有的子件不再展开（防环）。
    internal static class ReportsBom
    {
        const int Batch = 100;
        const int MaxChildRows = 50000;
        const string BaseFirst = "CASE WHEN COALESCE(NULLIF(bp.Free1,N''), NULLIF(bp.Free2,N''), NULLIF(bp.Free3,N''),"
            + " NULLIF(bp.Free4,N''), NULLIF(bp.Free5,N''), NULLIF(bp.Free6,N''), NULLIF(bp.Free7,N''),"
            + " NULLIF(bp.Free8,N''), NULLIF(bp.Free9,N''), NULLIF(bp.Free10,N'')) IS NULL THEN 0 ELSE 1 END,"
            + " b.Version DESC, bp.PartId, b.BomId DESC";
        const string Live = " FROM bom_bom b JOIN bom_parent p ON p.BomId=b.BomId JOIN bas_part bp ON bp.PartId=p.ParentId"
            + " WHERE b.Status=3 AND b.BomType=1"
            + " AND CONVERT(date, ?, 23) BETWEEN CONVERT(date, b.VersionEffDate) AND CONVERT(date, b.VersionEndDate)";

        // 参数：as_of, parent。
        const string RootSql = "SELECT TOP 1 b.BomId, b.Version" + Live + " AND bp.InvCode=? ORDER BY " + BaseFirst;

        // {IN} 按母件个数生成占位符。参数：as_of, 母件编码…, as_of。
        const string ChildSql = "SELECT z.parent_inv, cp.InvCode comp_inv, i.cInvName inv_name, i.cInvStd inv_std,"
            + " c.SortSeq sort_seq, c.BaseQtyN qty_n, c.BaseQtyD qty_d"
            + " FROM (SELECT b.BomId, bp.InvCode parent_inv, ROW_NUMBER() OVER (PARTITION BY bp.InvCode ORDER BY "
            + BaseFirst + ") rn" + Live + " AND bp.InvCode IN ({IN})) z"
            + " JOIN bom_opcomponent c ON c.BomId=z.BomId JOIN bas_part cp ON cp.PartId=c.ComponentId"
            + " LEFT JOIN Inventory i ON i.cInvCode=cp.InvCode"
            + " WHERE z.rn=1 AND ISNULL(c.RecursiveFlag,0)=0"
            + " AND CONVERT(date, ?, 23) BETWEEN CONVERT(date, c.EffBegDate) AND CONVERT(date, c.EffEndDate)"
            + " ORDER BY z.parent_inv, c.SortSeq, cp.InvCode";

        sealed class Node
        {
            public string Comp;
            public decimal? Qty;
            public string Path;
            public Dictionary<string, object> Item;
        }

        sealed class Walk
        {
            public WorkContext Ctx;
            public ReportArgs Args;
            public Dictionary<string, List<Dictionary<string, object>>> Children =
                new Dictionary<string, List<Dictionary<string, object>>>(StringComparer.OrdinalIgnoreCase);
            public List<object> Items = new List<object>();
            public bool Truncated;
        }

        public static ApiResult Expand(WorkContext ctx, ReportArgs a)
        {
            Dictionary<string, object> root = Rows.One(ctx.Conn, RootSql, new object[] { a.AsOf, a.Parent });
            if (root == null)
            {
                throw new BridgeException(404, "not_found", "该物料在指定日期没有已审核的物料清单");
            }
            // 数据权限：母件存货不放行 403；子件不放行的不列、也不往下展开。
            PermHook.Code(ctx, PermObj.Inventory, a.Parent);
            Walk w = new Walk();
            w.Ctx = ctx;
            w.Args = a;
            Node start = new Node();
            start.Comp = a.Parent;
            start.Qty = 1m;
            start.Path = "/" + a.Parent + "/";
            List<Node> frontier = new List<Node>();
            frontier.Add(start);
            for (int level = 1; level <= a.Levels && frontier.Count > 0 && !w.Truncated; level++)
            {
                frontier = Step(w, frontier, level);
            }
            Dictionary<string, object> body = Reports.Body();
            body["parent"] = a.Parent;
            body["as_of"] = a.AsOf;
            body["levels"] = a.Levels;
            body["bom_id"] = GlSql.Int(root, "BomId");
            body["version"] = GlSql.Int(root, "Version");
            body["items"] = w.Items;
            body["truncated"] = w.Truncated;
            return ApiResult.Ok(body);
        }

        // 一层：先补查这一层母件的子件，再按（母件编码、路径）顺序展开；返回下一层的母件。
        static List<Node> Step(Walk w, List<Node> frontier, int level)
        {
            if (!Load(w, frontier))
            {
                w.Truncated = true;
                return new List<Node>();
            }
            List<Node> next = new List<Node>();
            for (int i = 0; i < frontier.Count; i++)
            {
                Node parent = frontier[i];
                List<Dictionary<string, object>> kids;
                if (!w.Children.TryGetValue(parent.Comp, out kids))
                {
                    continue;
                }
                for (int k = 0; k < kids.Count; k++)
                {
                    string comp = GlSql.Col(kids[k], "comp_inv");
                    if (parent.Path.IndexOf("/" + comp + "/", StringComparison.OrdinalIgnoreCase) >= 0
                        || !PermHook.Allows(w.Ctx, PermObj.Inventory, comp))
                    {
                        continue;
                    }
                    if (w.Items.Count >= w.Args.Limit)
                    {
                        w.Truncated = true;
                        return new List<Node>();
                    }
                    Node node = Child(parent, kids[k], comp, level);
                    w.Items.Add(node.Item);
                    next.Add(node);
                }
            }
            next.Sort(Compare);
            return next;
        }

        static int Compare(Node x, Node y)
        {
            int c = string.CompareOrdinal(x.Comp, y.Comp);
            return c != 0 ? c : string.CompareOrdinal(x.Path, y.Path);
        }

        static Node Child(Node parent, Dictionary<string, object> row, string comp, int level)
        {
            decimal? n = Dec(row, "qty_n");
            decimal? d = Dec(row, "qty_d");
            Node node = new Node();
            node.Comp = comp;
            node.Qty = Mul(parent.Qty, n, d);
            node.Path = parent.Path + comp + "/";
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["level"] = level;
            item["parent"] = parent.Comp;
            item["component"] = comp;
            item["name"] = Reports.Text(row, "inv_name");
            item["spec"] = Reports.Text(row, "inv_std");
            item["sort"] = GlSql.Int(row, "sort_seq");
            item["qty_n"] = Round(n);
            item["qty_d"] = Round(d);
            item["qty"] = Round(node.Qty);
            item["path"] = node.Path;
            node.Item = item;
            return node;
        }

        // 累计用量在内存里按 decimal 全精度相乘，只在输出时保留 6 位。分母为 0 或溢出时为 null。
        static decimal? Mul(decimal? qty, decimal? n, decimal? d)
        {
            if (!qty.HasValue || !n.HasValue || !d.HasValue || d.Value == 0m)
            {
                return null;
            }
            try
            {
                return qty.Value * (n.Value / d.Value);
            }
            catch (OverflowException)
            {
                return null;
            }
        }

        static object Round(decimal? value)
        {
            if (!value.HasValue)
            {
                return null;
            }
            return decimal.Round(value.Value, 6, MidpointRounding.AwayFromZero);
        }

        static decimal? Dec(Dictionary<string, object> row, string name)
        {
            decimal value;
            if (!decimal.TryParse(GlSql.Col(row, name), NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                return null;
            }
            return value;
        }

        // 分批查还没查过的母件（每批 100 个编码）。单批子件行数到上限时返回 false，调用方标 truncated。
        static bool Load(Walk w, List<Node> frontier)
        {
            List<string> todo = new List<string>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < frontier.Count; i++)
            {
                string code = frontier[i].Comp;
                if (!w.Children.ContainsKey(code) && seen.Add(code))
                {
                    todo.Add(code);
                }
            }
            for (int start = 0; start < todo.Count; start += Batch)
            {
                int count = Math.Min(Batch, todo.Count - start);
                if (!LoadBatch(w, todo.GetRange(start, count)))
                {
                    return false;
                }
            }
            return true;
        }

        static bool LoadBatch(Walk w, List<string> codes)
        {
            List<object> args = new List<object>();
            args.Add(w.Args.AsOf);
            StringBuilder marks = new StringBuilder();
            for (int i = 0; i < codes.Count; i++)
            {
                marks.Append(i == 0 ? "?" : ", ?");
                args.Add(codes[i]);
                w.Children[codes[i]] = new List<Dictionary<string, object>>();
            }
            args.Add(w.Args.AsOf);
            string sql = ChildSql.Replace("{IN}", marks.ToString());
            List<Dictionary<string, object>> rows = Rows.Query(w.Ctx.Conn, sql, args.ToArray(), MaxChildRows);
            if (rows.Count >= MaxChildRows)
            {
                return false;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                List<Dictionary<string, object>> kids;
                if (w.Children.TryGetValue(GlSql.Col(rows[i], "parent_inv"), out kids))
                {
                    kids.Add(rows[i]);
                }
            }
            return true;
        }
    }
}
