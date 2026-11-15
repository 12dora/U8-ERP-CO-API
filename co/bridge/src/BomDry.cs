using System.Collections.Generic;

namespace U8Co
{
    // 物料清单写预演（校验模式）：BomAdd / BomUpdate / BomAuditing / BomUnauditing / BomDelete 都由 U8API 自己提交，
    // 桥查完闸门后在 BomCom.Run 调用 U8 之前停（DryRun.Stop）。detail.input 带上要送给 U8 的规整结果。
    internal static class BomDry
    {
        public static void SaveInput(BomAsk ask, bool update)
        {
            if (!DryRun.Active)
            {
                return;
            }
            List<object> lines = new List<object>();
            for (int i = 0; i < ask.Rows.Count; i++)
            {
                lines.Add(RowOf(ask.Rows[i]));
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["update_by_diff"] = update;
            input["inv_code"] = ask.InvCode;
            input["version"] = ask.Version;
            input["version_desc"] = ask.VersionDesc;
            input["eff_date"] = ask.EffDate;
            input["parent_scrap"] = ask.ParentScrap;
            input["lines"] = lines;
            DryRun.Set("input", input);
        }

        public static void TripleInput(int partId, string version)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["part_id"] = partId;
            input["version"] = version;
            DryRun.Set("input", input);
        }

        static Dictionary<string, object> RowOf(BomRow row)
        {
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["sort_seq"] = row.Seq;
            line["op_seq"] = row.OpSeq;
            line["inv_code"] = row.InvCode;
            line["base_qty_n"] = row.QtyN;
            line["base_qty_d"] = row.QtyD;
            line["comp_scrap"] = row.Scrap;
            line["wip_type"] = row.Wip;
            line["wh_code"] = row.Wh;
            line["remark"] = row.Remark;
            line["eff_beg"] = row.EffBeg;
            line["eff_end"] = row.EffEnd;
            return line;
        }
    }
}
