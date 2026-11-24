using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单修改后的回读核对（新连接上的 MoSnap 对 MoPlan）：
    // 行数不变；每行存货不变、数量 / 开工 / 完工 / 备注 / 自定义项等于目标；每行子件数不变，
    // 每个子件（按 MoAllocRow.Key 对应）数量、件数等于目标（没改数量的行即原值），MoUpdateCols 名单里的列不变，替代料行数等于目标
    // （调用方给了替代料的子件回读带替代料时再比全部列）。
    // 列出全部不符项；调用方据此报 504 outcome_unknown，不能让用料悄悄丢掉。
    internal static class MoUpdateCheck
    {
        const decimal Eps = 0.000001m;

        public static List<string> Diff(MoPlan plan, MoSnap after)
        {
            List<string> bad = new List<string>();
            if (after.Lines.Count != plan.Lines.Count)
            {
                bad.Add("行数 " + N(plan.Lines.Count) + " → " + N(after.Lines.Count));
            }
            for (int i = 0; i < plan.Lines.Count; i++)
            {
                MoTarget t = plan.Lines[i];
                MoLineRow row = after.LineById(t.Row.MoDId);
                if (row == null)
                {
                    bad.Add("第 " + N(t.Row.SortSeq) + " 行不见了");
                    continue;
                }
                LineDiff(t, row, bad);
                AllocDiff(t, plan.Allocs[t.Row.MoDId], after.AllocsOf(row.MoDId), bad);
            }
            return bad;
        }

        static void LineDiff(MoTarget t, MoLineRow row, List<string> bad)
        {
            string at = "第 " + N(t.Row.SortSeq) + " 行";
            if (!Same(row.InvCode, t.Row.InvCode))
            {
                bad.Add(at + "存货变成 " + row.InvCode);
            }
            if (!Near(row.Qty, t.Qty))
            {
                bad.Add(at + "数量 " + D(row.Qty) + "，应为 " + D(t.Qty));
            }
            if (row.Start != t.Start || row.Due != t.Due)
            {
                bad.Add(at + "日期 " + row.Start + "～" + row.Due + "，应为 " + t.Start + "～" + t.Due);
            }
            if (!AuxSame(row.AuxQty, t.AuxQty, t.Row.AuxQty))
            {
                bad.Add(at + "件数 " + row.AuxQty + " 与目标不符");
            }
            if (row.Remark != t.Remark)
            {
                bad.Add(at + "备注与请求不符");
            }
            foreach (KeyValuePair<string, string> kv in t.Defines)
            {
                string now;
                row.Defines.TryGetValue(kv.Key, out now);
                if ((now ?? "") != (kv.Value ?? ""))
                {
                    bad.Add(at + kv.Key + " 与请求不符");
                }
            }
        }

        static void AllocDiff(MoTarget line, List<MoAllocTarget> want, List<MoAllocRow> got, List<string> bad)
        {
            string at = "第 " + N(line.Row.SortSeq) + " 行";
            if (got.Count != want.Count)
            {
                bad.Add(at + "子件 " + N(want.Count) + " 行变成 " + N(got.Count) + " 行");
                return;
            }
            bool[] used = new bool[got.Count];
            for (int i = 0; i < want.Count; i++)
            {
                MoAllocRow a = Take(got, used, want[i].Row.Key);
                if (a == null)
                {
                    bad.Add(at + "子件 " + want[i].Row.InvCode + "（行号 " + N(want[i].Row.SortSeq) + "）不见了");
                    continue;
                }
                if (!Near(a.Qty, want[i].Qty))
                {
                    bad.Add(at + "子件 " + a.InvCode + " 数量 " + D(a.Qty) + "，应为 " + D(want[i].Qty));
                }
                if (!AuxSame(a.AuxQty, want[i].AuxQty, want[i].Row.AuxQty))
                {
                    bad.Add(at + "子件 " + a.InvCode + " 件数 " + a.AuxQty + " 与目标不符");
                }
                if (!AuxSame(a.UpperMoQty, -1m, want[i].Upper))
                {
                    bad.Add(at + "子件 " + a.InvCode + " 的 UpperMoQty " + a.UpperMoQty + "，应为 " + want[i].Upper);
                }
                List<string> cols = MoUpdateCols.Changed(want[i].Row.Keep, a.Keep);
                bool subs = !SubsSame(want[i], a);
                if (cols.Count > 0 || subs)
                {
                    bad.Add(at + "子件 " + a.InvCode + " 的 " + string.Join("、", cols.ToArray())
                        + (subs ? "（替代料）" : "") + " 与目标不符");
                }
            }
        }

        // 替代料：行数等于目标；调用方给了的、回读带了替代料时全部列等于目标。
        static bool SubsSame(MoAllocTarget want, MoAllocRow got)
        {
            if (got.Subs != MoUpdateSubs.CountOf(want))
            {
                return false;
            }
            return !want.Given || got.SubRows == null || MoSubs.SameSet(got.SubRows, MoUpdateSubs.WantOf(want));
        }

        // 件数：目标为 -1 表示应保持原值（原文比，null 为空串），否则按数比。
        static bool AuxSame(string now, decimal want, string old)
        {
            if (want < 0m)
            {
                return now == old || (now.Length > 0 && old.Length > 0 && Near(MoUpdateSql.Dec(now), MoUpdateSql.Dec(old)));
            }
            return now.Length > 0 && Near(MoUpdateSql.Dec(now), want);
        }

        static MoAllocRow Take(List<MoAllocRow> got, bool[] used, string key)
        {
            for (int i = 0; i < got.Count; i++)
            {
                if (!used[i] && got[i].Key == key)
                {
                    used[i] = true;
                    return got[i];
                }
            }
            return null;
        }

        static bool Near(decimal a, decimal b)
        {
            decimal d = a - b;
            return d < Eps && d > -Eps;
        }

        static bool Same(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), System.StringComparison.OrdinalIgnoreCase);
        }

        static string N(int n)
        {
            return n.ToString(CultureInfo.InvariantCulture);
        }

        static string D(decimal d)
        {
            return d.ToString("0.######", CultureInfo.InvariantCulture);
        }

        // 响应：与生产订单新增相同的 ok、type、id、code、state、lines、allocates、details（每行 line_id、sort_seq、inv_code、
        // qty、status、allocates），另给 changed（本次改动的行数，含只改了替代料的行）；改了替代料时另给 allocations
        // （这些子件回读的替代料，MoUpdateSubsWrite.Output）。
        public static ApiResult Result(VoucherKind kind, int id, MoSnap after, MoPlan plan)
        {
            List<object> details = new List<object>();
            int allocates = 0;
            for (int i = 0; i < after.Lines.Count; i++)
            {
                MoLineRow row = after.Lines[i];
                int n = after.AllocsOf(row.MoDId).Count;
                allocates += n;
                Dictionary<string, object> line = new Dictionary<string, object>();
                line["line_id"] = row.MoDId;
                line["sort_seq"] = row.SortSeq;
                line["inv_code"] = row.InvCode;
                line["qty"] = MoCreateSql.Dec(D(row.Qty));
                line["status"] = MoUpdateSql.Int(row.Status);
                line["allocates"] = n;
                details.Add(line);
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = after.Code;
            body["state"] = State(after);
            body["lines"] = after.Lines.Count;
            body["allocates"] = allocates;
            body["details"] = details;
            body["changed"] = Changed(plan);
            List<object> subs = MoUpdateSubsWrite.Output(plan, after);
            if (subs != null)
            {
                body["allocations"] = subs;
            }
            return ApiResult.Ok(body);
        }

        // 同 MoCloseSql / vouchers/load：全部行 Status 3 或 4 且审核人非空才算已审核；审核人、日期取第一行。
        internal static Dictionary<string, object> State(MoSnap after)
        {
            bool verified = after.Lines.Count > 0;
            for (int i = 0; i < after.Lines.Count; i++)
            {
                MoLineRow row = after.Lines[i];
                verified = verified && (row.Status == "3" || row.Status == "4") && row.Verifier.Length > 0;
            }
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = verified;
            state["verifier"] = verified ? after.Lines[0].Verifier : "";
            state["verified_at"] = verified ? after.Lines[0].VerifiedAt : "";
            return state;
        }

        // 改了字段或改了子件替代料的行数。
        static int Changed(MoPlan plan)
        {
            int n = 0;
            for (int i = 0; i < plan.Lines.Count; i++)
            {
                if (plan.Lines[i].Touched || SubsTouched(plan.Allocs[plan.Lines[i].Row.MoDId]))
                {
                    n++;
                }
            }
            return n;
        }

        static bool SubsTouched(List<MoAllocTarget> allocs)
        {
            for (int i = 0; i < allocs.Count; i++)
            {
                if (allocs[i].Given && allocs[i].SubsChanged)
                {
                    return true;
                }
            }
            return false;
        }
    }
}
