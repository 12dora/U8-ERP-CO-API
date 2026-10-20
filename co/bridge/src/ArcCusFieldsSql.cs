using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户字段修改的受控 SQL：一个事务里 UPDLOCK, ROWLOCK 读 Customer，核对 expect、地区分类（须存在且末级）、
    // 停用日期（不早于建档日期），预演到此回滚；真写时只改有变化的列（加 cModifyPerson、dModifyDate），
    // 以 pubufts 作乐观锁，回读核对。
    internal static class ArcCusFieldsSql
    {
        static readonly string LockSql = ArcCustomerRow.LockSql(
            "LTRIM(RTRIM(ISNULL(cCusAbbName,''))) AS abbrname, LTRIM(RTRIM(ISNULL(cDCCode,''))) AS district,"
            + " ISNULL(CONVERT(varchar(10), dEndDate, 23),'') AS end_date,"
            + " ISNULL(CONVERT(varchar(10), dCusCreateDatetime, 23),'') AS created");
        const string DistrictSql = "SELECT LTRIM(RTRIM(cDCCode)) AS code FROM DistrictClass WHERE cDCCode=? AND bDCEnd=1";

        public static ApiResult Run(WorkContext ctx, ArcCusFieldsAsk ask)
        {
            return ArcCustomerRow.Transact(ctx, "customer_fields " + ask.Code,
                delegate(object conn) { return Apply(conn, ctx, ask); });
        }

        static ApiResult Apply(object conn, WorkContext ctx, ArcCusFieldsAsk ask)
        {
            Dictionary<string, object> row = ArcCustomerRow.Need(conn, LockSql, ask.Code);
            Dictionary<string, string> cur = Current(row);
            Gate(ask, cur);
            Resolve(conn, ask, cur, ArcCustomerRow.Cell(row, "created"));
            if (DryRun.Active)
            {
                DryRun.StopWith(ctx, ArcCusFields.DryBody(ask, cur));
            }
            if (!ArcCusFields.Differs(cur, ask.Fields))
            {
                return ApiResult.Ok(ArcCusFields.OkBody(ask, cur, false));
            }
            string ts = ArcCustomerRow.Cell(row, "ts");
            Write(conn, ctx, ask, cur, ts);
            Confirm(conn, ask, ts);
            return ApiResult.Ok(ArcCusFields.OkBody(ask, cur, true));
        }

        static Dictionary<string, string> Current(Dictionary<string, object> row)
        {
            Dictionary<string, string> cur = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int i = 0; i < ArcCusFields.Keys.Length; i++)
            {
                cur[ArcCusFields.Keys[i]] = ArcCustomerRow.Cell(row, ArcCusFields.Keys[i]);
            }
            return cur;
        }

        // expect 里每个键的当前值都要对上；不符的键一并列出。
        internal static void Gate(ArcCusFieldsAsk ask, Dictionary<string, string> cur)
        {
            if (ask.Expect == null)
            {
                return;
            }
            List<string> bad = new List<string>();
            for (int i = 0; i < ArcCusFields.Keys.Length; i++)
            {
                string key = ArcCusFields.Keys[i];
                string want;
                if (ask.Expect.TryGetValue(key, out want) && !string.Equals(want, cur[key], ExpectCompare(key)))
                {
                    bad.Add(key + " 当前是" + (cur[key].Length == 0 ? "空" : cur[key]));
                }
            }
            if (bad.Count > 0)
            {
                throw new BridgeException(409, "state_mismatch", "与 expect 不符：" + string.Join("；", bad.ToArray()));
            }
        }

        // 地区分类编码在库里不分大小写，其余字段逐字比较。
        static StringComparison ExpectCompare(string key)
        {
            return key == ArcCusFields.District ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        }

        // 只校验真正要改的键：已经等于目标的不重复查（比如旧数据的地区分类不是末级，原样保留不该拦）。
        // 地区分类按库的比较规则（不分大小写）判断是否有变化，并把请求值换成库里存的写法，避免写出大小写不同的编码。
        static void Resolve(object conn, ArcCusFieldsAsk ask, Dictionary<string, string> cur, string created)
        {
            string district;
            if (ask.Fields.TryGetValue(ArcCusFields.District, out district))
            {
                ask.Fields[ArcCusFields.District] = StoredDistrict(conn, district, cur[ArcCusFields.District]);
            }
            string end;
            if (ask.Fields.TryGetValue(ArcCusFields.EndDate, out end) && end.Length > 0 && created.Length > 0
                && !string.Equals(end, cur[ArcCusFields.EndDate], StringComparison.Ordinal)
                && string.CompareOrdinal(end, created) < 0)
            {
                throw ArcReq.Bad("停用日期不能早于建档日期 " + created, FieldPath.Join("fields", ArcCusFields.EndDate));
            }
        }

        // 请求的地区分类对应的写入值：清空或与当前值只差大小写时保持当前值；否则必须是末级，返回 DistrictClass 里存的编码。
        static string StoredDistrict(object conn, string wanted, string current)
        {
            if (string.Equals(wanted, current, StringComparison.OrdinalIgnoreCase))
            {
                return current;
            }
            if (wanted.Length == 0)
            {
                return wanted;
            }
            Dictionary<string, object> row = Rows.One(conn, DistrictSql, new object[] { wanted });
            if (row == null)
            {
                throw new BridgeException(409, "district_invalid", "地区分类不存在或不是末级：" + wanted);
            }
            return ArcCustomerRow.Cell(row, "code");
        }

        static void Write(object conn, WorkContext ctx, ArcCusFieldsAsk ask, Dictionary<string, string> cur, string ts)
        {
            List<string> sets = new List<string>();
            List<object> args = new List<object>();
            for (int i = 0; i < ArcCusFields.Keys.Length; i++)
            {
                string key = ArcCusFields.Keys[i];
                string value;
                if (ask.Fields.TryGetValue(key, out value) && !string.Equals(cur[key], value, StringComparison.Ordinal))
                {
                    sets.Add(SetOf(key, value, args));
                }
            }
            ArcCustomerRow.Update(conn, ctx, string.Join(", ", sets.ToArray()), args.ToArray(), ask.Code, ts);
        }

        // 列名只来自固定表；空值清空写 NULL，其余值走参数。
        internal static string SetOf(string key, string value, List<object> args)
        {
            string column = key == ArcCusFields.Abbr ? "cCusAbbName"
                : key == ArcCusFields.District ? "cDCCode" : "dEndDate";
            if (value.Length == 0)
            {
                return column + "=NULL";
            }
            args.Add(value);
            return key == ArcCusFields.EndDate
                ? column + "=CONVERT(datetime, CONVERT(date, ?, 23))" : column + "=?";
        }

        // 回读：每个请求的键都等于目标才算成功；否则 pubufts 没变说明 UPDATE 没命中（别人改过，409），变了则结果未知（504）。
        static void Confirm(object conn, ArcCusFieldsAsk ask, string ts)
        {
            Dictionary<string, object> row = ArcCustomerRow.Lock(conn, LockSql, ask.Code);
            if (row == null)
            {
                throw ArcCustomerRow.Unknown(ask.Code, "字段");
            }
            Dictionary<string, string> now = Current(row);
            foreach (KeyValuePair<string, string> pair in ask.Fields)
            {
                if (!string.Equals(now[pair.Key], pair.Value, StringComparison.Ordinal))
                {
                    throw ArcCustomerRow.Cell(row, "ts") == ts
                        ? ArcCustomerRow.Concurrent(ask.Code) : ArcCustomerRow.Unknown(ask.Code, "字段");
                }
            }
        }
    }
}
