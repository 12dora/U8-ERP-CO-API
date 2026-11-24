using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户字段修改的受控 SQL：一个事务里 UPDLOCK, ROWLOCK 读 Customer（只读请求的列和校验要用的列），核对 expect，
    // ArcCusCheck 逐键校验（档案引用、日期次序、简称 / 名称重复），预演到此回滚；
    // 真写时只改有变化的列（加 cModifyPerson、dModifyDate），以 pubufts 作乐观锁，回读核对，再同步对应供应商的反向关联。
    // 改名不回写历史单据上的名称副本，也不重算助记码。
    internal static class ArcCusFieldsSql
    {
        // 简称在 Customer 上有唯一索引：按库的排序规则（不分大小写）找别的客户是否已用。
        internal const string AbbrTakenSql = "SELECT TOP 1 LTRIM(RTRIM(cCusCode)) AS code FROM Customer"
            + " WHERE cCusAbbName=? AND cCusCode<>?";
        // 读出的基础列：库里存的编码、建档日期（停用日期的下限）；别名不能与声明表的键重名。
        const string BaseColumns = "LTRIM(RTRIM(cCusCode)) AS ccode,"
            + " ISNULL(CONVERT(varchar(10), dCusCreateDatetime, 23),'') AS created";

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
            List<string> keys = ArcCusCols.Loaded(ask.Fields.Keys);
            string lockSql = LockSqlOf(keys);
            Dictionary<string, object> row = ArcCustomerRow.Need(conn, lockSql, ask.Code);
            Dictionary<string, string> cur = Current(row, keys);
            ask.Self = ArcCustomerRow.Cell(row, "ccode");
            Gate(ask, cur);
            ArcCusCheck.Resolve(conn, ctx, ask, cur, ArcCustomerRow.Cell(row, "created"));
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
            Confirm(conn, ask, ts, lockSql, keys);
            ArcCusRefs.SyncVendor(conn, ask, cur);
            return ApiResult.Ok(ArcCusFields.OkBody(ask, cur, true));
        }

        // 锁行读取的 SQL：只含 keys 里的列（请求的键和校验要用的键）、基础列、行版本；别名就是请求键。
        internal static string LockSqlOf(List<string> keys)
        {
            List<string> columns = new List<string>();
            columns.Add(BaseColumns);
            for (int i = 0; i < keys.Count; i++)
            {
                ArcCusCol col = ArcCusCols.Of(keys[i]);
                columns.Add(Expr(col.Kind, col.Column) + " AS " + col.Key);
                if (col.PairColumn != null)
                {
                    columns.Add(Expr(ArcCusCol.KText, col.PairColumn) + " AS " + col.Key + ArcCusCol.PairSuffix);
                }
            }
            return ArcCustomerRow.LockSql(string.Join(", ", columns.ToArray()));
        }

        // 列的读取表达式：空值读成空串，日期 yyyy-MM-dd，0/1 读成 '0' / '1'，小数统一 8 位小数（Current 再去掉多余的 0）。
        internal static string Expr(int kind, string column)
        {
            switch (kind)
            {
                case ArcCusCol.KDate:
                    return "ISNULL(CONVERT(varchar(10), " + column + ", 23),'')";
                case ArcCusCol.KBit:
                    return "ISNULL(CONVERT(varchar(1), " + column + "),'')";
                case ArcCusCol.KNum:
                    return "ISNULL(CONVERT(varchar(48), CONVERT(decimal(38,8), " + column + ")),'')";
                case ArcCusCol.KInt:
                    return "ISNULL(CONVERT(varchar(20), " + column + "),'')";
                default:
                    return "LTRIM(RTRIM(ISNULL(" + column + ",'')))";
            }
        }

        // 读出的行 → 键的当前值（规整成请求值的写法）；有联动列的另存一份（键 + _pair）。
        static Dictionary<string, string> Current(Dictionary<string, object> row, List<string> keys)
        {
            Dictionary<string, string> cur = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int i = 0; i < keys.Count; i++)
            {
                ArcCusCol col = ArcCusCols.Of(keys[i]);
                cur[col.Key] = ArcCusValue.FromDb(col, ArcCustomerRow.Cell(row, col.Key));
                if (col.PairColumn != null)
                {
                    cur[col.Key + ArcCusCol.PairSuffix] = ArcCustomerRow.Cell(row, col.Key + ArcCusCol.PairSuffix);
                }
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
            for (int i = 0; i < ArcCusCols.All.Length; i++)
            {
                ArcCusCol col = ArcCusCols.All[i];
                string want;
                if (ask.Expect.TryGetValue(col.Key, out want) && !string.Equals(want, cur[col.Key], ExpectCompare(col)))
                {
                    bad.Add(col.Key + " 当前是" + (cur[col.Key].Length == 0 ? "空" : cur[col.Key]));
                }
            }
            if (bad.Count > 0)
            {
                throw ArcCustomerRow.Mismatch("与 expect 不符：" + string.Join("；", bad.ToArray()),
                    ArcCusFields.CurrentOf(cur, ask.Fields));
            }
        }

        // 档案编码在库里不分大小写，其余字段逐字比较。
        internal static StringComparison ExpectCompare(ArcCusCol col)
        {
            return col.IgnoreCase ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        }

        static void Write(object conn, WorkContext ctx, ArcCusFieldsAsk ask, Dictionary<string, string> cur, string ts)
        {
            List<string> sets = new List<string>();
            List<object> args = new List<object>();
            for (int i = 0; i < ArcCusCols.All.Length; i++)
            {
                ArcCusCol col = ArcCusCols.All[i];
                string value;
                if (ask.Fields.TryGetValue(col.Key, out value) && !string.Equals(cur[col.Key], value, StringComparison.Ordinal))
                {
                    sets.Add(SetOf(col, value, args));
                    if (col.PairColumn != null)
                    {
                        sets.Add(PairSet(col, ask.Pairs, args));
                    }
                }
            }
            ArcCustomerRow.Update(conn, ctx, string.Join(", ", sets.ToArray()), args.ToArray(), ask.Code, ts);
        }

        // 列名只来自声明表；表外的键是内部错误。空值清空写 NULL，其余值走参数。
        internal static string SetOf(string key, string value, List<object> args)
        {
            ArcCusCol col = ArcCusCols.Of(key);
            if (col == null)
            {
                throw new BridgeException(500, "internal", "客户字段没有对应的列：" + key);
            }
            return SetOf(col, value, args);
        }

        internal static string SetOf(ArcCusCol col, string value, List<object> args)
        {
            if (value.Length == 0)
            {
                return col.Column + "=NULL";
            }
            args.Add(value);
            switch (col.Kind)
            {
                case ArcCusCol.KDate:
                    return col.Column + "=CONVERT(datetime, CONVERT(date, ?, 23))";
                case ArcCusCol.KBit:
                    return col.Column + "=CONVERT(bit, ?)";
                case ArcCusCol.KNum:
                    return col.Column + "=CONVERT(float, ?)";
                case ArcCusCol.KInt:
                    return col.Column + "=CONVERT(int, ?)";
                default:
                    return col.Column + "=?";
            }
        }

        // 联动列（所属行业名称）：随本列一起写，清空时一起清。
        internal static string PairSet(ArcCusCol col, Dictionary<string, string> pairs, List<object> args)
        {
            string value;
            if (!pairs.TryGetValue(col.Key, out value) || value.Length == 0)
            {
                return col.PairColumn + "=NULL";
            }
            args.Add(value);
            return col.PairColumn + "=?";
        }

        // 回读：每个请求的键（和联动列）都等于目标才算成功；否则 pubufts 没变说明 UPDATE 没命中（别人改过，409），变了则结果未知（504）。
        static void Confirm(object conn, ArcCusFieldsAsk ask, string ts, string lockSql, List<string> keys)
        {
            Dictionary<string, object> row = ArcCustomerRow.Lock(conn, lockSql, ask.Code);
            if (row == null)
            {
                throw ArcCustomerRow.Unknown(ask.Code, "字段");
            }
            Dictionary<string, string> now = Current(row, keys);
            foreach (KeyValuePair<string, string> pair in ask.Fields)
            {
                if (!Landed(pair.Key, pair.Value, now, ask.Pairs))
                {
                    throw ArcCustomerRow.Cell(row, "ts") == ts
                        ? ArcCustomerRow.Concurrent(ask.Code) : ArcCustomerRow.Unknown(ask.Code, "字段");
                }
            }
        }

        static bool Landed(string key, string value, Dictionary<string, string> now, Dictionary<string, string> pairs)
        {
            if (!string.Equals(now[key], value, StringComparison.Ordinal))
            {
                return false;
            }
            string pair;
            string stored;
            if (!pairs.TryGetValue(key, out pair))
            {
                return true;
            }
            return !now.TryGetValue(key + ArcCusCol.PairSuffix, out stored) || pair == stored;
        }
    }
}
