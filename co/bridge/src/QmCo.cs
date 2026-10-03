using System;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;

namespace U8Co
{
    // 一次 VoucherOperate 的结果：Error 是 U8 的拒绝原文（error 串或 ErrBag），Lost 是其他 COM 异常（结果要回读确认）。
    internal sealed class QmOutcome
    {
        public string Error = "";
        public Exception Lost;

        public bool Ok
        {
            get { return Error.Length == 0 && Lost == null; }
        }
    }

    // 质量单据（QM01–QM04）的 UFQMCo 组件，登录子系统 QM。调用约定：
    // Init(login, conn, bOutTrans=false) 引用 {0,1,2}，返回 VT_EMPTY，不当失败；
    // VoucherOperate(DomHead, domBody, actionType, error, voucherid) 引用 {0,1,3}，actionType 用 add / delete / confirm / unconfirm。
    // 返回值不可信（error 里有 U8 错误时也回 true）：成功 = 没有 COM 异常且 error 为空。U8 的错误两种来路：
    // error 串「错误列表：\r\n 1、  [描述]:…」，或 COM 异常的 <ErrBagList><ErrBag description="…"/>（取第一个 ErrBag）。
    // 新增的 DOM 用视图 where 1=2 的空白，行上 editprop="A"；删除、弃审要传从同一视图按 ID 读出的 DOM 和 voucherid。
    internal sealed class QmCo : IDisposable
    {
        public const string LoginSub = "QM";
        const string ListHead = "[描述]:";
        static readonly Regex BagDesc = new Regex("<ErrBag\\b[^>]*\\bdescription=\"([^\"]*)\"",
            RegexOptions.CultureInvariant);

        object _co;

        public static QmCo Open(WorkContext ctx, QmSpec spec)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "没有 U8 登录");
            }
            object co = ComUtil.Create(spec.ProgId);
            if (co == null)
            {
                throw new BridgeException(503, "com_unavailable", "组件无法创建 " + spec.ProgId);
            }
            QmCo made = new QmCo();
            made._co = co;
            try
            {
                ctx.DropLogin();
                object[] args = new object[] { ctx.Session.Login, ctx.Conn, false };
                ComUtil.CallRef(co, "Init", args, new int[] { 0, 1, 2 });
                return made;
            }
            catch
            {
                made.Dispose();
                throw;
            }
        }

        // doms[0] 表头、doms[1] 表体；U8 换了 DOM 就放掉旧的。桥自己的异常照常抛。
        public QmOutcome Operate(object[] doms, string action, string voucherId)
        {
            QmOutcome outcome = new QmOutcome();
            object[] args = new object[] { doms[0], doms[1], action, "", voucherId ?? "" };
            try
            {
                ComUtil.CallRef(_co, "VoucherOperate", args, new int[] { 0, 1, 3 });
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
                outcome.Error = ListText(Values.Text(args[3]));
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                string bag = BagText(ex.Message);
                if (bag.Length > 0)
                {
                    outcome.Error = bag;
                }
                else
                {
                    outcome.Lost = ex;
                }
            }
            return outcome;
        }

        public void Dispose()
        {
            object co = _co;
            _co = null;
            ComUtil.Final(co);
        }

        // COM 异常消息里的 ErrBagList：第一个 ErrBag 的 description（去掉 U8 的尾部空格填充）；不是 ErrBag 返回空串。
        internal static string BagText(string message)
        {
            string text = message ?? "";
            int at = text.IndexOf("<ErrBagList", StringComparison.Ordinal);
            if (at < 0)
            {
                return "";
            }
            string xml = text.Substring(at);
            try
            {
                XmlDocument doc = new XmlDocument();
                doc.LoadXml(xml.Trim());
                XmlElement bag = doc.DocumentElement == null ? null : doc.DocumentElement["ErrBag"];
                if (bag != null)
                {
                    return Squeeze(bag.GetAttribute("description"));
                }
            }
            catch (XmlException)
            {
            }
            Match m = BagDesc.Match(xml);
            return m.Success ? Squeeze(System.Net.WebUtility.HtmlDecode(m.Groups[1].Value)) : "";
        }

        // error 串：「错误列表：\r\n 1、  [描述]:A\r\n 2、  [描述]:B」→「A；B」；没有「[描述]:」时原样去空白。
        internal static string ListText(string err)
        {
            string text = (err ?? "").Trim();
            if (text.Length == 0 || text.IndexOf(ListHead, StringComparison.Ordinal) < 0)
            {
                return text;
            }
            List<string> parts = new List<string>();
            int at = text.IndexOf(ListHead, StringComparison.Ordinal);
            while (at >= 0)
            {
                int start = at + ListHead.Length;
                int next = text.IndexOf(ListHead, start, StringComparison.Ordinal);
                int end = LineEnd(text, start, next);
                string part = Squeeze(text.Substring(start, end - start));
                if (part.Length > 0)
                {
                    parts.Add(part);
                }
                at = next;
            }
            return parts.Count == 0 ? text : string.Join("；", parts.ToArray());
        }

        static int LineEnd(string text, int start, int next)
        {
            int end = next < 0 ? text.Length : next;
            int nl = text.IndexOfAny(new char[] { '\r', '\n' }, start);
            return nl >= 0 && nl < end ? nl : end;
        }

        // U8 在引号里的字段名后面补空格（「'检验部门        '不能为空！」）：只去掉引号内、紧挨闭合引号的空白，其余原样（两端去空白）。
        static string Squeeze(string text)
        {
            string src = (text ?? "").Trim();
            StringBuilder sb = new StringBuilder();
            bool quoted = false;
            int pending = 0;
            for (int i = 0; i < src.Length; i++)
            {
                char c = src[i];
                if (char.IsWhiteSpace(c))
                {
                    pending++;
                    continue;
                }
                bool closing = c == '\'' && quoted;
                if (!closing)
                {
                    sb.Append(src, i - pending, pending);
                }
                pending = 0;
                if (c == '\'')
                {
                    quoted = !quoted;
                }
                sb.Append(c);
            }
            return sb.ToString();
        }
    }
}
