using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 物料清单的 SQL：调用 U8 之前在请求连接上查完能查的（母件、子件、仓库、版本、生效日期），调用后在新连接上回读。
    // 表名、列名都是固定的 U8 表；调用方的值只进 ADO 参数。只认标准 BOM（BomType=1）。
    internal static class BomSql
    {
        // 存货：是否自制件、能否做母件 / 子件、停用日期，以及不带自由项的物料 PartId（bom_parent.ParentId 指向它）。
        const string InvSql = "select convert(varchar(10), isnull(i.bSelf,0)) as self,"
            + " convert(varchar(10), isnull(i.bBomMain,0)) as main, convert(varchar(10), isnull(i.bBomSub,0)) as sub,"
            + " convert(varchar(10), i.dEDate, 23) as ended, convert(varchar(20), (select min(bp.PartId) from bas_part bp"
            + " where bp.InvCode=i.cInvCode and coalesce(nullif(bp.Free1,N''), nullif(bp.Free2,N''), nullif(bp.Free3,N''),"
            + " nullif(bp.Free4,N''), nullif(bp.Free5,N''), nullif(bp.Free6,N''), nullif(bp.Free7,N''), nullif(bp.Free8,N''),"
            + " nullif(bp.Free9,N''), nullif(bp.Free10,N'')) is null)) as part_id"
            + " from Inventory i where i.cInvCode=?";
        // 该母件全部标准 BOM 版本（版本号、生效日期都不能重复）。
        const string VersionsSql = "select convert(varchar(20), b.BomId) as bom_id, convert(varchar(10), b.Version) as version,"
            + " convert(varchar(10), b.VersionEffDate, 23) as eff from bom_bom b join bom_parent p on p.BomId=b.BomId"
            + " where p.ParentId=? and b.BomType=1";
        const string StepSql = "select top 1 convert(varchar(10), VersionIncrement) as n from mom_parameter";
        const string WhSql = "select convert(varchar(10), dWhEndDate, 23) as ended from Warehouse where cWhCode=?";
        const string NowSql = "select convert(varchar(23), getdate(), 121) as t";
        // 新增之后按（母件、版本、主 BOM）找：fresh 表示创建时间不早于调用前的数据库时间。
        const string NewSql = "select convert(varchar(20), b.BomId) as bom_id,"
            + " case when b.CreateTime >= convert(datetime, ?, 121) then '1' else '0' end as fresh, b.CreateUser as maker"
            + " from bom_bom b join bom_parent p on p.BomId=b.BomId where p.ParentId=? and b.Version=? and b.BomType=1";
        // 删除闸门：生产订单、委外订单、组装 / 拆卸 / 形态转换单（AssemVouch）、配比出库单（MatchVouch 表头或表体）、调拨单的 BomId 指向它。
        // U8 删除不查生产订单（实测 B6），其余几类没实测，桥一并拦住。
        const string RefSql = "select (select count(*) from mom_orderdetail where BomId=?) as mo,"
            + " (select count(*) from OM_MODetails where BomId=?) as om, (select count(*) from AssemVouch where BomId=?) as assem,"
            + " (select count(*) from MatchVouch where BomId=?) + (select count(*) from MatchVouchs where Bomid=?) as matched,"
            + " (select count(*) from TransVouch where BomId=?) as trans";
        const string GoneSql = "select count(*) as n from bom_bom where BomId=?";
        const int DefaultStep = 10;

        internal static int PartOf(object conn, string inv, bool parent, string today)
        {
            Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { inv });
            string who = parent ? "母件" : "子件";
            if (row == null)
            {
                throw Bad(who + "存货不存在：" + inv);
            }
            Capable(row, inv, parent);
            string ended = CoRows.Col(row, "ended");
            if (ended.Length > 0 && string.CompareOrdinal(ended, today) <= 0)
            {
                throw Bad("存货 " + inv + " 已停用");
            }
            int part = CoRows.AsId(CoRows.Col(row, "part_id"));
            if (part <= 0)
            {
                throw Bad("存货 " + inv + " 没有物料档案（bas_part）");
            }
            return part;
        }

        // 母件要自制且「允许 BOM 母件」（U8 1011034），子件要「允许 BOM 子件」（U8 1020001）。
        static void Capable(Dictionary<string, object> row, string inv, bool parent)
        {
            if (parent && (CoRows.Col(row, "self") != "1" || CoRows.Col(row, "main") != "1"))
            {
                throw Bad("存货 " + inv + " 不是可做物料清单母件的自制件");
            }
            if (!parent && CoRows.Col(row, "sub") != "1")
            {
                throw Bad("存货 " + inv + " 不允许做物料清单子件");
            }
        }

        // 子件：存在、可做子件、不是母件本身；有仓库就查仓库。同一存货只查一次。
        internal static void CheckRows(object conn, BomAsk ask, List<BomRow> rows, string today)
        {
            Dictionary<string, int> seen = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            HashSet<string> whs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < rows.Count; i++)
            {
                BomRow row = rows[i];
                if (!seen.ContainsKey(row.InvCode))
                {
                    seen[row.InvCode] = PartOf(conn, row.InvCode, false, today);
                }
                if (seen[row.InvCode] == ask.PartId)
                {
                    throw Bad("第 " + BomReq.Num(row.Seq) + " 行：子件不能是母件本身");
                }
                if (row.Wh.Length > 0 && whs.Add(row.Wh))
                {
                    CheckWh(conn, row.Wh, today);
                }
            }
        }

        // 新加的行没给仓库时，U8 的 Component.SetPartId 会填存货的默认仓库（实测）；桥先按同样规则补上并照发，
        // 回读核对才对得上。只补有效（未停用、非资产）的默认仓库，已有行不补（U8 对已有行不改仓库）。
        const string DefWhSql = "select i.cDefWareHouse as wh from Inventory i join Warehouse w on w.cWhCode=i.cDefWareHouse"
            + " where i.cInvCode=? and isnull(w.bWhAsset,0)=0"
            + " and (w.dWhEndDate is null or convert(varchar(10), w.dWhEndDate, 23) > ?)";

        internal static void FillDefaultWh(object conn, List<BomRow> rows, HashSet<int> existing, string today)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                BomRow row = rows[i];
                if (row.Wh.Length > 0 || (existing != null && existing.Contains(row.Seq)))
                {
                    continue;
                }
                Dictionary<string, object> hit = Rows.One(conn, DefWhSql, new object[] { row.InvCode, today });
                if (hit != null)
                {
                    row.Wh = CoRows.Col(hit, "wh").Trim();
                }
            }
        }

        static void CheckWh(object conn, string wh, string today)
        {
            Dictionary<string, object> row = Rows.One(conn, WhSql, new object[] { wh });
            if (row == null)
            {
                throw Bad("仓库不存在：" + wh);
            }
            string ended = CoRows.Col(row, "ended");
            if (ended.Length > 0 && string.CompareOrdinal(ended, today) <= 0)
            {
                throw Bad("仓库 " + wh + " 已停用");
            }
        }

        // 新增：版本号省略时取最大版本加增量；给了就不能与现有版本重复。生效日期不能与该母件其他版本相同（U8 1011030）。
        internal static void PickVersion(object conn, BomAsk ask)
        {
            List<Dictionary<string, object>> rows = Versions(conn, ask.PartId);
            int max = 0;
            for (int i = 0; i < rows.Count; i++)
            {
                int version = Int(CoRows.Col(rows[i], "version"));
                max = Math.Max(max, version);
                if (ask.Version > 0 && version == ask.Version)
                {
                    throw new BridgeException(409, "state_mismatch", "物料清单版本已存在：" + ask.InvCode + " " + ask.VersionText);
                }
            }
            if (ask.Version == 0)
            {
                ask.Version = max + Step(conn);
            }
            CheckEffDate(rows, ask.EffDate, 0);
        }

        // 同一母件的其他版本（不含 self）已用这个生效日期：409。
        internal static void CheckEffDate(List<Dictionary<string, object>> rows, string eff, int self)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (CoRows.Col(rows[i], "eff") == eff && CoRows.AsId(CoRows.Col(rows[i], "bom_id")) != self)
                {
                    throw new BridgeException(409, "state_mismatch",
                        "生效日期 " + eff + " 已被版本 " + CoRows.Col(rows[i], "version") + " 使用");
                }
            }
        }

        internal static List<Dictionary<string, object>> Versions(object conn, int partId)
        {
            return Rows.Query(conn, VersionsSql, new object[] { partId }, 10000);
        }

        static int Step(object conn)
        {
            int n = Int(Rows.Scalar(conn, StepSql, new object[0]));
            return n > 0 ? n : DefaultStep;
        }

        internal static string Now(object conn)
        {
            string text = Rows.Scalar(conn, NowSql, new object[0]);
            if (string.IsNullOrEmpty(text))
            {
                throw new BridgeException(500, "internal", "读不到数据库时间");
            }
            return text;
        }

        // 按（母件、版本）找本次新建的主 BOM；返回全部命中行（正常只有一行）。
        internal static List<Dictionary<string, object>> Found(object conn, BomAsk ask)
        {
            return Rows.Query(conn, NewSql, new object[] { ask.Since, ask.PartId, ask.Version }, 10);
        }

        // 删除闸门：任一单据的 BomId 指向它就 409，说明是哪类单据。
        internal static void CheckUnused(object conn, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, RefSql, new object[] { id, id, id, id, id, id });
            string[] keys = new string[] { "mo", "om", "assem", "matched", "trans" };
            string[] names = new string[] { "生产订单", "委外订单", "组装、拆卸或形态转换单", "配比出库单", "调拨单" };
            for (int i = 0; i < keys.Length; i++)
            {
                if (Int(CoRows.Col(row, keys[i])) > 0)
                {
                    throw new BridgeException(409, "state_mismatch", names[i] + "已引用该物料清单，不能删除");
                }
            }
        }

        internal static bool Gone(object conn, int id)
        {
            string n = Rows.Scalar(conn, GoneSql, new object[] { id });
            return n != null && n.Trim() == "0";
        }

        internal static int Int(string text)
        {
            int n;
            if (text != null && int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            return 0;
        }

        internal static decimal Dec(string text)
        {
            decimal value;
            if (decimal.TryParse((text ?? "").Trim(), NumberStyles.Number, CultureInfo.InvariantCulture, out value))
            {
                return value;
            }
            return 0m;
        }

        internal static BridgeException Bad(string text)
        {
            return new BridgeException(400, "bad_request", text);
        }
    }
}
