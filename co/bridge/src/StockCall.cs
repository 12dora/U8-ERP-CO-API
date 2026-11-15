using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;

namespace U8Co
{
    // 调用 VoucherCO：打开、装载、审核参数、事务。模板 DOM 的行也在这里写入。
    internal static partial class StockCall
    {
        const string RsNs = "urn:schemas-microsoft-com:rowset";
        const string RowNs = "#RowsetSchema";

        // 不写 Login / bCheckRight，不调 InitAccountInfo。子产品在登录时已按 Kind.SubId 选定。
        public static object OpenCo(WorkContext ctx)
        {
            object co = ComUtil.Create("USERPCO.VoucherCO");
            if (co == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 组件未注册");
            }
            try
            {
                object[] args = new object[] { ctx.Session.Login, "" };
                object ret = ComUtil.CallRef(co, "IniLogin", args, new int[] { 1 });
                if (Values.Flag(ret))
                {
                    object keep = co;
                    co = null;
                    return keep;
                }
                string err = Values.Text(args[1]).Trim();
                if (err.Length == 0)
                {
                    err = "初始化库存组件失败";
                }
                AddDetail(ctx, err);
                throw new BridgeException(409, "u8_rejected", err);
            }
            finally
            {
                ComUtil.Final(co);
            }
        }

        public static void CallLoad(WorkContext ctx, object co, VoucherKind kind, int id, StockLoaded loaded)
        {
            object head0 = loaded.Head;
            object body0 = loaded.Body;
            object pos0 = loaded.Pos;
            string where = "id=" + id.ToString(CultureInfo.InvariantCulture);
            object[] args = new object[] { kind.StType, where, loaded.Head, loaded.Body, loaded.Pos, "", false, "" };
            object ret = ComUtil.CallRef(co, "Load", args, new int[] { 2, 3, 4, 5 });
            loaded.Head = Kept(head0, args[2]);
            loaded.Body = Kept(body0, args[3]);
            loaded.Pos = Kept(pos0, args[4]);
            if (Values.Flag(ret))
            {
                return;
            }
            string err = Values.Text(args[5]).Trim();
            if (err.Length == 0)
            {
                err = "U8 拒绝了操作";
            }
            AddDetail(ctx, err);
            throw new BridgeException(409, "u8_rejected", err);
        }

        // 视图 ufts 已是 nchar(money)。只 TrimEnd，保留前导空格。没有行就是单据不存在。
        public static string Ufts(object conn, VoucherKind kind, int id)
        {
            string raw = Rows.Scalar(conn,
                "select ufts from " + StockDom.HeadView(kind) + " with(nolock) where id=?",
                new object[] { id });
            if (raw == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return raw.TrimEnd();
        }

        // Verify、UnVerify、Delete 都是这 9 个参数。MakeWheres 等可选参数传 Missing 会类型不匹配。
        public static object[] VerifyArgs(VoucherKind kind, int id, object conn, string ufts,
            object msg, out int[] refs)
        {
            refs = new int[] { 2, 3, 4, 5, 6, 7, 8 };
            return new object[] { kind.StType, id, "", conn, ufts, msg, true, true, false };
        }

        public static object[] InsertArgs(VoucherKind kind, StockForms forms, out int[] refs)
        {
            refs = new int[] { 4, 5, 6, 7, 8, 9 };
            return new object[]
            {
                kind.StType, forms.Head, forms.Body, forms.Pos, "", forms.Conn, "", forms.Msg,
                true, true, false, "", false
            };
        }

        // Update 12 个参数。pos 不在引用槽里；err/conn/msg 和两个 true 是引用。
        public static object[] UpdateArgs(VoucherKind kind, StockForms forms, out int[] refs)
        {
            refs = new int[] { 4, 5, 6, 7, 8 };
            return new object[]
            {
                kind.StType, forms.Head, forms.Body, forms.Pos, "", forms.Conn, forms.Msg, true, true, false, "", true
            };
        }

        // 调拨审核多一个空的 MakeWheres 槽和 Scripting.Dictionary，字典的键是生成的 09/08 主键。
        public static object[] TransferVerifyArgs(VoucherKind kind, int id, object conn, string ufts,
            object msg, out int[] refs)
        {
            object dict = ComUtil.Create("Scripting.Dictionary");
            if (dict == null)
            {
                throw new BridgeException(503, "com_unavailable", "Scripting 未注册");
            }
            refs = new int[] { 2, 3, 4, 5, 6, 7, 8, 10, 11 };
            return new object[]
            {
                kind.StType, id, "", conn, ufts, msg, true, true, false,
                new DispatchWrapper(null), "", dict
            };
        }

        // MakeOutVouch 整单生成，由 RunAt 的 CommitSeen 提交（审计里 @@TRANCOUNT 前后都是 1，仍在桥的事务里）。
        // 按行部分出库的改单不在这个事务里（StockGenSalePart）。dry 是预演时提交前的登记（StockAt.Dry）。
        public static void RunMake(WorkContext ctx, object co, int dlid, object conn, object msg, StockCheck dry)
        {
            object[] args = new object[] { dlid, new DispatchWrapper(null), "", conn, msg, true };
            int[] refs = new int[] { 1, 2, 3, 4, 5 };
            try
            {
                StockAt at = AtOf("MakeOutVouch", args, refs, null, 2, 4);
                at.ConnAt = 3;
                at.Dry = dry;
                RunAt(ctx, co, at);
            }
            finally
            {
                ReleaseSlot(args[1], conn);
            }
        }

        public static void RunCo(WorkContext ctx, object co, string method, object[] args, int[] refs, string ufts)
        {
            RunAt(ctx, co, AtFor(method, args, refs, ufts));
        }

        // 提交前的回写核对。事务还在：照常抛出（调用方回滚后 409）。U8 已自行结束事务（@@TRANCOUNT 为 0 或读不到）
        // 时回滚不了：任何异常都改报 504 outcome_unknown（不报 500，免得调用方重投造成重复），并记下供人工核对。
        public static void AfterCheck(WorkContext ctx, StockCheck check, string label)
        {
            try
            {
                check(ctx.Conn);
            }
            catch (Exception ex)
            {
                if (Alive(ctx.Conn))
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "U8 已提交，回写核对失败 " + label + "：" + ex.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "U8 已自行提交（已保存/已删除），回写核对失败，需要人工核对：" + label);
            }
        }

        static bool Alive(object conn)
        {
            try
            {
                return CoTrans.Count(conn) != "0";
            }
            catch (Exception)
            {
                return false;
            }
        }

        // 调用方要在事务里加 Before / After 核对时，先取 StockAt 再交给 RunAt。
        public static StockAt AtFor(string method, object[] args, int[] refs, string ufts)
        {
            int errAt;
            int msgAt;
            int connAt;
            Slots(method, out errAt, out msgAt, out connAt);
            StockAt at = AtOf(method, args, refs, ufts, errAt, msgAt);
            at.ConnAt = connAt;
            return at;
        }

        // 调用方 BeginTrans，并把同一连接传入 cnnFrom。0x8004D00E 由 CoTrans.CommitSeen 当成已提交。
        public static void RunAt(WorkContext ctx, object co, StockAt at)
        {
            string method = at.Method;
            object[] args = at.Args;
            int[] refs = at.Refs;
            string ufts = at.Ufts;
            int errAt = at.ErrAt;
            int msgAt = at.MsgAt;
            int connAt = at.ConnAt;
            object conn = ctx.Conn;
            object msg0 = args[msgAt];
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                if (at.Before != null)
                {
                    at.Before(conn);
                }
                object ret = ComUtil.CallRef(co, method, args, refs);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                bool ok = Values.Flag(ret);
                string err = Values.Text(args[errAt]).Trim();
                object use = args[msgAt] == null ? msg0 : args[msgAt];
                if (!ok)
                {
                    Fail(ctx, err, StockMsg.Shortage(use), use);
                }
                if (err.Length > 0)
                {
                    AddDetail(ctx, err);
                }
                if (at.After != null)
                {
                    AfterCheck(ctx, at.After, "单据 " + ctx.Item.Id.ToString(CultureInfo.InvariantCulture));
                }
                DryAt(ctx, at);
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                if (ufts != null)
                {
                    AddDetail(ctx, "ufts=[" + ufts + "]");
                }
                NoteTran(conn, ctx.Item);
                Rollback(conn, open);
                throw;
            }
            finally
            {
                ReleaseMsg(args, msgAt, msg0);
                SeenConn(ctx, conn, args, connAt);
            }
        }

        // Insert 不自动编号（空 cCode 时 U8 报「单据号不能为零、空、空串」），按 U8 编号规则取号，见 BillNo。
        public static string NewCode(WorkContext ctx, object co, VoucherKind kind, Dictionary<string, object> head)
        {
            object usLogin = ComUtil.Get(co, "Login");
            try
            {
                string code = BillNo.Allocate(usLogin, ctx.Session.Login, kind.StType, head);
                AddDetail(ctx, "code=" + code);
                return code;
            }
            finally
            {
                ComUtil.Final(usLogin);
            }
        }

        public static int NewId(WorkContext ctx, object raw)
        {
            string text = Values.Text(raw).Trim();
            int id;
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out id) && id > 0)
            {
                return id;
            }
            AddDetail(ctx, "vouchid=[" + text + "]");
            return 0;
        }

        public static List<string> FieldNames(object rs)
        {
            object fields = null;
            try
            {
                fields = ComUtil.Get(rs, "Fields");
                int count = Convert.ToInt32(ComUtil.Get(fields, "Count"));
                List<string> names = new List<string>();
                for (int i = 0; i < count; i++)
                {
                    object field = null;
                    try
                    {
                        field = ComUtil.Call(fields, "Item", new object[] { i });
                        names.Add(Values.Text(ComUtil.Get(field, "Name")));
                    }
                    finally
                    {
                        ComUtil.ReleaseOne(field);
                    }
                }
                return names;
            }
            finally
            {
                ComUtil.ReleaseOne(fields);
            }
        }

        public static void AppendRow(object dom, Dictionary<string, string> row)
        {
            object data = null;
            object node = null;
            object root = null;
            object appended = null;
            try
            {
                Rows.UseXPath(dom);
                data = ComUtil.Call(dom, "selectSingleNode", new object[] { "//*[local-name()='data']" });
                if (data == null)
                {
                    data = ComUtil.Call(dom, "createNode", new object[] { 1, "rs:data", RsNs });
                    root = ComUtil.Get(dom, "documentElement");
                    appended = ComUtil.Call(root, "appendChild", new object[] { data });
                    ComUtil.ReleaseOne(appended);
                    appended = null;
                }
                node = ComUtil.Call(dom, "createNode", new object[] { 1, "z:row", RowNs });
                foreach (KeyValuePair<string, string> kv in row)
                {
                    if (kv.Value == null)
                    {
                        continue;
                    }
                    ComUtil.Call(node, "setAttribute", new object[] { kv.Key, kv.Value });
                }
                appended = ComUtil.Call(data, "appendChild", new object[] { node });
            }
            finally
            {
                ComUtil.ReleaseOne(appended);
                ComUtil.ReleaseOne(node);
                ComUtil.ReleaseOne(root);
                ComUtil.ReleaseOne(data);
            }
        }

        public static void CloseRs(object rs)
        {
            if (rs == null)
            {
                return;
            }
            try
            {
                ComUtil.Call(rs, "Close", new object[0]);
            }
            catch (Exception)
            {
            }
            ComUtil.Final(rs);
        }

        static void Slots(string method, out int errAt, out int msgAt, out int connAt)
        {
            if (method == "Insert")
            {
                errAt = 4;
                msgAt = 7;
                connAt = 5;
                return;
            }
            if (method == "Update")
            {
                errAt = 4;
                msgAt = 6;
                connAt = 5;
                return;
            }
            errAt = 2;
            msgAt = 5;
            connAt = 3;
        }

        static void ReleaseSlot(object slot, object conn)
        {
            if (slot == null || object.ReferenceEquals(slot, conn))
            {
                return;
            }
            ComUtil.Final(slot);
        }

        internal static void Fail(WorkContext ctx, string err, string lack, object msg)
        {
            string text = err ?? "";
            if (lack != null && lack.Length > 0)
            {
                text = text.Length == 0 ? lack : text + " | " + lack;
            }
            else if (text.Length == 0)
            {
                text = XmlBit(msg);
            }
            AddDetail(ctx, text);
            if (lack != null && lack.Length > 0)
            {
                throw new BridgeException(409, "stock_shortage", lack);
            }
            if (err == null || err.Length == 0)
            {
                err = "U8 拒绝了操作";
            }
            throw new BridgeException(409, "u8_rejected", err);
        }

        public static StockForms Forms(object head, object body, object pos, object conn, object msg)
        {
            StockForms forms = new StockForms();
            forms.Head = head;
            forms.Body = body;
            forms.Pos = pos;
            forms.Conn = conn;
            forms.Msg = msg;
            return forms;
        }

        static StockAt AtOf(string method, object[] args, int[] refs, string ufts, int errAt, int msgAt)
        {
            StockAt at = new StockAt();
            at.Method = method;
            at.Args = args;
            at.Refs = refs;
            at.Ufts = ufts;
            at.ErrAt = errAt;
            at.MsgAt = msgAt;
            return at;
        }

        static object Kept(object original, object updated)
        {
            if (updated == null || object.ReferenceEquals(updated, original))
            {
                return original;
            }
            ComUtil.Final(original);
            return updated;
        }

        internal static void ReleaseMsg(object[] args, int msgAt, object msg0)
        {
            object seen = args[msgAt];
            if (seen != null && !object.ReferenceEquals(seen, msg0))
            {
                ComUtil.Final(seen);
                args[msgAt] = msg0;
            }
        }

        // by-ref 换回来的连接和我们传入的不是同一个对象时，必须释放，不能只记日志。
        internal static void SeenConn(WorkContext ctx, object original, object[] args, int connAt)
        {
            object updated = args[connAt];
            if (updated == null || object.ReferenceEquals(original, updated))
            {
                return;
            }
            AddDetail(ctx, "cnnFrom replaced");
            ComUtil.Final(updated);
            args[connAt] = original;
        }

        static void NoteTran(object conn, WorkItem item)
        {
            if (item == null || item.TranAfter != null)
            {
                return;
            }
            try
            {
                item.TranAfter = CoTrans.Count(conn);
            }
            catch (Exception)
            {
            }
        }

        static void Rollback(object conn, bool open)
        {
            if (!open)
            {
                return;
            }
            try
            {
                CoTrans.Rollback(conn);
            }
            catch (Exception)
            {
            }
        }

        static string XmlBit(object dom)
        {
            if (dom == null)
            {
                return "";
            }
            try
            {
                string xml = Values.Text(ComUtil.Get(dom, "xml")).Trim();
                if (xml.Length > 160)
                {
                    return xml.Substring(0, 160);
                }
                return xml;
            }
            catch (Exception)
            {
                return "";
            }
        }

        internal static void AddDetail(WorkContext ctx, string extra)
        {
            if (ctx == null)
            {
                return;
            }
            CoRows.Note(ctx.Item, extra);
        }
    }
}
