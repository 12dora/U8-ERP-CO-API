using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

namespace U8Co
{
    // 回读一行：在新连接上按编码读；不存在返回 null。
    internal delegate Dictionary<string, string> ArcGlRead(object conn);

    // 一次 EAI 调用：组件、proc、要发的标签。AllowMissing 为 true 时组件没有 Transact 成员返回 null（凭证类别改走 SQL）。
    internal sealed class ArcGlCall
    {
        public string ProgId;
        public string Proc;
        public ArcBag Bag = new ArcBag();
        public ArcGlRead Read;
        public bool AllowMissing;
    }

    // 总账基础档案（ArcGlKinds）的写入：分给 ArcCurrency / ArcSign；这里放两者共用的 EAI 新增报文、调用和回读。
    // ICurrency、IDsign 的 Transact 新增成功，diffedit / delete 回「暂不提供此项功能」，修改、删除改走受控 SQL。
    // U8PZInsert 的 EAI 组件与 U8SrvTrans.IClsCommon 一样自己提交（不包 CoTrans），调用前查完，调用后在新连接上回读。
    // 实测：按 clsPZInsert 的写法先设 ToEAICon 再 Transact，ICurrency 报 DISP_E_UNKNOWNNAME（ToEAICon 只在 clsPZInsert 上）。
    // 这里不设 ToEAICon，只调 Transact(xml, login)，by-ref {1}，与 U8Distribute 晚绑定调用的方式相同。
    internal static class ArcGl
    {
        const int UnknownName = unchecked((int)0x80020006);

        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            switch (req.Kind.Name)
            {
                case ArcGlKinds.Currency:
                    return ArcCurrency.Write(ctx, req);
                default:
                    return ArcSign.Write(ctx, req);
            }
        }

        // 编码按 ArcKind.CodeTag 发（币种 <name>、凭证类别 <type>），之后是 bag 里的标签，同一标签只发一次。
        internal static string Envelope(ArcReq req, string proc, ArcBag bag)
        {
            ArcKind k = req.Kind;
            StringBuilder sb = new StringBuilder();
            sb.Append("<ufinterface roottag='").Append(k.Root).Append("' billtype='' docid='")
                .Append(Guid.NewGuid().ToString("N")).Append("' receiver='u8' sender='' proc='").Append(proc)
                .Append("' codeexchanged='N' exportneedexch='N' version='2.0'><").Append(k.Root).Append('>');
            Tag(sb, k.CodeTag, req.Code);
            IList<string> tags = bag.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                if (!string.Equals(tags[i], k.CodeTag, StringComparison.OrdinalIgnoreCase))
                {
                    Tag(sb, tags[i], bag.Get(tags[i]));
                }
            }
            sb.Append("</").Append(k.Root).Append("></ufinterface>");
            return sb.ToString();
        }

        // 调用 → 解析 → 回读。U8 拒绝 409 带原文；回读与预期不符、或报文无法识别时按 proc 判断，结果不明 504 outcome_unknown。
        internal static ApiResult Run(WorkContext ctx, ArcReq req, ArcGlCall call)
        {
            string xml = Envelope(req, call.Proc, call.Bag);
            ArcDryRun.Stop(ctx, req.Kind.Name, req.Code, req.Op, call.ProgId + ".Transact");
            string raw;
            if (!Transact(ctx, call.ProgId, xml, out raw))
            {
                if (call.AllowMissing)
                {
                    return null;
                }
                throw new BridgeException(503, "com_unavailable", call.ProgId + " 没有 Transact 成员，暂不能写入该档案（组件问题，重试无效）");
            }
            string dsc;
            int outcome = ArcXml.Outcome(raw, out dsc);
            if (outcome == 0)
            {
                throw new BridgeException(409, "u8_rejected", dsc);
            }
            CoRows.Note(ctx.Item, "arc " + req.Kind.Name + " " + call.Proc + " " + req.Code);
            Dictionary<string, string> row = Fresh(ctx, req, call.Read);
            // 修改只看得出名称：报文无法识别时即使读得到也算结果不明。
            if (Wanted(req, call.Proc, row) && (outcome > 0 || call.Proc != "diffedit"))
            {
                return Done(req, call.Proc);
            }
            if (outcome > 0 || call.Proc == "diffedit")
            {
                CoRows.Note(ctx.Item, "arc " + ArcXml.Short(raw));
                throw new BridgeException(504, "outcome_unknown", Unknown(req, call.Proc));
            }
            throw new BridgeException(409, "u8_rejected", ArcXml.Short(raw));
        }

        // false：组件没有 Transact 成员（DISP_E_UNKNOWNNAME），U8 什么也没做。其他 COM 异常把原文当返回，交给回读判断。
        static bool Transact(WorkContext ctx, string progId, string xml, out string raw)
        {
            raw = null;
            if (ctx.Session == null || ctx.Session.Login == null)
            {
                throw new BridgeException(500, "internal", "档案写入缺少 U8 登录");
            }
            object cc = null;
            try
            {
                cc = ComUtil.Create(progId);
                if (cc == null)
                {
                    throw new BridgeException(503, "com_unavailable", progId + " 未注册");
                }
                // 登录 by-ref 交给自己提交的导入组件，本次登录不放回缓存（LoginCache）。
                ctx.DropLogin();
                object[] args = new object[] { xml, ctx.Session.Login };
                raw = Values.Text(ComUtil.CallRef(cc, "Transact", args, new int[] { 1 }));
                return true;
            }
            catch (COMException ex)
            {
                if (ex.ErrorCode == UnknownName)
                {
                    return false;
                }
                raw = ex.Message;
                return true;
            }
            catch (MissingMethodException)
            {
                return false;
            }
            finally
            {
                ComUtil.Final(cc);
            }
        }

        // 新增、修改：读得到，且调用方给了名称时名称一致；删除：读不到。
        static bool Wanted(ArcReq req, string proc, Dictionary<string, string> row)
        {
            if (proc == "delete")
            {
                return row == null;
            }
            if (row == null)
            {
                return false;
            }
            string tag = req.Kind.NameTag;
            string want = tag == null ? null : req.Fields.Get(tag);
            if (want == null)
            {
                return true;
            }
            string got;
            row.TryGetValue(req.Map.Column(tag) ?? "", out got);
            return string.Equals((got ?? "").Trim(), want.Trim(), StringComparison.Ordinal);
        }

        internal static Dictionary<string, string> Fresh(WorkContext ctx, ArcReq req, ArcGlRead read)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return read(conn);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "arc readback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "U8 已处理档案 " + req.Code + "，但回读失败，结果未知");
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static string Unknown(ArcReq req, string proc)
        {
            if (proc == "delete")
            {
                return "U8 返回已删除档案 " + req.Code + "，但回读仍能查到，结果未知";
            }
            if (proc == "diffedit")
            {
                return "档案 " + req.Code + " 的修改结果未知（U8 返回无法识别或回读与修改不一致）";
            }
            return "U8 返回已保存档案 " + req.Code + "，但回读查不到，结果未知";
        }

        internal static ApiResult Done(ArcReq req, string proc)
        {
            Dictionary<string, object> body = ArcRead.Head(req);
            if (proc == "delete")
            {
                body["deleted"] = true;
            }
            return ApiResult.Ok(body);
        }

        // 不按年度分表的档案（币种、凭证类别）：ArcRead.Row。
        internal static ArcGlRead Plain(ArcKind kind, string code)
        {
            return delegate(object conn) { return ArcRead.Row(conn, kind, code); };
        }

        // Rows 的一行 → 列名（不分大小写）→ 文本，去掉 NULL。
        internal static Dictionary<string, string> Texts(Dictionary<string, object> row)
        {
            if (row == null)
            {
                return null;
            }
            Dictionary<string, string> texts = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, object> pair in row)
            {
                string text = pair.Value as string;
                if (text != null)
                {
                    texts[pair.Key] = text;
                }
            }
            return texts;
        }

        // SQL Server 的唯一键冲突（2627 主键 / 唯一约束、2601 唯一索引）。库的排序规则不分大小写、全角半角，
        // 桥的预查按排序规则比较，仍撞上说明并发写入或规则差异，按 409 处理，不当 500。
        internal static bool DuplicateKey(Exception ex)
        {
            if (ex == null || ex is BridgeException)
            {
                return false;
            }
            string text = ex.Message ?? "";
            return text.IndexOf("duplicate key", StringComparison.OrdinalIgnoreCase) >= 0 || text.IndexOf("重复键", StringComparison.Ordinal) >= 0;
        }

        internal static string Cell(Dictionary<string, string> row, string column)
        {
            string value;
            if (row == null || column == null || !row.TryGetValue(column, out value) || value == null)
            {
                return "";
            }
            return value.Trim();
        }

        static void Tag(StringBuilder sb, string tag, string value)
        {
            sb.Append('<').Append(tag).Append('>');
            string text = value ?? "";
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '&')
                {
                    sb.Append("&amp;");
                }
                else if (c == '<')
                {
                    sb.Append("&lt;");
                }
                else if (c == '>')
                {
                    sb.Append("&gt;");
                }
                else if (c >= ' ' || c == '\t' || c == '\n' || c == '\r')
                {
                    sb.Append(c);
                }
            }
            sb.Append("</").Append(tag).Append('>');
        }
    }
}
