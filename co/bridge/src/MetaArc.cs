using System;
using System.Collections.Generic;

namespace U8Co
{
    // meta 的档案、总账凭证、列表类型、路由部分。
    internal static class MetaArc
    {
        // 档案标签来自 U8 安装目录下的 RsXml（ArcMap.Of，按文件缓存）；读不到时该档案 tags 为 null 并带 tags_error，
        // complete 置 false，调用方不缓存这次结果。
        internal static List<object> Archives(out bool complete)
        {
            complete = true;
            List<ArcKind> kinds = new List<ArcKind>(ArcKind.List());
            kinds.Sort(delegate(ArcKind a, ArcKind b) { return string.CompareOrdinal(a.Name, b.Name); });
            List<object> list = new List<object>();
            for (int i = 0; i < kinds.Count; i++)
            {
                Dictionary<string, object> d = Head(kinds[i]);
                if (!Tags(kinds[i], d))
                {
                    complete = false;
                }
                list.Add(d);
            }
            return list;
        }

        static Dictionary<string, object> Head(ArcKind k)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["name"] = k.Name;
            d["root"] = MetaKinds.Text(k.Root);
            d["rs_file"] = MetaKinds.Text(k.RsFile);
            d["table"] = MetaKinds.Text(k.Table);
            d["key"] = MetaKinds.Text(k.Key);
            d["name_col"] = MetaKinds.Text(k.NameCol);
            d["need_template"] = k.NeedTemplate;
            d["read_only"] = k.ReadOnly;
            d["block"] = Copy(k.Block);
            d["private"] = Copy(k.Private);
            d["tags"] = null;
            d["writable"] = k.ReadOnly ? new string[0] : null;
            // 项目不走 RsXml，tags / writable 取固定标签表；deletable 标出能否删除。
            d["deletable"] = !k.ReadOnly && !k.NoDelete;
            d["updatable"] = !k.ReadOnly && !k.NoUpdate;
            if (k.SqlMap != null && !k.ReadOnly)
            {
                d["tags"] = new List<string>(k.SqlMap.Tags).ToArray();
                d["writable"] = new List<string>(k.SqlMap.Tags).ToArray();
            }
            return d;
        }

        // 返回 false 表示 RsXml 读取失败。没有 RsXml 的档案（只读类型）不算失败。
        static bool Tags(ArcKind k, Dictionary<string, object> d)
        {
            if (string.IsNullOrEmpty(k.RsFile))
            {
                return true;
            }
            ArcMap map;
            try
            {
                map = ArcMap.Of(k);
            }
            catch (BridgeException ex)
            {
                d["tags_error"] = ex.Message;
                return false;
            }
            List<string> tags = new List<string>(map.Tags);
            List<string> writable = new List<string>();
            for (int i = 0; i < tags.Count; i++)
            {
                if (!k.Blocked(tags[i]))
                {
                    writable.Add(tags[i]);
                }
            }
            d["tags"] = tags.ToArray();
            d["writable"] = k.ReadOnly ? new string[0] : writable.ToArray();
            return true;
        }

        // 照 GlReq.Draft / Line / Flows：head.sign 必填；分录 account、digest 必填，借贷二选一；2 到 200 行；现金流量 ≤ 50 项。
        internal static Dictionary<string, object> Gl()
        {
            string[][] names = GlReq.MetaNames();
            Dictionary<string, object> gl = new Dictionary<string, object>();
            gl["head"] = names[0];
            gl["line"] = names[1];
            gl["cash_flow"] = names[2];
            gl["required_head"] = new string[] { "sign" };
            gl["required_line"] = new string[] { "account", "digest" };
            gl["lines_min"] = 2;
            gl["lines_max"] = 200;
            gl["cash_flow_max"] = 50;
            // 记账 gl/vouchers/post 一次最多的凭证张数（GlPostParse.Max）。
            gl["post_max"] = GlPostParse.Max;
            return gl;
        }

        internal static string[] ListKindNames()
        {
            List<string> names = new List<string>(ListKinds.Names());
            names.Sort(StringComparer.Ordinal);
            return names.ToArray();
        }

        internal static List<object> Routes()
        {
            List<object> list = new List<object>();
            foreach (KeyValuePair<string, string[]> pair in Requests.MetaRoutes())
            {
                list.Add(Route(pair.Key, pair.Value));
            }
            list.Add(Route(BridgeMeta.Path, new string[0]));
            return list;
        }

        // optional：不在 keys 里、可选的顶层字段。写路由收幂等键（IdemReq.Supports = WriteGate 写路由），caller 由 API 填，直接调桥可省略。
        static Dictionary<string, object> Route(string path, string[] keys)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["path"] = path;
            d["keys"] = keys;
            d["optional"] = Optional(path);
            return d;
        }

        // 写路由另收 dry_run（DryRunReq.Accepts；自动核销的 dry_run 本来就在 keys 里）。
        static string[] Optional(string path)
        {
            List<string> list = new List<string>();
            if (IdemReq.Supports(path))
            {
                list.Add(IdemReq.KeyField);
                list.Add(IdemReq.CallerField);
            }
            if (DryRunReq.Accepts(path))
            {
                list.Add(DryRunReq.Field);
            }
            return list.ToArray();
        }

        static string[] Copy(string[] list)
        {
            return list == null ? new string[0] : (string[])list.Clone();
        }
    }
}
