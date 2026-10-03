using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 总账基础档案写入：币种（currency）、凭证类别（voucher_sign）。
    // EAI 分发表（EAI\XML\Operation\Distribute.xml）把这两个根标签指向 U8PZInsert.dll 里的 ICurrency / IDsign，
    // 不是 U8SrvTrans.IClsCommon。两者的 Transact 只提供新增，diffedit / delete 回「暂不提供此项功能」，
    // 所以新增走 EAI（ArcGl），修改、删除走受控 SQL（ArcCurrencySql、ArcSignSql）。get、list 照旧按表列名返回（RoRead）；
    // 字段是 RsXml 的 EAI 标签。会计科目仍只读：ICode.Transact 实测报「未设置对象变量或 With block 变量」，
    // 且科目按年度存、EAI 报文没有年度，写入年度无法确定。
    // 编码标签（ArcKind.CodeTag）：币种的编码是名称，按 <name> 发，fields 里的 code 是币种符号；凭证类别的编码是类别字，按 <type> 发。
    internal static class ArcGlKinds
    {
        internal const string Currency = "currency";
        internal const string Sign = "voucher_sign";
        // 经 EAI 新增时的登录子系统（GL 登录下 ICurrency、IDsign 可以新增）。修改、删除只跑 SQL，照其他档案按 AS 登录，
        // 不占总账的许可点数。
        internal const string WriteSub = "GL";
        // 币种符号 cexch_code nvarchar(4)，凭证类别字按 RsXml 注释最长 2，凭证类别名称 ctext nvarchar(30)。
        internal const int SymbolMax = 4;
        internal const int SignMax = 2;
        internal const int SignNameMax = 30;

        internal static bool Owns(ArcKind k)
        {
            return k != null && (k.Name == Currency || k.Name == Sign);
        }

        // RequestsP4 登录前取子系统：这两类档案的新增用 WriteSub，其余返回 null（照旧 AS）。
        // 汇率新增（ArcExchWrite）经分发器到同一 U8PZInsert.dll 的 icurrencyrate，同样按 WriteSub 登录。
        internal static string SubOf(string op, Dictionary<string, object> body)
        {
            if (op != "create" || body == null)
            {
                return null;
            }
            object archive;
            if (!body.TryGetValue("archive", out archive))
            {
                return null;
            }
            string name = archive as string;
            return name == Currency || name == Sign || name == ArcExch.Name ? WriteSub : null;
        }

        // 币种 CurrencyXmlRs.xml：编码是币种名称（单据里引用币种都用名称），按 <name> 发；币种符号 code（cexch_code）新增必填、
        // 不能改（EAI 按它关联主表）。自动编号 id、其他系统使用标志 otherused 由 U8 维护。折算方式、小数位数、最大误差在表里非空，
        // 新增缺省 1、5、0.00001（与 U8 样例的美元相同）。
        internal static ArcKind CurrencyKind(ArcKind k)
        {
            ArcKindRw.Eai(k, "currency", "CurrencyXmlRs.xml");
            k.CodeTag = "name";
            k.NameTag = null;
            k.Block = new string[] { "id", "otherused" };
            k.Defaults = new string[] { "caltype", "1", "precision", "5", "error", "0.00001" };
            return k;
        }

        // 凭证类别 DsignXmlRs.xml：编码是类别字 csign，按 <type> 发；名称 type_name（ctext，表上唯一）新增必填；
        // 排序号 order_code 新增可给（缺省最大加一），修改不收（U8 注释：改排序号要同时改凭证表和收付款结算表）。
        internal static ArcKind SignKind(ArcKind k)
        {
            ArcKindRw.Eai(k, "dsign", "DsignXmlRs.xml");
            k.CodeTag = "type";
            k.NameTag = "type_name";
            k.Block = new string[] { "i_id", "other_use_flag" };
            return k;
        }

        // 登录前（ArcReq.Parse）的格式校验；库里的状态在 ArcCurrency / ArcSign 里查。
        internal static void Check(ArcReq req)
        {
            if (req.Op == "delete" || req.Op == "get")
            {
                return;
            }
            if (req.Template != null)
            {
                throw ArcReq.Bad("档案 " + req.Kind.Name + " 不支持 template", "template");
            }
            switch (req.Kind.Name)
            {
                case Currency:
                    CheckCurrency(req);
                    break;
                default:
                    CheckSign(req);
                    break;
            }
        }

        static void CheckCurrency(ArcReq req)
        {
            string symbol = req.Fields.Get(req.Map.Canon("code"));
            if (req.Op == "update" && req.Fields.Has(req.Map.Canon("code")))
            {
                throw ArcReq.Bad("不能修改币种符号 code，请删除后重新新增", "fields.code");
            }
            if (req.Op == "create" && (symbol == null || symbol.Trim().Length == 0 || symbol.Length > SymbolMax))
            {
                throw ArcReq.Bad("新增币种必须在 fields 给 code（币种符号，1 到 4 个字符）", "fields.code");
            }
            Choice(req, "caltype", "0", "1", "折算方式 caltype 只能是 0 或 1");
            Integer(req, "precision", 0, 10);
            string error = req.Fields.Get(req.Map.Canon("error"));
            decimal value;
            if (error != null && (!decimal.TryParse(error, NumberStyles.Float, CultureInfo.InvariantCulture, out value) || value < 0m))
            {
                throw ArcReq.Bad("最大误差 error 必须是不小于 0 的数", "fields.error");
            }
        }

        static void CheckSign(ArcReq req)
        {
            if (req.Op == "update")
            {
                IList<string> tags = req.Fields.Tags;
                for (int i = 0; i < tags.Count; i++)
                {
                    if (!string.Equals(tags[i], "type_name", StringComparison.OrdinalIgnoreCase))
                    {
                        throw ArcReq.Bad("凭证类别只能修改名称 type_name（不能设置字段 " + tags[i] + "）", FieldPath.Join("fields", tags[i]));
                    }
                }
            }
            else if (req.Code.Length > SignMax)
            {
                throw ArcReq.Bad("凭证类别字 code 最长 2 个字符", "code");
            }
            Text(req, "type_name", SignNameMax);
            Integer(req, "order_code", 1, 255);
        }

        // 给了就必须非空白、不超长。
        static void Text(ArcReq req, string tag, int max)
        {
            string value = req.Fields.Get(req.Map.Canon(tag));
            if (value != null && (value.Trim().Length == 0 || value.Length > max))
            {
                throw ArcReq.Bad(tag + " 长度必须在 1 到 " + max.ToString(CultureInfo.InvariantCulture) + " 之间且不能全是空白", FieldPath.Join("fields", tag));
            }
        }

        static void Integer(ArcReq req, string tag, int min, int max)
        {
            string value = req.Fields.Get(req.Map.Canon(tag));
            int n;
            if (value != null && (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out n) || n < min || n > max))
            {
                throw ArcReq.Bad(tag + " 必须是 " + min.ToString(CultureInfo.InvariantCulture) + " 到 "
                    + max.ToString(CultureInfo.InvariantCulture) + " 的整数", FieldPath.Join("fields", tag));
            }
        }

        static void Choice(ArcReq req, string tag, string a, string b, string message)
        {
            string value = req.Fields.Get(req.Map.Canon(tag));
            if (value != null && value != a && value != b)
            {
                throw ArcReq.Bad(message, FieldPath.Join("fields", tag));
            }
        }
    }
}
