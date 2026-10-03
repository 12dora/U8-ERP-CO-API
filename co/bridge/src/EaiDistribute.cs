using System;
using System.Text;
using System.Xml;

namespace U8Co
{
    // U8 官方 EAI 分发器：U8Distribute.iDistribute.ProcessEx(xml, login)，login 按引用 {1}。
    // 分发器按 Distribute.xml 找到根标签对应的导入组件，导入自行提交，不在请求连接的事务里：调用前查完，预演停在调用之前，
    // 调用后在新连接上回读。期初结存（StockOpeningAdd）、汇率新增（ArcExchWrite）、供应商联系人新增（ArcVenContact）共用。
    internal static class EaiDistribute
    {
        internal const string ProgId = StockOpeningEai.ProgId;
        internal const string Method = StockOpeningEai.Method;
        internal const string What = ProgId + "." + Method;

        // 组件没注册 503（调用前，什么也没做）。调用方用完 ComUtil.Final。
        internal static object Open()
        {
            object eai = ComUtil.Create(ProgId);
            if (eai == null)
            {
                throw new BridgeException(503, "com_unavailable", ProgId + " 未注册");
            }
            return eai;
        }

        // 登录 by-ref 交给自行提交的导入组件，本次登录不放回缓存（LoginCache）。
        internal static string Process(WorkContext ctx, object eai, string xml)
        {
            if (ctx.Session == null || ctx.Session.Login == null)
            {
                throw new BridgeException(500, "internal", "EAI 导入缺少 U8 登录");
            }
            ctx.DropLogin();
            object[] args = new object[] { xml, ctx.Session.Login };
            return Values.Text(ComUtil.CallRef(eai, Method, args, new int[] { 1 }));
        }

        // 档案用：建组件、调用、释放。COM 异常原样抛出，由调用方按「结果未知」回读。
        internal static string Call(WorkContext ctx, string xml)
        {
            object eai = Open();
            try
            {
                return Process(ctx, eai, xml);
            }
            finally
            {
                ComUtil.Final(eai);
            }
        }

        // 报文头：<ufinterface roottag='…' … proc='…' …><根标签>。调用方接着写字段和结束标签。
        internal static StringBuilder Begin(string root, string proc)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("<ufinterface roottag='").Append(root).Append("' billtype='' docid='").Append(Guid.NewGuid().ToString("N"))
                .Append("' receiver='u8' sender='' proc='").Append(proc)
                .Append("' codeexchanged='N' exportneedexch='N' version='2.0'><").Append(root).Append('>');
            return sb;
        }

        internal static string End(StringBuilder sb, string root)
        {
            return sb.Append("</").Append(root).Append("></ufinterface>").ToString();
        }

        // 第一个 <item> 的 u8key（U8 编的号，如供应商联系人 S000100000001）；不是 XML、没有 item 或没给返回空串。
        internal static string Key(string raw)
        {
            if (raw == null || raw.Trim().Length == 0)
            {
                return "";
            }
            XmlDocument doc = new XmlDocument();
            doc.XmlResolver = null;
            try
            {
                doc.LoadXml(raw);
            }
            catch (XmlException)
            {
                return "";
            }
            XmlNodeList items = doc.GetElementsByTagName("item");
            XmlElement el = items.Count > 0 ? items[0] as XmlElement : null;
            return el == null ? "" : el.GetAttribute("u8key").Trim();
        }
    }
}
