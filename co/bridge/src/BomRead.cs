using System.Collections.Generic;

namespace U8Co
{
    // 物料清单读取（vouchers/load，type=bom，id=bom_bom.BomId）：纯 SQL，读线程上跑（只用 ctx.Conn）。
    // 表头是 bom_bom + bom_parent + 母件 bas_part / Inventory，明细是 bom_opcomponent + bom_opcomponentopt + 子件。
    // 键名固定为小写下划线，值都是字符串，空值省略。code 是母件存货编码，另给 version。
    // 修改前的整行打底（RowOf）和写后回读也用这里的查询。
    internal static class BomRead
    {
        const int LinesCap = 5000;
        const string HeadSql = "select convert(varchar(20), b.BomId) as bom_id, convert(varchar(10), b.BomType) as bom_type,"
            + " bp.InvCode as inv_code, i.cInvName as inv_name, i.cInvStd as inv_std, convert(varchar(20), p.ParentId) as part_id,"
            + " convert(varchar(10), b.Version) as version, b.VersionDesc as version_desc,"
            + " convert(varchar(10), b.VersionEffDate, 23) as eff_date, convert(varchar(10), b.VersionEndDate, 23) as end_date,"
            + " convert(varchar(10), b.Status) as status, b.RelsUser as verifier, convert(varchar(10), b.RelsDate, 23) as verified_at,"
            + " convert(varchar(40), p.ParentScrap) as parent_scrap, convert(varchar(10), isnull(b.IsWFControlled,0)) as wf,"
            + " b.CreateUser as created_by, convert(varchar(19), b.CreateTime, 120) as created_at, b.ModifyUser as modified_by,"
            + " convert(varchar(19), b.ModifyTime, 120) as modified_at, convert(varchar(20), convert(bigint, b.Ufts)) as ufts"
            + " from bom_bom b join bom_parent p on p.BomId=b.BomId join bas_part bp on bp.PartId=p.ParentId"
            + " left join Inventory i on i.cInvCode=bp.InvCode where b.BomId=?";
        // extras=1：该行带替代料、定位符、分段损耗或自由项，差异修改带不回去（BomEdit 拒绝）；辅计量看 aux_unit。
        const string LinesSql = "select convert(varchar(20), c.OpComponentId) as line_id, convert(varchar(10), c.SortSeq) as sort_seq,"
            + " c.OpSeq as op_seq, cp.InvCode as inv_code, i.cInvName as inv_name, i.cInvStd as inv_std,"
            + " convert(varchar(40), c.BaseQtyN) as base_qty_n, convert(varchar(40), c.BaseQtyD) as base_qty_d,"
            + " convert(varchar(40), c.CompScrap) as comp_scrap, convert(varchar(10), o.WIPType) as wip_type, o.Whcode as wh_code,"
            + " o.DrawDeptCode as dept_code, convert(varchar(10), c.EffBegDate, 23) as eff_beg,"
            + " convert(varchar(10), c.EffEndDate, 23) as eff_end, c.Remark as remark, convert(varchar(10), c.FVFlag) as fv_flag,"
            + " convert(varchar(10), c.ProductType) as product_type, convert(varchar(10), isnull(c.ByproductFlag,0)) as byproduct_flag,"
            + " convert(varchar(10), o.Offset) as offset_days, convert(varchar(40), o.PlanFactor) as plan_rate,"
            + " convert(varchar(10), isnull(o.AccuCostFlag,0)) as accu_cost_flag, convert(varchar(10), isnull(o.OptionalFlag,0)) as optional_flag,"
            + " convert(varchar(10), o.MutexRule) as mutex_rule, convert(varchar(10), isnull(o.CostWIPRel,0)) as cost_wip_rel,"
            + " c.AuxUnitCode as aux_unit, " + Extras + " as extras"
            + " from bom_opcomponent c join bas_part cp on cp.PartId=c.ComponentId"
            + " left join bom_opcomponentopt o on o.OptionsId=c.OptionsId left join Inventory i on i.cInvCode=cp.InvCode"
            + " where c.BomId=? order by c.SortSeq, c.OpComponentId";
        const string Extras = "case when exists (select 1 from bom_opcomponentsub s where s.OpComponentId=c.OpComponentId)"
            + " or exists (select 1 from bom_opcomponentloc l where l.OpComponentId=c.OpComponentId)"
            + " or exists (select 1 from bom_opcomponentscrap x where x.OpComponentId=c.OpComponentId)"
            + " or coalesce(nullif(cp.Free1,N''), nullif(cp.Free2,N''), nullif(cp.Free3,N''), nullif(cp.Free4,N''),"
            + " nullif(cp.Free5,N''), nullif(cp.Free6,N''), nullif(cp.Free7,N''), nullif(cp.Free8,N''), nullif(cp.Free9,N''),"
            + " nullif(cp.Free10,N'')) is not null"
            + " then '1' else '0' end";

        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> head = Head(ctx.Conn, id);
            List<Dictionary<string, object>> lines = Lines(ctx.Conn, id);
            Dictionary<string, object> body = Body(kind, id, head);
            body["head"] = head;
            body["lines"] = lines;
            return ApiResult.Ok(body);
        }

        // 不存在 404；替代 BOM（BomType=2）本期不支持，400。
        internal static Dictionary<string, object> Head(object conn, int id)
        {
            Dictionary<string, object> head = Rows.One(conn, HeadSql, new object[] { id });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(head, "bom_type") != "1")
            {
                throw new BridgeException(400, "bad_request", "只支持标准物料清单（主 BOM），替代 BOM 请在 U8 客户端处理");
            }
            return head;
        }

        internal static List<Dictionary<string, object>> Lines(object conn, int id)
        {
            return Rows.Query(conn, LinesSql, new object[] { id }, LinesCap);
        }

        // 成功响应的公共部分：ok、type、id、code（母件存货编码）、version、state。
        internal static Dictionary<string, object> Body(VoucherKind kind, int id, Dictionary<string, object> head)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = CoRows.Col(head, "inv_code");
            body["version"] = BomSql.Int(CoRows.Col(head, "version"));
            body["state"] = State(head);
            return body;
        }

        // Status：1 未审核、3 已审核、4 停用。审核人、审核日期在 RelsUser / RelsDate。
        internal static Dictionary<string, object> State(Dictionary<string, object> head)
        {
            Dictionary<string, object> state = new Dictionary<string, object>();
            string status = CoRows.Col(head, "status");
            state["verified"] = status == "3";
            state["verifier"] = CoRows.Col(head, "verifier");
            state["verified_at"] = CoRows.Col(head, "verified_at");
            state["closed"] = status == "4";
            return state;
        }

        // 库里的一行 → 写给 U8 的整行（标志取现值，读不到按新增缺省）。
        internal static BomRow RowOf(Dictionary<string, object> line)
        {
            BomRow row = new BomRow();
            row.Seq = BomSql.Int(CoRows.Col(line, "sort_seq"));
            row.OpSeq = Or(CoRows.Col(line, "op_seq"), row.OpSeq);
            row.InvCode = CoRows.Col(line, "inv_code");
            row.QtyN = BomSql.Dec(CoRows.Col(line, "base_qty_n"));
            row.QtyD = BomSql.Dec(CoRows.Col(line, "base_qty_d"));
            row.Scrap = BomSql.Dec(CoRows.Col(line, "comp_scrap"));
            row.Wip = IntOr(line, "wip_type", row.Wip);
            row.Wh = CoRows.Col(line, "wh_code");
            row.Remark = CoRows.Col(line, "remark");
            row.EffBeg = CoRows.Col(line, "eff_beg");
            row.EffEnd = Or(CoRows.Col(line, "eff_end"), row.EffEnd);
            Flags(row, line);
            return row;
        }

        static void Flags(BomRow row, Dictionary<string, object> line)
        {
            row.FvFlag = IntOr(line, "fv_flag", row.FvFlag);
            row.ProductType = IntOr(line, "product_type", row.ProductType);
            row.Byproduct = IntOr(line, "byproduct_flag", row.Byproduct);
            row.Offset = IntOr(line, "offset_days", row.Offset);
            string plan = CoRows.Col(line, "plan_rate");
            row.PlanRate = plan.Length > 0 ? BomSql.Dec(plan) : row.PlanRate;
            row.AccuCost = IntOr(line, "accu_cost_flag", row.AccuCost);
            row.Optional = IntOr(line, "optional_flag", row.Optional);
            row.Mutex = IntOr(line, "mutex_rule", row.Mutex);
            row.CostWip = IntOr(line, "cost_wip_rel", row.CostWip);
        }

        static int IntOr(Dictionary<string, object> line, string key, int fallback)
        {
            string text = CoRows.Col(line, key);
            return text.Length > 0 ? BomSql.Int(text) : fallback;
        }

        static string Or(string text, string fallback)
        {
            return text.Length > 0 ? text : fallback;
        }
    }
}
