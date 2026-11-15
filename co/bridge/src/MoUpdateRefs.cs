using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // MOrderUpdate 把子件删了重插，AllocateId 全换新：任何按 AllocateId 指向这张订单子件的行都会变成孤儿。
    // 调用前查下面这些表（表名、列名是常量；账套里没有的表或列先查 sys.columns 跳过），有一行就 409。
    // rdrecords10.iMPoIds 指的是订单行 MoDId，不在这里（MoUpdateSql 的下游闸门已查）。
    // rdrecords11.iMPoIds（材料出库）不在这里：桥能按（行、行号、存货）确定新子件，修改后由 MoUpdateRemap 改写；
    // 其余表（领料申请、调拨、替代料、计划、历史表、材料出库的序列号明细 ST_SNDetail_MaOut 等）桥改不准，仍一律 409。
    internal static class MoUpdateRefs
    {
        static readonly string[][] Refs = new string[][]
        {
            new string[] { "mom_moallocatesub", "AllocateId" }, new string[] { "mom_moallocatescrap", "AllocateId" },
            new string[] { "mom_moallocateShortDetail", "AllocateId" }, new string[] { "mom_moallocatehistory", "AllocateId" },
            new string[] { "MaterialOut_SubTable", "AllocateId" }, new string[] { "MaterialRecordOutList", "allocateid" },
            new string[] { "mom_replenishapplydtl", "MoAllocateId" }, new string[] { "mps_demand", "AllocateId" },
            new string[] { "mps_refonlinedata", "AllocateId" }, new string[] { "mps_schedule", "AllocateId" },
            new string[] { "mps_schedule_closedcomp", "AllocateId" }, new string[] { "mps_ssr_reserve", "AllocateId" },
            new string[] { "mps_substitute", "AllocateId" }, new string[] { "CJTM_RefOnlineData", "AllocateId" },
            new string[] { "CJTM_ZJYL_occupyRes", "AllocateId" }, new string[] { "hy_dz_scdd_moallocate", "AllocateId" },
            new string[] { "SR_ServiceItems", "AllocateId" }, new string[] { "mom_orderdetail", "PAllocateId" },
            new string[] { "mom_orderdetail_ref", "PAllocateId" }, new string[] { "MaterialAppVouchs", "iMPoIds" },
            new string[] { "ST_AppTransVouchs", "iMPoIds" }, new string[] { "MatchVouchs", "iMPoIds" },
            new string[] { "ScrapVouchs", "iMPoIds" },
            new string[] { "qt_modetails", "iMPoIds" }, new string[] { "TransVouchs", "iMPoIds" },
            new string[] { "TransVouchsHistory", "iMPoIds" }, new string[] { "MaterialAppVouchsHistory", "iMPoIds" },
            new string[] { "RdRecords11History", "iMPoIds" }, new string[] { "qt_modetailses", "impoids" },
            new string[] { "ST_SNDetail_MaOut", "impoids" }
        };
        const string Allocs = "select a.AllocateId from mom_moallocate a join mom_orderdetail d on d.MoDId=a.MoDId where d.MoId=?";
        public const string Refused = "生产订单子件已被其他单据或计划引用，不能修改";

        public static void Gate(object conn, int id)
        {
            List<string[]> found = Existing(conn);
            if (found.Count == 0)
            {
                return;
            }
            object[] args = new object[found.Count];
            for (int i = 0; i < args.Length; i++)
            {
                args[i] = id;
            }
            string hit = Rows.Scalar(conn, ProbeSql(found), args);
            if (!string.IsNullOrEmpty(hit))
            {
                throw new BridgeException(409, "state_mismatch", Refused + "（" + hit.Trim() + "）");
            }
        }

        // 账套里实际有的（表、列）。
        static List<string[]> Existing(object conn)
        {
            StringBuilder sb = new StringBuilder("select t.name as t, c.name as c from sys.columns c"
                + " join sys.tables t on t.object_id=c.object_id where 1=0");
            for (int i = 0; i < Refs.Length; i++)
            {
                sb.Append(" or (t.name='").Append(Refs[i][0]).Append("' and c.name='").Append(Refs[i][1]).Append("')");
            }
            List<Dictionary<string, object>> rows = Rows.Query(conn, sb.ToString(), new object[0], Refs.Length * 2);
            List<string[]> found = new List<string[]>();
            for (int i = 0; i < Refs.Length; i++)
            {
                if (Has(rows, Refs[i]))
                {
                    found.Add(Refs[i]);  // 拼进 SQL 的只用常量名单里的名字
                }
            }
            return found;
        }

        static bool Has(List<Dictionary<string, object>> rows, string[] pair)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (string.Equals(CoRows.Col(rows[i], "t"), pair[0], System.StringComparison.OrdinalIgnoreCase)
                    && string.Equals(CoRows.Col(rows[i], "c"), pair[1], System.StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        // 名单里有没有这一对（表、列），--selftest 用。
        internal static bool Lists(string table, string column)
        {
            for (int i = 0; i < Refs.Length; i++)
            {
                if (string.Equals(Refs[i][0], table, System.StringComparison.OrdinalIgnoreCase)
                    && string.Equals(Refs[i][1], column, System.StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        // 一条查询：每张表一个 exists 分支，命中返回表名。每个分支一个 ?（MoId）。
        internal static string ProbeSql(List<string[]> found)
        {
            StringBuilder sb = new StringBuilder("select top 1 x.t from (");
            for (int i = 0; i < found.Count; i++)
            {
                if (i > 0)
                {
                    sb.Append(" union all ");
                }
                sb.Append("select '").Append(found[i][0]).Append("' as t where exists (select 1 from ").Append(found[i][0])
                    .Append(" r where r.").Append(found[i][1]).Append(" in (").Append(Allocs).Append("))");
            }
            return sb.Append(") x").ToString();
        }
    }
}
