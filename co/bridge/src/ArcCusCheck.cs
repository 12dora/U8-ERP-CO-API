using System;
using System.Collections.Generic;

namespace U8Co
{
    // customer_fields 的逐键业务校验（在事务里、expect 核对之后）：引用的档案存在 / 末级 / 未停用（ArcCusRefs）、自定义项设置（ArcCusDefine）、
    // 停用日期与发展日期次序、简称与名称重复。只校验真正要改的键：已经等于当前值的不重复查
    // （比如旧数据的地区分类不是末级、协议已停用，原样保留不该拦），并把引用的编码换成档案里存的写法。
    internal static class ArcCusCheck
    {
        // 名称是否要求唯一取决于账套参数（AccInformation，系统 AA、编号 79、RptCusName=1），与 U8 的 CheckCusNameMul 一致。
        internal const string NameFlagSql = "SELECT cValue AS v FROM AccInformation"
            + " WHERE cSysID=N'AA' AND cID=N'79' AND cName=N'RptCusName' AND cValue=N'1'";
        internal const string NameTakenSql = "SELECT TOP 1 LTRIM(RTRIM(cCusCode)) AS code FROM Customer"
            + " WHERE cCusName=? AND cCusCode<>?";

        public static void Resolve(
            object conn, WorkContext ctx, ArcCusFieldsAsk ask, Dictionary<string, string> cur, string created)
        {
            for (int i = 0; i < ArcCusCols.All.Length; i++)
            {
                ArcCusCol col = ArcCusCols.All[i];
                string wanted;
                if (ask.Fields.TryGetValue(col.Key, out wanted))
                {
                    ask.Fields[col.Key] = Stored(conn, ctx, ask, col, wanted, cur[col.Key]);
                }
            }
            CheckDates(ask, cur, created);
            CheckAbbr(conn, ask, cur);
            CheckName(conn, ask, cur);
            ArcCusRefs.CheckHeadInvoice(conn, ask, cur);
        }

        // 请求值对应的写入值：与当前值相同（引用编码不分大小写）时保持当前值；否则按种类校验。
        static string Stored(
            object conn, WorkContext ctx, ArcCusFieldsAsk ask, ArcCusCol col, string wanted, string current)
        {
            if (col.Special == ArcCusCol.SpHead && wanted.Length == 0)
            {
                wanted = ask.Self;
            }
            if (string.Equals(wanted, current, ArcCusFieldsSql.ExpectCompare(col)))
            {
                return current;
            }
            if (col.Kind == ArcCusCol.KRef)
            {
                return ArcCusRefs.Stored(conn, ctx, ask, col, wanted);
            }
            if (col.Special == ArcCusCol.SpDefine)
            {
                return ArcCusDefine.Check(conn, col, wanted);
            }
            return wanted;
        }

        // 停用日期不早于发展日期（发展日期为空时不早于建档日期）：请求改了其中任何一个、结果与当前值不同、停用日期非空时才查。
        internal static void CheckDates(ArcCusFieldsAsk ask, Dictionary<string, string> cur, string created)
        {
            bool hasEnd = ask.Fields.ContainsKey(ArcCusFields.EndDate);
            bool hasSeed = ask.Fields.ContainsKey(ArcCusFields.SeedDate);
            if (!hasEnd && !hasSeed)
            {
                return;
            }
            string end = Effective(ask, cur, ArcCusFields.EndDate);
            string seed = Effective(ask, cur, ArcCusFields.SeedDate);
            bool changed = !string.Equals(end, cur[ArcCusFields.EndDate], StringComparison.Ordinal)
                || !string.Equals(seed, cur[ArcCusFields.SeedDate], StringComparison.Ordinal);
            string low = seed.Length > 0 ? seed : created;
            if (changed && end.Length > 0 && low.Length > 0 && string.CompareOrdinal(end, low) < 0)
            {
                throw DateOrder(seed, created, hasEnd);
            }
        }

        static BridgeException DateOrder(string seed, string created, bool hasEnd)
        {
            string message = seed.Length > 0 ? "停用日期不能早于发展日期 " + seed : "停用日期不能早于建档日期 " + created;
            return ArcReq.Bad(message, FieldPath.Join("fields", hasEnd ? ArcCusFields.EndDate : ArcCusFields.SeedDate));
        }

        static string Effective(ArcCusFieldsAsk ask, Dictionary<string, string> cur, string key)
        {
            string value;
            return ask.Fields.TryGetValue(key, out value) ? value : cur[key];
        }

        // 简称有变化（逐字比较）时查别的客户是否已用；与当前值相同不查。
        static void CheckAbbr(object conn, ArcCusFieldsAsk ask, Dictionary<string, string> cur)
        {
            string abbr;
            if (!ask.Fields.TryGetValue(ArcCusFields.Abbr, out abbr)
                || string.Equals(abbr, cur[ArcCusFields.Abbr], StringComparison.Ordinal))
            {
                return;
            }
            if (Rows.One(conn, ArcCusFieldsSql.AbbrTakenSql, new object[] { abbr, ask.Code }) != null)
            {
                throw ArcCusFieldsSql.AbbrTaken(ask);
            }
        }

        // 名称有变化且账套要求名称唯一时，查别的客户是否已用。
        static void CheckName(object conn, ArcCusFieldsAsk ask, Dictionary<string, string> cur)
        {
            string name;
            if (!ask.Fields.TryGetValue(ArcCusFields.Name, out name)
                || string.Equals(name, cur[ArcCusFields.Name], StringComparison.Ordinal)
                || Rows.One(conn, NameFlagSql, new object[0]) == null)
            {
                return;
            }
            if (Rows.One(conn, NameTakenSql, new object[] { name, ask.Code }) != null)
            {
                throw NameTaken(name);
            }
        }

        internal static BridgeException NameTaken(string name)
        {
            return new BridgeException(409, "name_taken", "客户名称已被其他客户使用：" + name,
                FieldPath.Join("fields", ArcCusFields.Name));
        }
    }
}
