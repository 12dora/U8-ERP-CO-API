using System;
using System.Collections.Generic;
using System.Text;
using System.Xml;

namespace U8Co
{
    // EAI 信封与 U8SrvTrans.IClsCommon.Transact（实测：登录子系统 AS，byref {1}，返回
    // <ufinterface roottag="return"><item … succeed="0" dsc="ok" u8key=".."/></ufinterface>）。
    internal static class ArcXml
    {
        public static string Envelope(ArcReq req, string proc, ArcBag bag)
        {
            ArcKind k = req.Kind;
            StringBuilder sb = new StringBuilder();
            sb.Append("<ufinterface roottag='").Append(k.Root).Append("' billtype='' docid='")
                .Append(Guid.NewGuid().ToString("N")).Append("' receiver='u8' sender='' proc='").Append(proc)
                .Append("' codeexchanged='N' exportneedexch='N' version='2.0'><").Append(k.Root).Append('>');
            if (k.InvBody)
            {
                sb.Append("<header>");
            }
            KeyTags(sb, req);
            IList<string> tags = Unique(req, bag);
            for (int i = 0; i < tags.Count; i++)
            {
                Tag(sb, tags[i], bag.Get(tags[i]));
            }
            if (k.InvBody)
            {
                sb.Append("</header><body><entry>");
                Tag(sb, "invcode", req.Code);
                sb.Append("</entry></body>");
            }
            sb.Append("</").Append(k.Root).Append("></ufinterface>");
            return sb.ToString();
        }

        // 编码：一般发 <code>；两列主键的档案（ArcKind.CodeTags）按两个标签发编码的两段，如 <id>、<value>。
        static void KeyTags(StringBuilder sb, ArcReq req)
        {
            string[] names = req.Kind.CodeTags;
            if (names == null)
            {
                Tag(sb, "code", req.Code);
                return;
            }
            object[] parts = ArcPair.KeyArgs(req.Kind, req.Code);
            Tag(sb, names[0], (string)parts[0]);
            Tag(sb, names[1], (string)parts[1]);
        }

        // 每列只发一个标签：调用方用了别名标签的列，丢掉模板/补发的标签；其余按先到先得。
        static List<string> Unique(ArcReq req, ArcBag bag)
        {
            HashSet<string> caller = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            IList<string> own = req.Fields.Tags;
            for (int i = 0; i < own.Count; i++)
            {
                caller.Add(req.Map.Column(own[i]));
            }
            HashSet<string> done = KeyColumns(req);
            List<string> result = new List<string>();
            IList<string> tags = bag.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string column = req.Map.Column(tags[i]) ?? tags[i];
                bool shadowed = !req.Fields.Has(tags[i]) && caller.Contains(column);
                if (!shadowed && done.Add(column))
                {
                    result.Add(tags[i]);
                }
            }
            return result;
        }

        // 编码已经发过的列（code，或两列主键的两个标签），bag 里同列的标签不再发。
        static HashSet<string> KeyColumns(ArcReq req)
        {
            HashSet<string> done = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            done.Add(req.Map.Column("code") ?? "");
            string[] keys = req.Kind.CodeTags ?? new string[0];
            for (int i = 0; i < keys.Length; i++)
            {
                done.Add(req.Map.Column(keys[i]) ?? keys[i]);
            }
            return done;
        }

        public static string Transact(WorkContext ctx, string xml)
        {
            if (ctx.Session == null || ctx.Session.Login == null)
            {
                throw new BridgeException(500, "internal", "档案写入缺少 U8 登录");
            }
            object cc = null;
            try
            {
                cc = ComUtil.Create("U8SrvTrans.IClsCommon");
                if (cc == null)
                {
                    throw new BridgeException(503, "com_unavailable", "U8SrvTrans.IClsCommon 未注册");
                }
                // 登录 by-ref 交给自己提交的导入组件，本次登录不放回缓存（LoginCache）。
                ctx.DropLogin();
                object[] args = new object[] { xml, ctx.Session.Login };
                return Values.Text(ComUtil.CallRef(cc, "Transact", args, new int[] { 1 }));
            }
            finally
            {
                ComUtil.Final(cc);
            }
        }

        // 1 = 全部 succeed="0"；0 = U8 拒绝，dsc 为原文；-1 = 返回无法识别。
        public static int Outcome(string raw, out string dsc)
        {
            dsc = "";
            XmlNodeList items = Items(raw);
            if (items == null || items.Count == 0)
            {
                return -1;
            }
            foreach (XmlNode node in items)
            {
                XmlElement el = node as XmlElement;
                string flag = el == null ? "" : el.GetAttribute("succeed").Trim();
                if (flag == "0")
                {
                    continue;
                }
                if (flag.Length == 0)
                {
                    return -1;
                }
                dsc = el.GetAttribute("dsc").Trim();
                if (dsc.Length == 0)
                {
                    dsc = "U8 拒绝了档案操作，没有返回原因";
                }
                return 0;
            }
            return 1;
        }

        public static string Short(string raw)
        {
            string text = raw == null ? "" : raw.Trim();
            if (text.Length == 0)
            {
                return "U8 没有返回结果";
            }
            return text.Length > 500 ? text.Substring(0, 500) : text;
        }

        static XmlNodeList Items(string raw)
        {
            if (raw == null || raw.Trim().Length == 0)
            {
                return null;
            }
            try
            {
                XmlDocument doc = new XmlDocument();
                doc.XmlResolver = null;
                doc.LoadXml(raw);
                return doc.GetElementsByTagName("item");
            }
            catch (XmlException)
            {
                return null;
            }
        }

        static void Tag(StringBuilder sb, string tag, string value)
        {
            sb.Append('<').Append(tag).Append('>');
            for (int i = 0; i < value.Length; i++)
            {
                Escape(sb, value[i]);
            }
            sb.Append("</").Append(tag).Append('>');
        }

        static void Escape(StringBuilder sb, char c)
        {
            switch (c)
            {
                case '&':
                    sb.Append("&amp;");
                    break;
                case '<':
                    sb.Append("&lt;");
                    break;
                case '>':
                    sb.Append("&gt;");
                    break;
                case '"':
                    sb.Append("&quot;");
                    break;
                case '\'':
                    sb.Append("&apos;");
                    break;
                default:
                    // 库里偶有的其他控制字符不能进 XML，丢掉。
                    if (c >= ' ' || c == '\t' || c == '\n' || c == '\r')
                    {
                        sb.Append(c);
                    }
                    break;
            }
        }
    }
}
