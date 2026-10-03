using System;
using System.Collections.Generic;

namespace U8Co
{
    // 不良品处理单的模板必输项（voucheritems.IsNull=1，VT 355 / 356）：DOM 填好之后、取号之前核对。缺的字段一次列全，
    // 每项写成调用方该送的请求字段名加 U8 界面上的标题（扩展自定义项取 UserDef 里设的名称，如 chdefine15「处理类型」；
    // 其余取 voucheritems_lang 的中文标题），桥自己填、调用方送不了的列写模板字段名。单号在核对之后才取，跳过 CREJECTCODE。
    internal sealed class QmTplAsk
    {
        public string Skip = "";
        public string[][] Names;
    }

    internal static class QmRejTpl
    {
        const string RequiredSql = "select v.FieldName, v.CardSection, coalesce(ud.cItemName, vl.carditemname, v.carditemname, '') as Caption"
            + " from voucheritems v outer apply (select top 1 l.carditemname from voucheritems_lang l where l.vt_id=v.VT_ID"
            + " and l.fieldname=v.FieldName and l.cardsection=v.CardSection and l.localeid='zh-CN') vl"
            + " outer apply (select top 1 g.cItemName from UserDef_Base b join UserDef_Lang g on g.cId=b.cID and g.LocaleID='zh-CN'"
            + " where b.cDicDbName=v.FieldName and b.cClass=case v.CardSection when 'B' then N'单据体' else N'单据头' end) ud"
            + " where v.VT_ID=? and v.IsNull=1";
        const string Skip = "CREJECTCODE";

        // 模板字段名（不分大小写）→ 请求字段名。
        static readonly string[][] Requested = new string[][]
        {
            new string[] { "DDATE", "dDate" },
            new string[] { "FQUANTITY", "quantity" },
            new string[] { "CSCRAPDISCODE", "cScrapDisCode" },
            new string[] { "CREASONCODE", "cReasonCode" },
            new string[] { "CREASONNAME", "cReasonCode" },
            new string[] { "CDIMINVCODE", "cDimInvCode" },
            new string[] { "CBWHCODE", "cbWhCode" }
        };

        public static void Require(object conn, int vt, object head, object body, int maxLines)
        {
            QmTplAsk ask = new QmTplAsk();
            ask.Skip = Skip;
            Require(conn, vt, head, body, maxLines, ask);
        }

        // 其他报检单、其他检验单（QmOthReq.TplAsk）：ask.Skip 是桥在核对之后才填的单号列（CINSPECTCODE / CCHECKCODE），
        // ask.Names 是调用方自己的「模板字段名 → 请求字段名」，先于共用表查（不良品处理单不带，提示不变）。
        public static void Require(object conn, int vt, object head, object body, int maxLines, QmTplAsk ask)
        {
            string skip = ask.Skip;
            string[][] extra = ask.Names;
            List<Dictionary<string, object>> fields = QmSql.Many(conn, RequiredSql, 500, vt);
            if (fields == null || fields.Count == 0)
            {
                return;
            }
            List<string> missing = QmTplReq.Missing(fields, Rows.FromDom(head, 1), Rows.FromDom(body, maxLines), skip);
            if (missing.Count > 0)
            {
                throw Refuse(fields, missing, extra);
            }
        }

        internal static BridgeException Refuse(List<Dictionary<string, object>> fields, List<string> missing)
        {
            return Refuse(fields, missing, null);
        }

        internal static BridgeException Refuse(List<Dictionary<string, object>> fields, List<string> missing, string[][] extra)
        {
            List<string> labels = new List<string>();
            string field = null;
            for (int i = 0; i < missing.Count; i++)
            {
                Dictionary<string, object> row = Find(fields, missing[i]);
                bool isBody = QmRejSpec.Same(CoRows.Col(row, "CardSection"), "B");
                string asked = RequestName(missing[i], extra);
                labels.Add(Label(asked ?? missing[i], CoRows.Col(row, "Caption")));
                if (field == null && asked != null)
                {
                    field = (isBody ? "lines." : "head.") + asked;
                }
            }
            BridgeException ex = new BridgeException(400, "bad_request",
                "缺少必输字段 " + string.Join("、", labels.ToArray()) + "（U8 单据模板设置为必输）");
            return field == null ? ex : ex.WithField(field);
        }

        // 调用方能送的请求字段名；桥自己填的列返回 null。
        internal static string RequestName(string fieldName)
        {
            return RequestName(fieldName, null);
        }

        internal static string RequestName(string fieldName, string[][] extra)
        {
            string own = Lookup(extra, fieldName);
            if (own != null)
            {
                return own;
            }
            string low = (fieldName ?? "").ToLowerInvariant();
            if (QmReq.Span(low, "chdefine", 11, 16))
            {
                return "chDefine" + low.Substring(8);
            }
            if (QmReq.Span(low, "cdefine", 1, 16))
            {
                return "cDefine" + low.Substring(7);
            }
            return Lookup(Requested, fieldName);
        }

        static string Lookup(string[][] table, string fieldName)
        {
            if (table == null)
            {
                return null;
            }
            for (int i = 0; i < table.Length; i++)
            {
                if (QmRejSpec.Same(table[i][0], fieldName))
                {
                    return table[i][1];
                }
            }
            return null;
        }

        internal static string Label(string name, string caption)
        {
            return caption == null || caption.Length == 0 ? name : name + "（" + caption + "）";
        }

        static Dictionary<string, object> Find(List<Dictionary<string, object>> fields, string name)
        {
            for (int i = 0; i < fields.Count; i++)
            {
                if (QmRejSpec.Same(CoRows.Col(fields[i], "FieldName"), name))
                {
                    return fields[i];
                }
            }
            return null;
        }
    }
}
