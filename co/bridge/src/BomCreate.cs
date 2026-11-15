using System;
using System.Collections.Generic;

namespace U8Co
{
    // 物料清单新增（vouchers/create，type=bom）：U8API BomAdd，登录子系统 BO，只建标准 BOM（BomType=1），新版本状态按
    // mom_parameter.BomDefaultStatus（如 1 未审核）。调用前查完母件、子件、仓库、版本号和生效日期。
    // 新 BomId 不写回表头（实测 B2），调用后在新连接上按（母件 PartId、版本、BomType=1）找：
    // U8 返回成功时认创建时间不早于调用前数据库时间的那一行；调用异常或 IPC 错误时还要制单人是本操作员、
    // 行号和子件与请求逐行相同，否则不认。找到算成功；有同版本的行却认不了 504，一行都没有时 IPC 错误 503、其他 504。
    internal static class BomCreate
    {
        public static ApiResult Run(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            BomAsk ask = BomReq.ParseCreate(head, lines);
            string user = Operator(ctx);
            string today = ctx.Item.Date ?? "";
            PermContext perm = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(BomRoutes.CreateRule);
            PermCheck.RequireRule(perm, rule);
            // 先查编码是否存在（不存在 400），再按数据权限判断（越权 403）。
            Prepare(ctx.Conn, ask, today);
            MoDelete.CheckRows(perm, rule, BomRoutes.ParentRows(ask.InvCode));
            ask.Since = BomSql.Now(ctx.Conn);
            Exception lost = BomCom.Save(ctx, ask, false);
            int id = Recover(ctx, ask, user, lost);
            return BomOut.Saved(ctx, kind, id, "新增");
        }

        static string Operator(WorkContext ctx)
        {
            string user = ctx.Item.Operator == null ? "" : ctx.Item.Operator.Trim();
            if (user.Length == 0)
            {
                throw new BridgeException(500, "internal", "缺少操作员");
            }
            return user;
        }

        // 版本生效日期缺省为登录日期，版本说明缺省空串，母件损耗率缺省 0。
        static void Prepare(object conn, BomAsk ask, string today)
        {
            ask.PartId = BomSql.PartOf(conn, ask.InvCode, true, today);
            ask.VersionDesc = ask.HeadText("version_desc") ?? "";
            ask.EffDate = ask.HeadText("eff_date") ?? today;
            object scrap;
            ask.ParentScrap = ask.Head.TryGetValue("parent_scrap", out scrap) ? (decimal)scrap : 0m;
            BomSql.PickVersion(conn, ask);
            ask.Rows = BomMerge.Build(ask);
            BomSql.CheckRows(conn, ask, ask.Rows, today);
            BomSql.FillDefaultWh(conn, ask.Rows, null, today);
        }

        static int Recover(WorkContext ctx, BomAsk ask, string user, Exception lost)
        {
            int id = 0;
            bool seen = true;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                List<Dictionary<string, object>> rows = BomSql.Found(conn, ask);
                seen = rows.Count > 0;
                id = rows.Count == 1 ? Accept(conn, ask, user, rows[0], lost == null) : 0;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "BomCreate " + MoApi.FirstLine(ex.Message));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (id > 0)
            {
                return id;
            }
            if (seen && lost != null)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 调用异常，可能已建成物料清单但无法确认，结果未知");
            }
            throw Unknown(lost);
        }

        static int Accept(object conn, BomAsk ask, string user, Dictionary<string, object> row, bool ok)
        {
            int id = CoRows.AsId(CoRows.Col(row, "bom_id"));
            if (id <= 0 || CoRows.Col(row, "fresh") != "1")
            {
                return 0;
            }
            if (ok)
            {
                return id;
            }
            bool mine = string.Equals(CoRows.Col(row, "maker"), user, StringComparison.OrdinalIgnoreCase);
            return mine && SameRows(BomRead.Lines(conn, id), ask.Rows) ? id : 0;
        }

        static bool SameRows(List<Dictionary<string, object>> lines, List<BomRow> rows)
        {
            if (lines.Count != rows.Count)
            {
                return false;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (BomSql.Int(CoRows.Col(lines[i], "sort_seq")) != rows[i].Seq
                    || !string.Equals(CoRows.Col(lines[i], "inv_code"), rows[i].InvCode, StringComparison.OrdinalIgnoreCase)
                    || BomSql.Dec(CoRows.Col(lines[i], "base_qty_n")) != rows[i].QtyN)
                {
                    return false;
                }
            }
            return true;
        }

        // lost 为空说明 InvokeApi 返回了成功（只是回读不到或认不了新版本）。
        static BridgeException Unknown(Exception lost)
        {
            if (lost == null)
            {
                return new BridgeException(504, "outcome_unknown", "U8 已返回成功但回读不到新物料清单，结果未知");
            }
            if (MoApi.IsIpc(lost.Message))
            {
                return new BridgeException(503, "u8_unavailable", "U8 生产制造服务未运行");
            }
            string text = MoApi.FirstLine(lost.Message);
            return new BridgeException(504, "outcome_unknown",
                "U8 新增物料清单调用异常，结果未知" + (text.Length > 0 ? "：" + text : ""));
        }
    }
}
