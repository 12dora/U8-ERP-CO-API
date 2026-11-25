using System;
using System.Collections.Generic;

namespace U8Co
{
    internal static class ProbeDom
    {
        public const int AdUseClient = 3;
        public const int AdOpenStatic = 3;
        public const int AdLockReadOnly = 1;
        public const int AdPersistXml = 1;
        public const string RsNs = "urn:schemas-microsoft-com:rowset";
        public const string RowNs = "#RowsetSchema";
        public const string SelectNs = "xmlns:rs='urn:schemas-microsoft-com:rowset' xmlns:z='#RowsetSchema'"
            + " xmlns:s='uuid:BDC6E3F0-6DA3-11d1-A2A3-00AA00C14882'"
            + " xmlns:dt='uuid:C2F41010-65B3-11d1-A29F-00AA00C14882'";

        public static void Exec(ProbeRun run, Dictionary<string, object> step)
        {
            string op = StepIo.Op(step);
            if (Head(run, step, op))
            {
                return;
            }
            if (op == "dom.addrow")
            {
                ProbeRows.Add(run, step);
                return;
            }
            if (op == "dom.rows")
            {
                ProbeRows.Show(run, step);
                return;
            }
            if (op == "dom.set")
            {
                ProbeRows.Set(run, step);
                return;
            }
            if (op == "dom.single")
            {
                ProbeRows.Single(run, step);
                return;
            }
            throw new BridgeException(400, "bad_request", "不认识操作 " + op);
        }

        public static void UseXPath(object dom)
        {
            ComUtil.Call(dom, "setProperty", new object[] { "SelectionLanguage", "XPath" });
            ComUtil.Call(dom, "setProperty", new object[] { "SelectionNamespaces", SelectNs });
        }

        static bool Head(ProbeRun run, Dictionary<string, object> step, string op)
        {
            if (op == "dom.new")
            {
                NewDom(run, step);
                return true;
            }
            if (op == "dom.blank")
            {
                Blank(run, step);
                return true;
            }
            if (op == "dom.load")
            {
                Load(run, step);
                return true;
            }
            if (op == "dom.xml")
            {
                Xml(run, step);
                return true;
            }
            return false;
        }

        static void NewDom(ProbeRun run, Dictionary<string, object> step)
        {
            NeedVar(step);
            object dom = ComUtil.Create("MSXML2.DOMDocument");
            if (dom == null)
            {
                throw new BridgeException(503, "com_unavailable", "MSXML 未注册");
            }
            ComUtil.Set(dom, "async", false);
            UseXPath(dom);
            Keep(run, step, dom);
        }

        static void Blank(ProbeRun run, Dictionary<string, object> step)
        {
            NeedVar(step);
            object rs = null;
            object dom = null;
            try
            {
                rs = OpenBlank(run.NeedConn(), StepIo.Req(step, "sql"));
                dom = Persist(rs);
                ProbeAdo.CloseRs(rs);
                rs = null;
                UseXPath(dom);
                Keep(run, step, dom);
                dom = null;
            }
            finally
            {
                ProbeAdo.CloseRs(rs);
                ComUtil.Final(dom);
            }
        }

        static void Load(ProbeRun run, Dictionary<string, object> step)
        {
            NeedVar(step);
            if (!StepIo.Present(step, "id"))
            {
                throw new BridgeException(400, "bad_request", "缺少 id");
            }
            object cmd = null;
            object rs = null;
            object dom = null;
            try
            {
                object[] args = new object[] { Nums.AsInt(step["id"]) };
                rs = ProbeAdo.OpenRs(run.NeedConn(), StepIo.Req(step, "sql"), args, out cmd);
                if (ProbeAdo.Eof(rs))
                {
                    throw new BridgeException(404, "not_found", "查询没有行");
                }
                dom = Persist(rs);
                UseXPath(dom);
                Keep(run, step, dom);
                dom = null;
            }
            finally
            {
                ProbeAdo.CloseRs(rs);
                ComUtil.Final(cmd);
                ComUtil.Final(dom);
            }
        }

        static void Xml(ProbeRun run, Dictionary<string, object> step)
        {
            object dom = run.NeedRef(StepIo.Req(step, "dom"));
            int max = Limit(step, 4000, 20000);
            string xml = Values.Text(ComUtil.Get(dom, "xml"));
            Line line = ProbeOut.Begin(run, step, true);
            line.Num("len", xml.Length);
            line.Str("xml", ProbeOut.Cut(xml, max));
            ProbeOut.Emit(line);
        }

        static void Keep(ProbeRun run, Dictionary<string, object> step, object dom)
        {
            Line line = ProbeOut.Begin(run, step, true);
            ProbeOut.Keep(run, step, dom, line);
            ProbeOut.Emit(line);
        }

        static void NeedVar(Dictionary<string, object> step)
        {
            if (!StepIo.Has(step, "var"))
            {
                throw new BridgeException(400, "bad_request", "需要变量");
            }
        }

        static int Limit(Dictionary<string, object> step, int fallback, int cap)
        {
            if (!StepIo.Present(step, "max") || step["max"] == null)
            {
                return fallback;
            }
            int max = Nums.AsInt(step["max"]);
            if (max < 1 || max > cap)
            {
                throw new BridgeException(400, "bad_request", "max 无效");
            }
            return max;
        }

        static object OpenBlank(object conn, string sql)
        {
            object rs = ComUtil.Create("ADODB.Recordset");
            if (rs == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            try
            {
                // 客户端游标才能把 where 1=2 的空记录集存成带架构、没有 z:row 的 DOM。
                ComUtil.Set(rs, "CursorLocation", AdUseClient);
                ComUtil.Call(rs, "Open", new object[] { sql, conn, AdOpenStatic, AdLockReadOnly });
                return rs;
            }
            catch
            {
                ProbeAdo.CloseRs(rs);
                throw;
            }
        }

        static object Persist(object rs)
        {
            object dom = ComUtil.Create("MSXML2.DOMDocument");
            if (dom == null)
            {
                throw new BridgeException(503, "com_unavailable", "MSXML 未注册");
            }
            try
            {
                ComUtil.Set(dom, "async", false);
                ComUtil.Call(rs, "Save", new object[] { dom, AdPersistXml });
                return dom;
            }
            catch
            {
                ComUtil.Final(dom);
                throw;
            }
        }
    }
}
