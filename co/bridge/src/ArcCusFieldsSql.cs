using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户字段修改的受控 SQL：一个事务里 UPDLOCK, ROWLOCK 读 Customer，核对 expect、地区分类（须存在且末级）、
    // 停用日期（不早于建档日期）、收付款协议（须在 AA_Agreement 且未停用）、简称不与其他客户重复，预演到此回滚；
    // 真写时只改有变化的列（加 cModifyPerson、dModifyDate），以 pubufts 作乐观锁，回读核对。
    // 名称、税号不唯一，不查重；改名不回写历史单据上的名称副本，也不重算助记码。
    internal static class ArcCusFieldsSql
    {
        // 别名必须与 ArcCusFields.Keys 一致：缺别名时 Cell 取到空串，当前值会被悄悄当成空（selftest 逐个核对）。
        internal static readonly string LockSql = ArcCustomerRow.LockSql(
            "LTRIM(RTRIM(ISNULL(cCusAbbName,''))) AS abbrname, LTRIM(RTRIM(ISNULL(cDCCode,''))) AS district,"
            + " ISNULL(CONVERT(varchar(10), dEndDate, 23),'') AS end_date,"
            + " LTRIM(RTRIM(ISNULL(cCusSAProtocol,''))) AS sa_protocol,"
            + " LTRIM(RTRIM(ISNULL(cCusName,''))) AS name, LTRIM(RTRIM(ISNULL(cCusRegCode,''))) AS tax_no,"
            + " ISNULL(CONVERT(varchar(10), dCusCreateDatetime, 23),'') AS created");
        // 简称在 Customer 上有唯一索引：按库的排序规则（不分大小写）找别的客户是否已用。
        internal const string AbbrTakenSql = "SELECT TOP 1 LTRIM(RTRIM(cCusCode)) AS code FROM Customer"
            + " WHERE cCusAbbName=? AND cCusCode<>?";
        const string DistrictSql = "SELECT LTRIM(RTRIM(cDCCode)) AS code FROM DistrictClass WHERE cDCCode=? AND bDCEnd=1";
        // 收付款协议档案不分销售 / 采购（表上没有侧别标志）；停用只看 dEDate。
        internal const string ProtocolSql = "SELECT LTRIM(RTRIM(cCode)) AS code,"
            + " ISNULL(CONVERT(varchar(10), dEDate, 23),'') AS ended FROM AA_Agreement WHERE cCode=?";

        // 键 → Customer 列名。只认这张表，没登记的键不拼 SQL（防止落到别的列）。
        static readonly Dictionary<string, string> Columns = ColumnMap();

        // 预查之后仍撞上简称唯一索引（2627 / 2601，并发写入）按 409 abbrname_taken，不当 500。
        // 只有请求改了简称才这样映射（Customer 上只有简称这一个可能撞上的唯一索引）；没改简称的重复键原样上抛。
        // 预演（DryRunDone）原样上抛。
        public static ApiResult Run(WorkContext ctx, ArcCusFieldsAsk ask)
        {
            try
            {
                return ArcCustomerRow.Transact(ctx, "customer_fields " + ask.Code,
                    delegate(object conn) { return Apply(conn, ctx, ask); });
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception ex)
            {
                if (AbbrCollision(ask, ex))
                {
                    throw AbbrTaken(ask);
                }
                throw;
            }
        }

        // 重复键且请求 fields 里有简称：才按 abbrname_taken。
        internal static bool AbbrCollision(ArcCusFieldsAsk ask, Exception ex)
        {
            return ask != null && ask.Fields != null && ask.Fields.ContainsKey(ArcCusFields.Abbr) && ArcGl.DuplicateKey(ex);
        }

        internal static BridgeException AbbrTaken(ArcCusFieldsAsk ask)
        {
            string abbr;
            ask.Fields.TryGetValue(ArcCusFields.Abbr, out abbr);
            return new BridgeException(409, "abbrname_taken", "简称已被其他客户使用：" + (abbr ?? ""),
                FieldPath.Join("fields", ArcCusFields.Abbr));
        }

        static ApiResult Apply(object conn, WorkContext ctx, ArcCusFieldsAsk ask)
        {
            Dictionary<string, object> row = ArcCustomerRow.Need(conn, LockSql, ask.Code);
            Dictionary<string, string> cur = Current(row);
            Gate(ask, cur);
            Resolve(conn, ask, cur, ArcCustomerRow.Cell(row, "created"), ctx.Item.Date);
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

        // expect 里每个键的当前值都要对上；不符的键一并列在消息里，detail.current 给 fields 各键的当前值。
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
                throw ArcCustomerRow.Mismatch("与 expect 不符：" + string.Join("；", bad.ToArray()),
                    ArcCusFields.CurrentOf(cur, ask.Fields));
            }
        }

        // 地区分类、收付款协议编码在库里不分大小写，其余字段逐字比较。
        static StringComparison ExpectCompare(string key)
        {
            return key == ArcCusFields.District || key == ArcCusFields.SaProtocol
                ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        }

        // 只校验真正要改的键：已经等于目标的不重复查（比如旧数据的地区分类不是末级、协议已停用，原样保留不该拦）。
        // 地区分类、收付款协议按库的比较规则（不分大小写）判断是否有变化，并把请求值换成库里存的写法，
        // 避免写出大小写不同的编码。date 是登录日期，判断协议是否已停用。
        static void Resolve(
            object conn, ArcCusFieldsAsk ask, Dictionary<string, string> cur, string created, string date)
        {
            string district;
            if (ask.Fields.TryGetValue(ArcCusFields.District, out district))
            {
                ask.Fields[ArcCusFields.District] = StoredDistrict(conn, district, cur[ArcCusFields.District]);
            }
            string protocol;
            if (ask.Fields.TryGetValue(ArcCusFields.SaProtocol, out protocol))
            {
                ask.Fields[ArcCusFields.SaProtocol] =
                    StoredProtocol(conn, protocol, cur[ArcCusFields.SaProtocol], date);
            }
            CheckEndDate(ask, cur, created);
            CheckAbbr(conn, ask, cur);
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
            if (Rows.One(conn, AbbrTakenSql, new object[] { abbr, ask.Code }) != null)
            {
                throw AbbrTaken(ask);
            }
        }

        // 停用日期不能早于建档日期（与当前值相同、清空、建档日期未知时不查）。
        static void CheckEndDate(ArcCusFieldsAsk ask, Dictionary<string, string> cur, string created)
        {
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

        // 请求的收付款协议对应的写入值：清空或与当前值只差大小写时保持当前值（不查存在 / 停用）；
        // 否则必须在 AA_Agreement 里且未停用，返回档案里存的编码。
        static string StoredProtocol(object conn, string wanted, string current, string date)
        {
            if (string.Equals(wanted, current, StringComparison.OrdinalIgnoreCase))
            {
                return current;
            }
            if (wanted.Length == 0)
            {
                return wanted;
            }
            Dictionary<string, object> row = Rows.One(conn, ProtocolSql, new object[] { wanted });
            if (row == null)
            {
                throw new BridgeException(409, "sa_protocol_invalid", "收付款协议不存在：" + wanted);
            }
            if (Stopped(ArcCustomerRow.Cell(row, "ended"), date))
            {
                throw new BridgeException(409, "sa_protocol_invalid", "收付款协议已停用：" + wanted);
            }
            return ArcCustomerRow.Cell(row, "code");
        }

        // 停用日期非空且不晚于登录日期即为已停用（与业务员 dPInValidDate 同口径）；登录日期未知时不拦。
        internal static bool Stopped(string ended, string date)
        {
            return date != null && date.Length > 0 && ended != null && ended.Length > 0
                && string.CompareOrdinal(ended, date) <= 0;
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

        // 列名只来自固定表 Columns；表外的键是内部错误。空值清空写 NULL，其余值走参数。
        internal static string SetOf(string key, string value, List<object> args)
        {
            string column;
            if (key == null || !Columns.TryGetValue(key, out column))
            {
                throw new BridgeException(500, "internal", "客户字段没有对应的列：" + key);
            }
            if (value.Length == 0)
            {
                return column + "=NULL";
            }
            args.Add(value);
            return key == ArcCusFields.EndDate
                ? column + "=CONVERT(datetime, CONVERT(date, ?, 23))" : column + "=?";
        }

        static Dictionary<string, string> ColumnMap()
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
            map[ArcCusFields.Abbr] = "cCusAbbName";
            map[ArcCusFields.District] = "cDCCode";
            map[ArcCusFields.EndDate] = "dEndDate";
            map[ArcCusFields.SaProtocol] = "cCusSAProtocol";
            map[ArcCusFields.Name] = "cCusName";
            map[ArcCusFields.TaxNo] = "cCusRegCode";
            return map;
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
