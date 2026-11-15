using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 期初余额报表 opening_balance 的参数（只读）。字段名由 Requests.ReportSpecs 把关；fiscal_year、after 由
    // ReportsReq.Parse 统一校验，这里只校验其余取值，登录前抛 400；处理函数里再解析一次。
    internal sealed class OpeningArgs
    {
        // stock 库存期初、arap 应收应付期初、gl 总账期初余额。
        public string Module = "";
        public string Side = "";
        public string Wh = "";
        public string Inv = "";
        public string Batch = "";
        public string Partner = "";
        public string CodePrefix = "";
        public bool LeafOnly;
        // gl：空串按科目（GL_accsum），否则按辅助项（GL_accass）。
        public string Dim = "";
        public bool NonZero = true;
    }

    internal static class ReportsOpeningReq
    {
        internal const string Name = "opening_balance";
        static readonly string[] Modules = new string[] { "stock", "arap", "gl" };
        static readonly string[] Dims = new string[] { "customer", "vendor", "dept", "person", "project" };
        // 各模块可带的字段（公共字段 module、nonzero、after、limit 之外）。
        static readonly Dictionary<string, string[]> Allowed = BuildAllowed();
        static readonly string[] Common = new string[] { "module", "nonzero", "after", "limit" };

        static Dictionary<string, string[]> BuildAllowed()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("stock", new string[] { "wh", "inv", "batch" });
            map.Add("arap", new string[] { "side", "partner", "code_prefix" });
            map.Add("gl", new string[] { "fiscal_year", "code_prefix", "leaf_only", "dim" });
            return map;
        }

        public static bool Owns(string name)
        {
            return name == Name;
        }

        // 登录子系统：库存 ST，应收应付按 side 用 AR / AP，总账 GL。不是本报表返回 null。
        public static string SubOf(string name, Dictionary<string, object> body)
        {
            if (!Owns(name))
            {
                return null;
            }
            string module = Requests.Field(body, "module") as string;
            if (module == "stock")
            {
                return "ST";
            }
            if (module == "arap")
            {
                return (Requests.Field(body, "side") as string) == "ap" ? "AP" : "AR";
            }
            return "GL";
        }

        // 权限规则键：report:opening_balance:stock|ar|ap|gl|gl_aux（PermRegistryMore.OpeningRules）。
        public static string RuleKey(OpeningArgs o)
        {
            if (o.Module == "arap")
            {
                return "report:" + Name + ":" + o.Side;
            }
            if (o.Module == "gl" && o.Dim.Length > 0)
            {
                return "report:" + Name + ":gl_aux";
            }
            return "report:" + Name + ":" + o.Module;
        }

        public static OpeningArgs Parse(ReportArgs a, Dictionary<string, object> body)
        {
            OpeningArgs o = new OpeningArgs();
            o.Module = GlReq.Field(body, "module") as string;
            if (o.Module == null || Array.IndexOf(Modules, o.Module) < 0)
            {
                throw GlReq.Bad("module 只能是 stock、arap 或 gl", "module");
            }
            RejectOthers(o.Module, body);
            a.Limit = OptInt(body, "limit", 200, 1, 1000);
            o.NonZero = OptBool(body, "nonzero", true);
            if (o.Module == "stock")
            {
                o.Wh = OptText(body, "wh", 60);
                o.Inv = OptText(body, "inv", 60);
                o.Batch = OptText(body, "batch", 60);
                return o;
            }
            o.CodePrefix = OptPrefix(body, "code_prefix");
            if (o.Module == "arap")
            {
                o.Side = GlReq.Field(body, "side") as string;
                if (o.Side != "ar" && o.Side != "ap")
                {
                    throw GlReq.Bad("module=arap 时 side 必须是 ar 或 ap", "side");
                }
                o.Partner = OptText(body, "partner", 60);
                return o;
            }
            o.LeafOnly = OptBool(body, "leaf_only", false);
            object dim = GlReq.Field(body, "dim");
            if (dim != null)
            {
                o.Dim = dim as string;
                if (o.Dim == null || Array.IndexOf(Dims, o.Dim) < 0)
                {
                    throw GlReq.Bad("dim 只能是 customer、vendor、dept、person、project", "dim");
                }
            }
            return o;
        }

        // 带了别的模块的字段时 400，避免调用方以为过滤生效了。
        static void RejectOthers(string module, Dictionary<string, object> body)
        {
            string[] own = Allowed[module];
            foreach (string key in body.Keys)
            {
                if (Array.IndexOf(Common, key) >= 0 || Array.IndexOf(own, key) >= 0 || !IsReportField(key))
                {
                    continue;
                }
                throw GlReq.Bad(key + " 不能和 module=" + module + " 一起用", key);
            }
        }

        // 只管本报表自己的字段；acc、year、operator、date 等公共字段不在这里。
        static bool IsReportField(string key)
        {
            foreach (string[] fields in Allowed.Values)
            {
                if (Array.IndexOf(fields, key) >= 0)
                {
                    return true;
                }
            }
            return false;
        }

        static int OptInt(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? fallback : GlReq.IntIn(raw, key, min, max);
        }

        static bool OptBool(Dictionary<string, object> body, string key, bool fallback)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return fallback;
            }
            if (!(raw is bool))
            {
                throw GlReq.Bad(key + " 必须是 true 或 false", key);
            }
            return (bool)raw;
        }

        // 科目编码前缀：只收字母、数字、点和减号，可以直接当 LIKE 前缀用。
        static string OptPrefix(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            if (!ReportsReq.CodeChars(raw as string, 40))
            {
                throw GlReq.Bad(key + " 格式无效", key);
            }
            return (string)raw;
        }

        static string OptText(Dictionary<string, object> body, string key, int max)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0 || text.Length > max || HasControl(text))
            {
                throw GlReq.Bad(key + " 必须是 1 到 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符，不含控制字符", key);
            }
            return text;
        }

        static bool HasControl(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    return true;
                }
            }
            return false;
        }
    }
}
