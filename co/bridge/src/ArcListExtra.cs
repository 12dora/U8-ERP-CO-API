using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // archives/list 的附加输出（事件源 archive:<名称> 用）：
    // keys_only 每行只留 code、ufts（删除扫描）；有停用日期的六类档案整行另给 end_date（yyyy-mm-dd 或 null）和 disabled。
    // disabled 只看停用日期是否已填（与当天日期无关），同一份数据每次结果相同，事件指纹可重复计算。
    // 列名只来自本表，不收调用方输入；人员的失效日期在 Person（ArcRead.ListSql 的别名 s），其余在主表 h。
    // 选择器标志：仓库 bin_managed（货位管理）、货位 leaf（末级）、存货 batch_managed（批次管理）和
    // shelf_life_managed（保质期管理），整行给布尔（NULL 按 false）；keys_only 不给，其他档案不加。
    internal static class ArcListExtra
    {
        // 档案名 → 停用日期表达式（已带别名）。列名按 U8 表结构（均为 datetime）。
        static readonly string[] EndCols = new string[]
        {
            "customer", "h.dEndDate",
            "vendor", "h.dEndDate",
            "warehouse", "h.dWhEndDate",
            "department", "h.dDepEndDate",
            "person", "s.dPInValidDate",
            "inventory", "h.dEDate"
        };

        // 档案名、输出键、列（已带别名 h）。列名按 U8 表结构（均为 bit）。
        static readonly string[] FlagCols = new string[]
        {
            "warehouse", "bin_managed", "h.bWhPos",
            "position", "leaf", "h.bPosEnd",
            "inventory", "batch_managed", "h.bInvBatch",
            "inventory", "shelf_life_managed", "h.bInvQuality"
        };

        internal static string EndExpr(ArcKind kind)
        {
            if (kind == null)
            {
                return null;
            }
            for (int i = 0; i + 1 < EndCols.Length; i += 2)
            {
                if (EndCols[i] == kind.Name)
                {
                    return EndCols[i + 1];
                }
            }
            return null;
        }

        // keys_only：缺省 false，只收 JSON 布尔。
        internal static bool ParseKeysOnly(object raw)
        {
            if (raw == null)
            {
                return false;
            }
            if (!(raw is bool))
            {
                throw ArcReq.Bad("keys_only 必须是布尔", "keys_only");
            }
            return (bool)raw;
        }

        // 列表 SELECT 追加 end_date 列；keys_only 或没有停用日期的档案不加。
        internal static void AppendSelect(StringBuilder sql, ArcReq req)
        {
            AppendFlags(sql, req);
            string expr = EndExpr(req.Kind);
            if (expr == null || req.KeysOnly)
            {
                return;
            }
            sql.Append(", CONVERT(varchar(10), ").Append(expr).Append(", 23) AS end_date");
        }

        // 列表 SELECT 追加选择器标志列（只用主表 h，只读档案的列表也调用）；keys_only 不加。
        internal static void AppendFlags(StringBuilder sql, ArcReq req)
        {
            if (req.Kind == null || req.KeysOnly)
            {
                return;
            }
            for (int i = 0; i + 2 < FlagCols.Length; i += 3)
            {
                if (FlagCols[i] == req.Kind.Name)
                {
                    sql.Append(", ISNULL(").Append(FlagCols[i + 2]).Append(", 0) AS ").Append(FlagCols[i + 1]);
                }
            }
        }

        // 整行附加选择器标志（布尔）；没有标志的档案不加。
        internal static void PutFlags(Dictionary<string, object> item, ArcKind kind, Dictionary<string, object> row)
        {
            if (kind == null)
            {
                return;
            }
            for (int i = 0; i + 2 < FlagCols.Length; i += 3)
            {
                if (FlagCols[i] == kind.Name)
                {
                    item[FlagCols[i + 1]] = ArcRead.Cell(row, FlagCols[i + 1]) == "1";
                }
            }
        }

        // 整行附加 end_date、disabled；没有停用日期的档案不加，保持原有形状。
        internal static void PutEnd(Dictionary<string, object> item, ArcKind kind, Dictionary<string, object> row)
        {
            if (EndExpr(kind) == null)
            {
                return;
            }
            string end = ArcRead.Cell(row, "end_date");
            item["end_date"] = end;
            item["disabled"] = end != null;
        }

        // keys_only：把各实现返回的 items 收成 {code, ufts}；分页、水位等其他键不动。
        internal static ApiResult Keys(ApiResult result, ArcReq req)
        {
            if (!req.KeysOnly || result == null || result.Body == null)
            {
                return result;
            }
            object raw;
            List<object> items = result.Body.TryGetValue("items", out raw) ? raw as List<object> : null;
            if (items == null)
            {
                return result;
            }
            List<object> keys = new List<object>(items.Count);
            for (int i = 0; i < items.Count; i++)
            {
                keys.Add(KeyItem(items[i] as Dictionary<string, object>));
            }
            result.Body["items"] = keys;
            return result;
        }

        static Dictionary<string, object> KeyItem(Dictionary<string, object> item)
        {
            Dictionary<string, object> key = new Dictionary<string, object>();
            object value = null;
            key["code"] = item != null && item.TryGetValue("code", out value) ? value : null;
            value = null;
            key["ufts"] = item != null && item.TryGetValue("ufts", out value) ? value : null;
            return key;
        }
    }

    // --selftest 的档案列表附加输出部分（ArcListExtra）：只测纯逻辑，不连库。
    internal static class ArcListExtraSelfTest
    {
        public static void Run()
        {
            Expect("end customer", ArcListExtra.EndExpr(ArcKind.Find("customer")) == "h.dEndDate");
            Expect("end person", ArcListExtra.EndExpr(ArcKind.Find("person")) == "s.dPInValidDate");
            Expect("end person join", ArcKind.Find("person").SubTs != null);
            Expect("end inventory", ArcListExtra.EndExpr(ArcKind.Find("inventory")) == "h.dEDate");
            Expect("end none", ArcListExtra.EndExpr(ArcKind.Find("unit")) == null);
            ArcReq req = new ArcReq();
            req.Kind = ArcKind.Find("warehouse");
            StringBuilder sql = new StringBuilder();
            ArcListExtra.AppendSelect(sql, req);
            Expect("end select", sql.ToString().EndsWith(", CONVERT(varchar(10), h.dWhEndDate, 23) AS end_date"));
            req.KeysOnly = true;
            sql.Length = 0;
            ArcListExtra.AppendSelect(sql, req);
            Expect("end select keys", sql.Length == 0);
            CheckItems();
            CheckParse();
            CheckFlags();
        }

        // 选择器标志：SELECT 列、整行布尔、keys_only 与其他档案不加。
        static void CheckFlags()
        {
            ArcReq req = new ArcReq();
            req.Kind = ArcKind.Find("warehouse");
            StringBuilder sql = new StringBuilder();
            ArcListExtra.AppendSelect(sql, req);
            Expect("flag select wh", sql.ToString() == ", ISNULL(h.bWhPos, 0) AS bin_managed, CONVERT(varchar(10), h.dWhEndDate, 23) AS end_date");
            req.Kind = ArcKind.Find("inventory");
            sql.Length = 0;
            ArcListExtra.AppendFlags(sql, req);
            Expect("flag select inv", sql.ToString() == ", ISNULL(h.bInvBatch, 0) AS batch_managed, ISNULL(h.bInvQuality, 0) AS shelf_life_managed");
            req.Kind = ArcKind.Find("unit");
            sql.Length = 0;
            ArcListExtra.AppendFlags(sql, req);
            Expect("flag select other", sql.Length == 0);
            req.Kind = ArcKind.Find("position");
            req.KeysOnly = true;
            ArcListExtra.AppendFlags(sql, req);
            Expect("flag select keys", sql.Length == 0);
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["batch_managed"] = "1";
            row["shelf_life_managed"] = "0";
            Dictionary<string, object> item = new Dictionary<string, object>();
            ArcListExtra.PutFlags(item, ArcKind.Find("inventory"), row);
            Expect("flag item inv", item.Count == 2 && (bool)item["batch_managed"] && !(bool)item["shelf_life_managed"]);
            item = new Dictionary<string, object>();
            ArcListExtra.PutFlags(item, ArcKind.Find("position"), new Dictionary<string, object>());
            Expect("flag item null", item.Count == 1 && !(bool)item["leaf"]);
            item = new Dictionary<string, object>();
            ArcListExtra.PutFlags(item, ArcKind.Find("customer"), row);
            Expect("flag item other", item.Count == 0);
        }

        static void CheckItems()
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["end_date"] = "2024-12-31";
            Dictionary<string, object> item = new Dictionary<string, object>();
            ArcListExtra.PutEnd(item, ArcKind.Find("vendor"), row);
            Expect("end item", (string)item["end_date"] == "2024-12-31" && (bool)item["disabled"]);
            item = new Dictionary<string, object>();
            ArcListExtra.PutEnd(item, ArcKind.Find("vendor"), new Dictionary<string, object>());
            Expect("end item null", item.ContainsKey("end_date") && item["end_date"] == null && !(bool)item["disabled"]);
            item = new Dictionary<string, object>();
            ArcListExtra.PutEnd(item, ArcKind.Find("unit"), row);
            Expect("end item other", item.Count == 0);
            Dictionary<string, object> full = new Dictionary<string, object>();
            full["code"] = "C01";
            full["name"] = "张三";
            full["ufts"] = "123";
            full["disabled"] = true;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["items"] = new List<object> { full };
            body["next"] = "C01";
            ArcReq req = new ArcReq();
            req.KeysOnly = true;
            ApiResult result = ArcListExtra.Keys(ApiResult.Ok(body), req);
            Dictionary<string, object> key = (Dictionary<string, object>)((List<object>)result.Body["items"])[0];
            Expect("keys item", key.Count == 2 && (string)key["code"] == "C01" && (string)key["ufts"] == "123");
            Expect("keys next", (string)result.Body["next"] == "C01");
        }

        static void CheckParse()
        {
            Expect("keys default", !ArcListExtra.ParseKeysOnly(null));
            Expect("keys true", ArcListExtra.ParseKeysOnly(true));
            bool rejected = false;
            try
            {
                ArcListExtra.ParseKeysOnly("true");
            }
            catch (BridgeException ex)
            {
                rejected = ex.Status == 400;
            }
            Expect("keys strict", rejected);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
