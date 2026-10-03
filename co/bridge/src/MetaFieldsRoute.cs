using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // POST /u8co/v1/meta/fields：按账套给出单据 / 档案 / 总账凭证可写字段的中文标题、类型、必输、枚举。
    // 读路由：读线程池 + 登录缓存（RouteClass），登录子系统 AS，除有效登录外不要求功能权限（不在 PermRegistry 的读路由表里，
    // PermGate 不拦），不持写闸门。标题一律现查本账套（U8 模板和列表定义各账套可改），只有总账凭证的标题是本项目固定的。
    internal static class MetaFieldsRoute
    {
        // 总账凭证（GlReq.MetaNames）的固定标题和类型；必填同 meta gl 的 required_head / required_line。
        static readonly string[][] GlLabels = new string[][]
        {
            new string[] { "sign", "凭证类别", "string" },
            new string[] { "date", "日期", "date" },
            new string[] { "attachments", "附件数", "int" },
            new string[] { "account", "科目", "string" },
            new string[] { "digest", "摘要", "string" },
            new string[] { "debit", "借方", "decimal" },
            new string[] { "credit", "贷方", "decimal" },
            new string[] { "dept", "部门", "string" },
            new string[] { "person", "人员", "string" },
            new string[] { "customer", "客户", "string" },
            new string[] { "supplier", "供应商", "string" },
            new string[] { "item_class", "项目大类", "string" },
            new string[] { "item", "项目", "string" },
            new string[] { "settle", "结算方式", "string" },
            new string[] { "doc_no", "票据号", "string" },
            new string[] { "doc_date", "票据日期", "date" },
            new string[] { "currency", "币种", "string" },
            new string[] { "rate", "汇率", "decimal" },
            new string[] { "qty", "数量", "decimal" },
            new string[] { "cash_flow", "现金流量", "array" }
        };
        // 现金流量项目里的 item / debit / credit 与分录同名、意思不同，单列。
        static readonly string[][] FlowLabels = new string[][]
        {
            new string[] { "item", "流量项目", "string" },
            new string[] { "debit", "借", "decimal" },
            new string[] { "credit", "贷", "decimal" }
        };
        static readonly string[] GlHeadRequired = new string[] { "sign" };
        static readonly string[] GlLineRequired = new string[] { "account", "digest" };

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "查询请求缺少请求体");
            }
            MetaFieldsAsk ask = MetaFieldsReq.Parse(ctx.Item.Body);
            if (ask.Kind != null)
            {
                CoRows.Note(ctx.Item, "字段说明 " + ask.Kind.Name + " " + ask.Op);
                return ApiResult.Ok(MetaFieldsDoc.Build(ctx, ask));
            }
            if (ask.Arc != null)
            {
                CoRows.Note(ctx.Item, "字段说明 档案 " + ask.Arc.Name);
                return ApiResult.Ok(MetaFieldsArc.Build(ctx, ask.Arc));
            }
            CoRows.Note(ctx.Item, "字段说明 总账凭证");
            return ApiResult.Ok(Gl());
        }

        internal static Dictionary<string, object> Gl()
        {
            string[][] names = GlReq.MetaNames();
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["gl"] = true;
            result["head"] = GlSide(names[0], GlLabels, GlHeadRequired);
            result["lines"] = GlSide(names[1], GlLabels, GlLineRequired);
            result["cash_flow"] = GlSide(names[2], FlowLabels, new string[0]);
            result["fields_revision"] = Revision(result["head"], result["lines"], result["cash_flow"]);
            return result;
        }

        static List<object> GlSide(string[] names, string[][] labels, string[] required)
        {
            List<object> list = new List<object>();
            for (int i = 0; i < names.Length; i++)
            {
                string[] row = Row(labels, names[i]);
                Dictionary<string, object> d = new Dictionary<string, object>();
                d["name"] = names[i];
                d["label"] = row == null ? null : row[1];
                d["type"] = row == null ? null : row[2];
                d["required"] = Array.IndexOf(required, names[i]) >= 0;
                list.Add(d);
            }
            return list;
        }

        static string[] Row(string[][] labels, string name)
        {
            for (int i = 0; i < labels.Length; i++)
            {
                if (labels[i][0] == name)
                {
                    return labels[i];
                }
            }
            return null;
        }

        // 返回的字段列表（按返回顺序）的 JSON 的 SHA-256。字典按插入顺序序列化，构造顺序固定，结果稳定。
        internal static string Revision(object first, object second, object third)
        {
            List<object> all = new List<object>();
            all.Add(first);
            all.Add(second);
            all.Add(third);
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = Json.ResponseLimit;
            byte[] bytes = Encoding.UTF8.GetBytes(ser.Serialize(all));
            return Crypto.Hex(Crypto.Sha256(bytes));
        }
    }
}
