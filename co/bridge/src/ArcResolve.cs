using System;
using System.Collections.Generic;

namespace U8Co
{
    // archives/resolve：按名称、简称、助记码等把调用方的文本解析成档案编码。读路由，只跑 SQL（读线程、登录缓存）。
    // 一个请求可以含多类档案，功能权限按每项的 archive:<档案> 规则逐项查（PermGate 对本路由整体放过，见 PermRegistry.PerItem），
    // 记录级数据权限（AA_HoldAuth）与 archives/list 同一段条件。
    // 分档：code → name → abbr → mnemonic（含存货代码 add_code）→ contains；第一档有命中就停，档内按编码排序。
    internal static class ArcResolve
    {
        internal const string Path = Requests.ArcRoot + "resolve";

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "解析请求缺少请求体");
            }
            ArcResolveReq req = ArcResolveReq.Parse(ctx.Item.Body);
            PermContext p = PermCheck.Of(ctx);
            List<object> results = new List<object>();
            // 同一类档案的查询表达式每个请求只建一次（项目的大类列表只查一次库）。
            Dictionary<string, ResolveSrc> srcs = new Dictionary<string, ResolveSrc>(StringComparer.Ordinal);
            for (int i = 0; i < req.Items.Count; i++)
            {
                results.Add(One(ctx, p, req, req.Items[i], srcs));
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["results"] = results;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> One(WorkContext ctx, PermContext p, ArcResolveReq req, ResolveItem item,
            Dictionary<string, ResolveSrc> srcs)
        {
            ResolveRun run = new ResolveRun();
            run.Rule = Permit(p, item.Kind);
            run.Perm = p;
            run.Req = req;
            run.Date = ctx.Item.Date ?? "";
            ResolveSrc src;
            if (!srcs.TryGetValue(item.Kind.Name, out src))
            {
                src = ArcResolveSrc.Of(ctx, item.Kind, p);
                srcs[item.Kind.Name] = src;
            }
            run.Src = src;
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["archive"] = item.Kind.Name;
            result["q"] = item.Q;
            if (!run.Src.Empty)
            {
                for (int tier = ArcResolveSql.TierCode; tier <= ArcResolveSql.TierContains; tier++)
                {
                    if (!ArcResolveSql.Has(run.Src, tier))
                    {
                        continue;
                    }
                    List<Dictionary<string, object>> rows = Query(ctx, run, tier, item.Q);
                    if (rows.Count > 0)
                    {
                        Fill(result, run, tier, item.Q, rows);
                        return result;
                    }
                }
            }
            result["status"] = "none";
            result["candidates"] = new List<object>();
            result["more"] = false;
            return result;
        }

        // 该档案的读取规则（同 archives/list）；操作员、角色另要求账套主管（同 ArcUa）。
        static PermRule Permit(PermContext p, ArcKind kind)
        {
            PermRule rule = PermRegistry.ForKey("archive:" + kind.Name);
            PermCheck.RequireRule(p, rule);
            if (ArcUa.Is(kind) && !p.Supervisor)
            {
                throw new BridgeException(403, "no_permission", "只有账套主管能读取操作员、角色");
            }
            return rule;
        }

        static List<Dictionary<string, object>> Query(WorkContext ctx, ResolveRun run, int tier, string q)
        {
            List<object> args = new List<object>();
            string sql = ArcResolveSql.Build(run, tier, q, args);
            return Rows.Query(ctx.Conn, sql, args.ToArray(), run.Req.Limit + 1);
        }

        // 第 1–4 档恰好一条是 exact，第 5 档恰好一条是 partial，多于一条是 ambiguous；match 只在恰好一条时给。
        static void Fill(Dictionary<string, object> result, ResolveRun run, int tier, string q,
            List<Dictionary<string, object>> rows)
        {
            int limit = run.Req.Limit;
            List<object> candidates = new List<object>();
            for (int i = 0; i < rows.Count && i < limit; i++)
            {
                candidates.Add(Candidate(rows[i], tier, q, run.Req.IncludeDisabled));
            }
            if (rows.Count == 1)
            {
                Dictionary<string, object> first = (Dictionary<string, object>)candidates[0];
                Dictionary<string, object> match = new Dictionary<string, object>();
                match["code"] = first["code"];
                match["name"] = first["name"];
                match["match"] = first["match"];
                result["status"] = tier == ArcResolveSql.TierContains ? "partial" : "exact";
                result["match"] = match;
            }
            else
            {
                result["status"] = "ambiguous";
            }
            result["candidates"] = candidates;
            result["more"] = rows.Count > limit;
        }

        static Dictionary<string, object> Candidate(Dictionary<string, object> row, int tier, string q, bool withDisabled)
        {
            Dictionary<string, object> c = new Dictionary<string, object>();
            c["code"] = Trimmed(row, "code") ?? "";
            c["name"] = Trimmed(row, "name") ?? "";
            c["match"] = MatchName(row, tier, q);
            Extra(c, row, "abbr", "abbr");
            Extra(c, row, "spec", "spec");
            Extra(c, row, "unit", "unit");
            Extra(c, row, "class_code", "class_code");
            if (withDisabled && ArcRead.Cell(row, "disabled") == "1")
            {
                c["disabled"] = true;
            }
            return c;
        }

        // 助记码一档里，助记码等于 q（不分大小写）记 mnemonic，否则是存货代码命中，记 add_code。
        static string MatchName(Dictionary<string, object> row, int tier, string q)
        {
            if (tier != ArcResolveSql.TierMnem)
            {
                return ArcResolveSql.TierNames[tier];
            }
            string mnem = Trimmed(row, "mnem");
            return mnem != null && string.Equals(mnem, q, StringComparison.OrdinalIgnoreCase) ? "mnemonic" : "add_code";
        }

        static void Extra(Dictionary<string, object> c, Dictionary<string, object> row, string col, string key)
        {
            string value = Trimmed(row, col);
            if (value != null && value.Length > 0)
            {
                c[key] = value;
            }
        }

        static string Trimmed(Dictionary<string, object> row, string col)
        {
            string value = ArcRead.Cell(row, col);
            return value == null ? null : value.Trim();
        }
    }
}
