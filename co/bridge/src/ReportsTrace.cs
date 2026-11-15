using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 单据追溯 doc_trace：从一张单据出发，按 ReportsTraceMap 的边表逐跳向上游、下游找关联单据。
    // 每一跳每条边一句参数化查询（id 每批 100 个），新节点每类一句查询取单号、日期、状态和数据权限标记。
    // 数据权限与 vouchers/load 相同：按该类型的读取规则（voucher:<type>）判断功能权限和记录级条件；
    // 起点单据越权 403，其余越权的节点不列、不往下找，只计入 omitted。上游找到的节点不再往下游找（反之亦然）。
    // 截断分两种，都让响应的 truncated 为 true：某条边一次查出多于 MaxEdgeRows 行时只停这一个方向（EdgeCapped，
    // 下游被截断后上游照样找）；节点数到 max_nodes 时两个方向都停（NodeCapped）。
    internal static class ReportsTrace
    {
        const int Batch = 100;
        const int MaxEdgeRows = 20000;

        const string NodeSql = "SELECT h.{ID} AS id, {CODE} AS code, CONVERT(varchar(10), {DATE}, 23) AS doc_date,"
            + " {STATE} AS state, CASE WHEN 1=1{PERM} THEN 1 ELSE 0 END AS allowed FROM {HEAD} h"
            + " WHERE h.{ID} IN ({IN}){COND}";
        const string EdgeSql = "SELECT {UP} AS up_id, {DOWN} AS down_id, COUNT(*) AS n FROM {SRC}"
            + " WHERE {KEY} IN ({IN}){COND} GROUP BY {UP}, {DOWN}";

        sealed class Walk
        {
            public WorkContext Ctx;
            public PermContext Perm;
            public TraceArgs Args;
            public Dictionary<string, Dictionary<string, object>> Nodes =
                new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
            public List<object> NodeList = new List<object>();
            public Dictionary<string, Dictionary<string, object>> Edges =
                new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
            public List<object> EdgeList = new List<object>();
            public HashSet<string> Denied = new HashSet<string>(StringComparer.Ordinal);
            // EdgeCapped：本方向有边超过行数上限（每个方向开始时清零）；EdgeHit：任一方向出现过；NodeCapped：节点数到上限。
            public bool EdgeCapped;
            public bool EdgeHit;
            public bool NodeCapped;

            public bool Stopped
            {
                get { return EdgeCapped || NodeCapped; }
            }
        }

        // 一跳里找到的一条关联：上游、下游两端和明细行数。
        sealed class Link
        {
            public string From;
            public int FromId;
            public string To;
            public int ToId;
            public int Lines;
        }

        public static ApiResult Run(WorkContext ctx, ReportArgs ignored)
        {
            TraceArgs a = ReportsTraceReq.ParseTrace(ctx.Item.Body);
            Walk w = new Walk();
            w.Ctx = ctx;
            w.Args = a;
            w.Perm = PermCheck.Of(ctx);
            PermCheck.RequireRule(w.Perm, PermRegistry.ForKey("voucher:" + a.Type));
            List<Dictionary<string, object>> rows = Fetch(w, ReportsTraceMap.Node(a.Type), new List<int>(new int[] { a.Id }));
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (!GlSql.Bit(rows[0], "allowed"))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
            Add(w, a.Type, rows[0], 0);
            if (a.Direction != "up")
            {
                Expand(w, true);
            }
            if (a.Direction != "down")
            {
                Expand(w, false);
            }
            Dictionary<string, object> body = Reports.Body();
            body["type"] = a.Type;
            body["id"] = a.Id;
            body["depth"] = a.Depth;
            body["direction"] = a.Direction;
            body["nodes"] = w.NodeList;
            body["edges"] = w.EdgeList;
            body["omitted"] = w.Denied.Count;
            body["truncated"] = w.EdgeHit || w.NodeCapped;
            return ApiResult.Ok(body);
        }

        static string Key(string kind, int id)
        {
            return kind + ":" + id.ToString(CultureInfo.InvariantCulture);
        }

        static void Expand(Walk w, bool down)
        {
            w.EdgeCapped = false;
            List<string> frontier = new List<string>();
            frontier.Add(Key(w.Args.Type, w.Args.Id));
            for (int level = 1; level <= w.Args.Depth && frontier.Count > 0 && !w.Stopped; level++)
            {
                frontier = Hop(w, frontier, down, down ? level : -level);
            }
        }

        // 一跳：按边表查出全部关联，取新节点（带权限标记），登记节点和两端都在图里的边；返回新节点作为下一跳的起点。
        static List<string> Hop(Walk w, List<string> frontier, bool down, int level)
        {
            List<Link> links = new List<Link>();
            TraceEdge[] edges = ReportsTraceMap.All();
            for (int i = 0; i < edges.Length && !w.Stopped; i++)
            {
                List<int> ids = IdsOf(frontier, down ? edges[i].From : edges[i].To);
                for (int start = 0; start < ids.Count && !w.Stopped; start += Batch)
                {
                    Query(w, edges[i], ids.GetRange(start, Math.Min(Batch, ids.Count - start)), down, links);
                }
            }
            List<string> added = Resolve(w, links, down, level);
            for (int i = 0; i < links.Count; i++)
            {
                AddEdge(w, links[i]);
            }
            return added;
        }

        static List<int> IdsOf(List<string> frontier, string kind)
        {
            List<int> ids = new List<int>();
            string prefix = kind + ":";
            for (int i = 0; i < frontier.Count; i++)
            {
                if (frontier[i].StartsWith(prefix, StringComparison.Ordinal))
                {
                    ids.Add(int.Parse(frontier[i].Substring(prefix.Length), CultureInfo.InvariantCulture));
                }
            }
            return ids;
        }

        static void Query(Walk w, TraceEdge e, List<int> ids, bool down, List<Link> links)
        {
            List<object> args = new List<object>();
            string sql = EdgeSql.Replace("{UP}", e.Up).Replace("{DOWN}", e.Down).Replace("{SRC}", e.Source)
                .Replace("{KEY}", down ? e.Up : e.Down).Replace("{IN}", Marks(args, ids)).Replace("{COND}", e.Cond);
            List<Dictionary<string, object>> rows = Rows.Query(w.Ctx.Conn, sql, args.ToArray(), MaxEdgeRows + 1);
            if (rows.Count > MaxEdgeRows)
            {
                w.EdgeCapped = true;
                w.EdgeHit = true;
                return;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                Link link = new Link();
                link.From = e.From;
                link.FromId = GlSql.Int(rows[i], "up_id");
                link.To = e.To;
                link.ToId = GlSql.Int(rows[i], "down_id");
                link.Lines = GlSql.Int(rows[i], "n");
                if (link.FromId > 0 && link.ToId > 0)
                {
                    links.Add(link);
                }
            }
        }

        static string Marks(List<object> args, List<int> ids)
        {
            StringBuilder marks = new StringBuilder();
            for (int i = 0; i < ids.Count; i++)
            {
                marks.Append(i == 0 ? "?" : ", ?");
                args.Add(ids[i]);
            }
            return marks.ToString();
        }

        // 新节点按类型分组（类型按首次出现的顺序，组内按 id 升序）逐类取出；没有该类型读取权限的整类计入 omitted。
        static List<string> Resolve(Walk w, List<Link> links, bool down, int level)
        {
            List<string> kinds = new List<string>();
            Dictionary<string, List<int>> fresh = new Dictionary<string, List<int>>(StringComparer.Ordinal);
            for (int i = 0; i < links.Count; i++)
            {
                string kind = down ? links[i].To : links[i].From;
                int id = down ? links[i].ToId : links[i].FromId;
                string key = Key(kind, id);
                if (w.Nodes.ContainsKey(key) || w.Denied.Contains(key))
                {
                    continue;
                }
                List<int> ids;
                if (!fresh.TryGetValue(kind, out ids))
                {
                    ids = new List<int>();
                    fresh.Add(kind, ids);
                    kinds.Add(kind);
                }
                if (!ids.Contains(id))
                {
                    ids.Add(id);
                }
            }
            List<string> added = new List<string>();
            for (int k = 0; k < kinds.Count; k++)
            {
                List<int> ids = fresh[kinds[k]];
                ids.Sort();
                AddKind(w, kinds[k], ids, level, added);
            }
            return added;
        }

        static void AddKind(Walk w, string kind, List<int> ids, int level, List<string> added)
        {
            PermRule rule = PermRegistry.ForKey("voucher:" + kind);
            if (rule == null || !w.Perm.HasAny(rule.Auths))
            {
                for (int i = 0; i < ids.Count; i++)
                {
                    w.Denied.Add(Key(kind, ids[i]));
                }
                return;
            }
            TraceNode node = ReportsTraceMap.Node(kind);
            for (int start = 0; start < ids.Count; start += Batch)
            {
                List<Dictionary<string, object>> rows = Fetch(w, node, ids.GetRange(start, Math.Min(Batch, ids.Count - start)));
                for (int i = 0; i < rows.Count; i++)
                {
                    string key = Key(kind, GlSql.Int(rows[i], "id"));
                    if (!GlSql.Bit(rows[i], "allowed"))
                    {
                        w.Denied.Add(key);
                    }
                    else if (w.Nodes.Count >= w.Args.MaxNodes)
                    {
                        w.NodeCapped = true;
                    }
                    else
                    {
                        Add(w, kind, rows[i], level);
                        added.Add(key);
                    }
                }
            }
        }

        // 取节点，带 allowed（该类型读取规则的记录级条件，参数在 id 之前）。按 id 升序。
        static List<Dictionary<string, object>> Fetch(Walk w, TraceNode node, List<int> ids)
        {
            List<object> args = new List<object>();
            StringBuilder perm = new StringBuilder();
            PermSql.AppendRule(perm, args, w.Perm, PermRegistry.ForKey("voucher:" + node.Kind), "h");
            string sql = NodeSql.Replace("{ID}", node.Id).Replace("{CODE}", node.Code).Replace("{DATE}", node.Date)
                .Replace("{STATE}", node.State).Replace("{HEAD}", node.Head).Replace("{COND}", node.Cond)
                .Replace("{PERM}", perm.ToString()).Replace("{IN}", Marks(args, ids));
            return Rows.Query(w.Ctx.Conn, sql + " ORDER BY h." + node.Id, args.ToArray(), ids.Count);
        }

        static void Add(Walk w, string kind, Dictionary<string, object> row, int level)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            int id = GlSql.Int(row, "id");
            item["type"] = kind;
            item["id"] = id;
            item["code"] = Reports.Text(row, "code");
            item["date"] = Reports.Text(row, "doc_date");
            item["state"] = GlSql.Col(row, "state");
            item["level"] = level;
            w.Nodes[Key(kind, id)] = item;
            w.NodeList.Add(item);
        }

        // 两端都在图里才登记；同一对单据由两条边找到时（上游、下游各找一次）只记一次。
        static void AddEdge(Walk w, Link link)
        {
            string from = Key(link.From, link.FromId);
            string to = Key(link.To, link.ToId);
            if (!w.Nodes.ContainsKey(from) || !w.Nodes.ContainsKey(to) || w.Edges.ContainsKey(from + ">" + to))
            {
                return;
            }
            Dictionary<string, object> edge = new Dictionary<string, object>();
            edge["from_type"] = link.From;
            edge["from_id"] = link.FromId;
            edge["to_type"] = link.To;
            edge["to_id"] = link.ToId;
            edge["lines"] = link.Lines;
            w.Edges[from + ">" + to] = edge;
            w.EdgeList.Add(edge);
        }
    }
}
