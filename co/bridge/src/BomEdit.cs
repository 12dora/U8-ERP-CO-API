using System;
using System.Collections.Generic;

namespace U8Co
{
    // 物料清单修改（vouchers/update，type=bom，id=BomId）：U8API BomUpdate，登录子系统 BO。
    // U8 的 ModifyBOM 不看状态（实测 B4），桥自己只改未审核（Status=1）、没进审批流的标准 BOM；
    // 按 U8 BomBase._AddBomDetails（差异模式）：没送的替代料、定位符会被删掉，子件按 DInvCode 重取物料（自由项丢失），
    // 所以行上带替代料、定位符、分段损耗或自由项的不改；自定义项、领料部门、辅计量只在送了时才写，没送保留原值，
    // 可以改，但辅计量行的辅用量不随主用量重算，这类行不改用量。
    // 读出现有全部行和标志，叠上调用方的 add / update / delete（BomMerge），以 UpdateByDiff=true 送全部行。
    // 调用后在新连接上回读，表头（版本说明、生效日期、母件损耗率）和每行送给 U8 的全部字段与要写的一致才算成功；
    // 不一致时 IPC 错误 503、调用异常 504、U8 返回成功却不一致 409。
    internal static class BomEdit
    {
        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            BomAsk ask = BomReq.ParseUpdate(head, lines);
            string today = ctx.Item.Date ?? "";
            PermContext perm = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(BomRoutes.UpdateRule);
            PermCheck.RequireRule(perm, rule);
            Dictionary<string, object> before = BomRead.Head(ctx.Conn, id);
            BomState.Open(before, "修改");
            List<Dictionary<string, object>> current = BomRead.Lines(ctx.Conn, id);
            Plain(current, ask);
            MoDelete.CheckRows(perm, rule, BomRoutes.ParentRows(CoRows.Col(before, "inv_code")));
            BomMerge.Merge(ask, before, current);
            if (ask.EffDate != CoRows.Col(before, "eff_date"))
            {
                BomSql.CheckEffDate(BomSql.Versions(ctx.Conn, ask.PartId), ask.EffDate, id);
            }
            BomSql.CheckRows(ctx.Conn, ask, Touched(ask), today);
            BomSql.FillDefaultWh(ctx.Conn, ask.Rows, ask.Existing, today);
            Exception lost = BomCom.Save(ctx, ask, true);
            Confirm(ctx, id, ask, lost);
            return BomOut.Saved(ctx, kind, id, "修改");
        }

        static void Plain(List<Dictionary<string, object>> lines, BomAsk ask)
        {
            HashSet<int> qty = QtyChanged(ask);
            for (int i = 0; i < lines.Count; i++)
            {
                string seq = CoRows.Col(lines[i], "sort_seq");
                if (CoRows.Col(lines[i], "extras") == "1")
                {
                    throw new BridgeException(409, "state_mismatch", "第 " + seq
                        + " 行带替代料、定位符、分段损耗或自由项，请在 U8 客户端修改");
                }
                if (CoRows.Col(lines[i], "aux_unit").Trim().Length > 0 && qty.Contains(BomSql.Int(seq)))
                {
                    throw new BridgeException(409, "state_mismatch", "第 " + seq + " 行带辅计量，用量请在 U8 客户端修改");
                }
            }
        }

        // update 里改了 base_qty_n / base_qty_d 的行号。
        static HashSet<int> QtyChanged(BomAsk ask)
        {
            HashSet<int> seqs = new HashSet<int>();
            for (int i = 0; i < ask.Changes.Count; i++)
            {
                BomChange change = ask.Changes[i];
                if (change.Op == "update" && (change.Fields.ContainsKey("base_qty_n") || change.Fields.ContainsKey("base_qty_d")))
                {
                    seqs.Add(change.Seq);
                }
            }
            return seqs;
        }

        // 要查的行：新增的行和改了字段的行（存货、仓库按新增同一套规则查），没动的现有行不查。
        static List<BomRow> Touched(BomAsk ask)
        {
            HashSet<int> changed = new HashSet<int>();
            for (int i = 0; i < ask.Changes.Count; i++)
            {
                if (ask.Changes[i].Op == "update")
                {
                    changed.Add(ask.Changes[i].Seq);
                }
            }
            List<BomRow> rows = new List<BomRow>();
            for (int i = 0; i < ask.Rows.Count; i++)
            {
                if (changed.Contains(ask.Rows[i].Seq) || !ask.Existing.Contains(ask.Rows[i].Seq))
                {
                    rows.Add(ask.Rows[i]);
                }
            }
            return rows;
        }

        static void Confirm(WorkContext ctx, int id, BomAsk ask, Exception lost)
        {
            bool same = false;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                same = Same(BomRead.Head(conn, id), BomRead.Lines(conn, id), ask);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "BomEdit " + MoApi.FirstLine(ex.Message));
                throw new BridgeException(504, "outcome_unknown", "已提交修改但未能回读，BomId " + BomReq.Num(id));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (!same)
            {
                throw BomOut.Unreached(lost, "修改", id);
            }
        }

        // 回读与要写的整套状态比对（BomSelfTest 也用）：表头三项，行数，每行 BomRow.SameAs。
        internal static bool Same(Dictionary<string, object> head, List<Dictionary<string, object>> lines, BomAsk ask)
        {
            if (CoRows.Col(head, "eff_date") != ask.EffDate || CoRows.Col(head, "version_desc").Trim() != ask.VersionDesc.Trim()
                || BomSql.Dec(CoRows.Col(head, "parent_scrap")) != ask.ParentScrap || lines.Count != ask.Rows.Count)
            {
                return false;
            }
            for (int i = 0; i < lines.Count; i++)
            {
                if (!BomRead.RowOf(lines[i]).SameAs(ask.Rows[i]))
                {
                    return false;
                }
            }
            return true;
        }
    }
}
