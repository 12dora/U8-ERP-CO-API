using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace U8Co
{
    // 生产订单新增（vouchers/create，type=production_order）：U8API MOrderAdd，登录子系统 MO，与审核同一个 U8ApiComBroker。
    // Connect 之后 GetExtBoEntity("extbo") 取扩展实体，NewItem 建表头、GetSubEntity("Mom_OrderDetail") 建行，SetValue 填字段；
    // 不送 Mom_MoAllocate，U8 按标准 BOM 自动展开子件。API 自己开 TransactionScope 提交，不包 CoTrans。
    // 新 MoId 不在表头 GetValue("MoId") 上，从 InvokeApi 之后 ext.Serialize() 的 <mom_order moid="…"> 取，
    // 在新连接上确认这张订单存在且制单人是本操作员；拿不到时由 MoCreateLost 严格比对找唯一一张。
    // 之后在新连接上回读单号、行状态和每行子件数。见 docs/u8-notes.md「生产订单新增与删除」。
    internal static class MoCreate
    {
        public const string CreateRule = "write:production_order:create";
        const string AddUrl = "U8API/MOrder/MOrderAdd";
        static readonly Regex HeadMoId = new Regex("<mom_order\\b[^>]*\\bmoid=\"(\\d+)\"",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            U8Resolve.Enter();
            try
            {
                return Core(ctx, kind, head, lines);
            }
            finally
            {
                U8Resolve.Leave();
            }
        }

        static ApiResult Core(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            if (kind == null || kind.Name != "production_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持新增");
            }
            MoCreateAsk ask = MoCreateReq.Parse(head, lines);
            string user = Operator(ctx);
            PermContext perm = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(CreateRule);
            PermCheck.RequireRule(perm, rule);
            // 先查编码是否存在（不存在 400），再按数据权限判断（越权 403）。
            MoCreateSql.Validate(ctx.Conn, ask);
            MoDelete.CheckRows(perm, rule, PermRows(ask));
            MoCreateSql.Mark(ctx.Conn, ask);
            // 预演（校验模式）：MOrderAdd 自己提交，闸门查完就停。
            MoDry.CreateInput(ask);
            DryRun.Stop(ctx, "U8API MOrderAdd");
            MoAdded done = Invoke(ctx, ask);
            int id = Recover(ctx, ask, user, done);
            List<Dictionary<string, object>> after = Reread(ctx, id);
            return MoCreateOut.Result(kind, id, after);
        }

        static string Operator(WorkContext ctx)
        {
            string user = ctx.Item.Operator == null ? "" : ctx.Item.Operator.Trim();
            if (user.Length == 0)
            {
                throw new BridgeException(500, "internal", "缺少操作员");
            }
            return user;
        }

        // 数据权限按请求里的存货、部门、仓库判断（列名与 PermRegistry 的生产订单对象一致）。
        static List<Dictionary<string, object>> PermRows(MoCreateAsk ask)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["InvCode"] = ask.Lines[i].InvCode;
                row["MDeptCode"] = ask.Lines[i].Dept;
                row["WhCode"] = ask.Lines[i].Wh;
                rows.Add(row);
            }
            return rows;
        }

        // 调用前（建对象、Connect、填实体）出错照常报；InvokeApi 开始后的非桥异常和 IPC 错误交给 Recover 按库确认。
        static MoAdded Invoke(WorkContext ctx, MoCreateAsk ask)
        {
            if (ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "没有 U8 登录");
            }
            MoAdded done = new MoAdded();
            MoCom com = new MoCom();
            try
            {
                com.Env = MoApi.Need(ComUtil.Create(MoApi.EnvProgId));
                ComUtil.Set(com.Env, "U8Login", ctx.Session.Login);
                com.Broker = MoApi.Need(ComUtil.Create(MoApi.BrokerProgId));
                ComUtil.Call(com.Broker, "Connect", new object[] { AddUrl, com.Env });
                com.Connected = true;
                com.Ext = ComUtil.Call(com.Broker, "GetExtBoEntity", new object[] { "extbo" });
                Fill(com, ask);
                com.Invoked = true;
                bool ok = Values.Flag(ComUtil.Call(com.Broker, "InvokeApi", new object[0]));
                string err = Values.Text(ComUtil.Call(com.Broker, "GetLastError", new object[0])).Trim();
                Settle(ctx, done, ok, err);
                done.MoId = ok ? MoIdOf(ctx, com.Ext) : 0;
                return done;
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, ex.GetType().Name + " " + MoApi.FirstLine(ex.Message));
                if (!com.Invoked)
                {
                    throw MoApi.Failed(ex);
                }
                done.Lost = ex;
                return done;
            }
            finally
            {
                com.Release();
            }
        }

        // InvokeApi 返回 false：IPC 错误（U8MPool 没起）留给 Recover，其余是 U8 拒绝（409 u8_rejected，原文第一行）。
        static void Settle(WorkContext ctx, MoAdded done, bool ok, string err)
        {
            if (err.Length > 0)
            {
                CoRows.Note(ctx.Item, MoApi.FirstLine(err));
            }
            if (!ok && MoApi.IsIpc(err))
            {
                done.Lost = new InvalidOperationException(err);
                return;
            }
            if (!ok)
            {
                throw MoApi.Refused(err);
            }
        }

        static void Fill(MoCom com, MoCreateAsk ask)
        {
            object head = com.Keep(ComUtil.Call(com.Ext, "NewItem", new object[0]));
            if (ask.Code.Length > 0)
            {
                Put(head, "MoCode", ask.Code);
            }
            object body = com.Keep(ComUtil.Call(head, "GetSubEntity", new object[] { "Mom_OrderDetail" }));
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                MoLine line = ask.Lines[i];
                object row = com.Keep(ComUtil.Call(body, "NewItem", new object[0]));
                Put(row, "DSortSeq", line.Seq);
                Put(row, "DMoClass", 1);
                Put(row, "DInvCode", line.InvCode);
                Put(row, "DQty", line.Qty);
                Put(row, "DStartDate", line.Start);
                Put(row, "DDueDate", line.Due);
                Put(row, "DMoTypeCode", line.MoType);
                Put(row, "DMDeptCode", line.Dept);
                PutOptional(row, "DWhCode", line.Wh);
                PutOptional(row, "DRemark", line.Remark);
            }
        }

        static void Put(object item, string name, object value)
        {
            ComUtil.Call(item, "SetValue", new object[] { name, value });
        }

        static void PutOptional(object item, string name, string value)
        {
            if (value != null && value.Length > 0)
            {
                Put(item, name, value);
            }
        }

        // 取不到 MoId 返回 0，由 Recover 按库找；这里的异常不能让已成功的新增报成失败。
        static int MoIdOf(WorkContext ctx, object ext)
        {
            try
            {
                string xml = Values.Text(ComUtil.Call(ext, "Serialize", new object[0]));
                Match m = HeadMoId.Match(xml);
                return m.Success ? CoRows.AsId(m.Groups[1].Value) : 0;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoCreate Serialize " + MoApi.FirstLine(ex.Message));
                return 0;
            }
        }

        // 在新连接上定下新订单：U8 给了 MoId 且该订单存在、制单人是本操作员就认它（不看调用前的最大 MoId）；
        // 否则按 MoCreateLost 严格比对找唯一一张。找到就算成功；找不到时 IPC 错误报 503（多半没建成），
        // 其他情况结果未知 504，不能让调用方重投。
        static int Recover(WorkContext ctx, MoCreateAsk ask, string user, MoAdded done)
        {
            int id = 0;
            int loose = 1;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                id = MoCreateSql.Owned(conn, done.MoId, user) ? done.MoId : MoCreateLost.Find(conn, ask, user, out loose);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoCreate " + MoApi.FirstLine(ex.Message));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (id > 0)
            {
                return id;
            }
            if (loose > 0 && done.Lost != null)
            {
                // 有同存货的新单但对不上唯一一张（或回读失败）：即使是 IPC 错误也不能说「没建成」。
                throw new BridgeException(504, "outcome_unknown", "U8 调用异常，可能已建成生产订单但无法确认，结果未知");
            }
            throw Unknown(done.Lost);
        }

        // lost 为空说明 InvokeApi 返回了成功（只是拿不到或确认不了新 MoId）。
        static BridgeException Unknown(Exception lost)
        {
            if (lost == null)
            {
                return new BridgeException(504, "outcome_unknown", "U8 已返回成功但回读不到新生产订单，结果未知");
            }
            if (MoApi.IsIpc(lost.Message))
            {
                return new BridgeException(503, "u8_unavailable", "U8 生产制造服务未运行");
            }
            string text = MoApi.FirstLine(lost.Message);
            return new BridgeException(504, "outcome_unknown",
                "U8 新增生产订单调用异常，结果未知" + (text.Length > 0 ? "：" + text : ""));
        }

        static List<Dictionary<string, object>> Reread(WorkContext ctx, int id)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                List<Dictionary<string, object>> rows = MoCreateSql.Lines(conn, id);
                if (rows.Count > 0)
                {
                    return rows;
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoCreate " + MoApi.FirstLine(ex.Message));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            throw new BridgeException(504, "outcome_unknown", "已新增生产订单但未能回读，MoId " + id);
        }

        sealed class MoAdded
        {
            public int MoId;
            public Exception Lost;
        }

        // 本次调用建的 COM 对象：扩展实体的子对象只 ReleaseOne，自己 Create 的 Env / Broker 在 Disconnect 之后 Final。
        sealed class MoCom
        {
            public object Env;
            public object Broker;
            public object Ext;
            public bool Connected;
            public bool Invoked;
            readonly List<object> _items = new List<object>();

            public object Keep(object item)
            {
                _items.Add(item);
                return item;
            }

            public void Release()
            {
                for (int i = _items.Count - 1; i >= 0; i--)
                {
                    ComUtil.ReleaseOne(_items[i]);
                }
                ComUtil.ReleaseOne(Ext);
                MoApi.Disconnect(Broker, Connected);
                ComUtil.Final(Broker);
                ComUtil.Final(Env);
            }
        }
    }
}
