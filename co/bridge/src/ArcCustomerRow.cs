using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户档案受控 SQL 路由（customer_person、customer_fields、customer_address）共用的行级零件：权限、事务外壳、
    // UPDLOCK 读行 + pubufts 乐观锁更新、修改人 / 修改日期、回读失败的两种错误。
    // 这些路由都不走客户 EAI（客户 diffedit 会清银行账号、改最近收款日、补助记码，并拒无所属行业的客户）。
    internal static class ArcCustomerRow
    {
        const string TsColumn = "CONVERT(varchar(20), CONVERT(bigint, pubufts)) AS ts";
        const string StampSql = "cModifyPerson=?, dModifyDate=CONVERT(datetime, CONVERT(date, ?, 23))";
        const string KeySql = " WHERE cCusCode=? AND pubufts=CONVERT(binary(8), CONVERT(bigint, ?))";

        // 功能权限（规则键对应的 AS011 一族）+ 客户记录级权限。
        public static void Permit(WorkContext ctx, string ruleKey, string code)
        {
            PermContext p = PermCheck.Of(ctx);
            PermCheck.RequireRule(p, PermRegistry.ForKey(ruleKey));
            if (!PermCheck.Allow(p, PermObj.Customer, code))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        // 一个事务：开事务、跑 apply、提交；预演（DryRunDone）原样上抛，其它异常交给 CoRows.CatchTran 回滚。
        public static ApiResult Transact(WorkContext ctx, string note, Func<object, ApiResult> apply)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                ApiResult result = apply(conn);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.Commit(conn);
                open = false;
                CoRows.Note(ctx.Item, note);
                return result;
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 加行锁读客户：columns 是要读的列表达式（别名自定），另带 ts（pubufts 的十进制串）。
        public static string LockSql(string columns)
        {
            return "SELECT " + columns + ", " + TsColumn
                + " FROM Customer WITH (UPDLOCK, ROWLOCK) WHERE cCusCode=?";
        }

        // 读不到返回 null（回读用）。
        public static Dictionary<string, object> Lock(object conn, string lockSql, string code)
        {
            return Rows.One(conn, lockSql, new object[] { code });
        }

        // 读不到抛 404。
        public static Dictionary<string, object> Need(object conn, string lockSql, string code)
        {
            Dictionary<string, object> row = Lock(conn, lockSql, code);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            return row;
        }

        // 只改 setList 里的列（"列=?" 或 "列=NULL"，用 setArgs 依次绑定 ?），同时写修改人、修改日期；
        // 仅当 pubufts 仍是读到的 ts 时才改。
        public static void Update(object conn, WorkContext ctx, string setList, object[] setArgs, string code, string ts)
        {
            object[] args = new object[setArgs.Length + 4];
            Array.Copy(setArgs, args, setArgs.Length);
            args[setArgs.Length] = Modifier(ctx);
            args[setArgs.Length + 1] = ctx.Item.Date;
            args[setArgs.Length + 2] = code;
            args[setArgs.Length + 3] = ts;
            GlSql.Exec(conn, UpdateSql(setList), args);
        }

        public static string UpdateSql(string setList)
        {
            return "UPDATE Customer SET " + setList + ", " + StampSql + KeySql;
        }

        // 修改人写操作员姓名；没有姓名时退回操作员编码。
        public static string Modifier(WorkContext ctx)
        {
            string name = ctx.OperatorName;
            if (name != null && name.Trim().Length > 0)
            {
                return name.Trim();
            }
            string code = ctx.Item == null ? null : ctx.Item.Operator;
            return code ?? "";
        }

        public static string Cell(Dictionary<string, object> row, string name)
        {
            return (GlSql.Col(row, name) ?? "").Trim();
        }

        // 不区分大小写的比较（U8 编码、人员编码查找不分大小写）。
        public static bool Same(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        // expect 与当前值不符：409 state_mismatch，消息照旧，另在错误体 detail.current 里给结构化的当前值
        // （customer_fields 是请求 fields 的各键，customer_person 是 person；空值是空串），调用方不必解析中文消息。
        public static BridgeException Mismatch(string message, Dictionary<string, object> current)
        {
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["current"] = current;
            return new BridgeException(409, "state_mismatch", message).WithDetail(detail);
        }

        // 回读与写入不一致、pubufts 已变：别人在中间改了，409。
        public static BridgeException Concurrent(string code)
        {
            return new BridgeException(409, "state_mismatch", "客户 " + code + " 已被他人修改");
        }

        // 回读与写入不一致、pubufts 没变：结果未知，504。
        public static BridgeException Unknown(string code, string what)
        {
            return new BridgeException(504, "outcome_unknown",
                "客户 " + code + " 已写入" + what + "，但回读与写入不一致，结果未知");
        }

        public static string Text(Dictionary<string, object> body, string key)
        {
            object value = Requests.Field(body, key);
            if (value == null)
            {
                return null;
            }
            string text = value as string;
            if (text == null)
            {
                throw ArcReq.Bad(key + " 必须是字符串", key);
            }
            return text;
        }
    }
}
