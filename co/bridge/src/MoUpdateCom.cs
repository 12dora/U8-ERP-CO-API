using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单修改的 U8API 调用（U8ApiComBroker，登录子系统 MO，API 自己开 TransactionScope 提交，不包 CoTrans）：
    // 1. Connect("U8API/MOrder/MOrderLoad")，AssignNormalValue("mocode")，InvokeApi，GetExtBoEntity("extbo") 取 U8 加载的整张订单
    //    （表头 → Mom_OrderDetail → Mom_MoAllocate，子件的全部字段都在里面）；
    // 2. 在加载出的实体上按 DSortSeq 改行字段，改了数量的行把每个子件的 DQty（和 DAuxQty）改成 MoUpdatePlan 算的值；
    //    Load 不带子件的 DRemark、DProductType，按快照补上（否则重写后变成缺省）；
    // 3. Load 的 broker 先 Disconnect，再用新建的 Env / Broker Connect("U8API/MOrder/MOrderUpdate")，SetExtBoEntity("extbo", 实体)，InvokeApi。
    //    同一时刻只连着一个（U8MPool 会话、许可点数）；Disconnect 只释放参数集，实体本身不释放（ExtensionBusinessObject.Release 为空）。
    // MOrderUpdate 先清掉每一行已有的子件再按送去的 Mom_MoAllocate 重插，所以子件必须全部重送（见 docs/u8-notes.md §9）。
    // 加载出的行数、子件数与快照对不上时在调用 Update 之前 409，不冒清空用料的险。
    // 未覆盖：Load 之后 GetExtBoEntity 拿到的是否就是加载结果（新增时同一实体上 Serialize 能读到 moid，按同理推断）、
    // Disconnect 之后把 Load 的实体交给 Update 是否可行、索引器 Item(i) 经 IDispatch 是否可取。
    // Load 把用量（DBaseQtyN / DBaseQtyD / DQty）按数量精度 Math.Round 过，桥把快照里的原值写回，不让精度丢在重插里。
    internal static class MoUpdateCom
    {
        const string LoadUrl = "U8API/MOrder/MOrderLoad";
        const string UpdateUrl = "U8API/MOrder/MOrderUpdate";

        // 调用前（建对象、Load、改实体、Connect Update）出错照常报；Update 的 InvokeApi 开始后的一切失败（U8 拒绝返回 MoRefused，
        // 其余异常、IPC 错误原样）都返回给调用方按库回读确认，由回读决定 409 / 200 / 503 / 504。
        public static Exception Invoke(WorkContext ctx, string code, MoPlan plan)
        {
            if (ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "没有 U8 登录");
            }
            MoUpdCom com = new MoUpdCom();
            try
            {
                com.Env = MoApi.Need(ComUtil.Create(MoApi.EnvProgId));
                ComUtil.Set(com.Env, "U8Login", ctx.Session.Login);
                com.Broker = MoApi.Need(ComUtil.Create(MoApi.BrokerProgId));
                object ext = Load(ctx, com, code);
                MoUpdateExt.Apply(com, ext, plan);
                MoApi.Disconnect(com.Broker, com.Connected);
                com.Connected = false;
                // 实测：同一个 broker Disconnect 后再 Connect 报「ConnectionString 属性尚未初始化」。Load 的连接先断开，
                // 再新建 Env / Broker 做 Update（旧的留到 Release，实体仍可用），同一时刻仍只连着一个。
                com.Retire();
                com.Env = MoApi.Need(ComUtil.Create(MoApi.EnvProgId));
                ComUtil.Set(com.Env, "U8Login", ctx.Session.Login);
                com.Broker = MoApi.Need(ComUtil.Create(MoApi.BrokerProgId));
                ComUtil.Call(com.Broker, "Connect", new object[] { UpdateUrl, com.Env });
                com.Connected = true;
                ComUtil.Call(com.Broker, "SetExtBoEntity", new object[] { "extbo", ext });
                com.Invoked = true;
                bool ok = Values.Flag(ComUtil.Call(com.Broker, "InvokeApi", new object[0]));
                string err = Values.Text(ComUtil.Call(com.Broker, "GetLastError", new object[0])).Trim();
                return Settle(ctx, ok, err);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoUpdate " + ex.GetType().Name + " " + MoApi.FirstLine(ex.Message));
                if (!com.Invoked)
                {
                    throw MoApi.Failed(ex);
                }
                return ex;
            }
            finally
            {
                com.Release();
            }
        }

        // Load 被拒（单据不存在、生产制造服务未运行）在修改之前，照 MoApi.Refused 报 409 / 503。
        static object Load(WorkContext ctx, MoUpdCom com, string code)
        {
            ComUtil.Call(com.Broker, "Connect", new object[] { LoadUrl, com.Env });
            com.Connected = true;
            ComUtil.Call(com.Broker, "AssignNormalValue", new object[] { "mocode", code });
            bool ok = Values.Flag(ComUtil.Call(com.Broker, "InvokeApi", new object[0]));
            if (!ok)
            {
                string err = Values.Text(ComUtil.Call(com.Broker, "GetLastError", new object[0])).Trim();
                CoRows.Note(ctx.Item, "MoUpdate Load " + MoApi.FirstLine(err));
                throw MoApi.Refused(err);
            }
            object ext = com.Keep(ComUtil.Call(com.Broker, "GetExtBoEntity", new object[] { "extbo" }));
            if (ext == null || MoUpdateExt.Count(ext) == 0)
            {
                // 未覆盖：extbo 是 out 参数，加载结果若没写回参数实体，改取 GetResult("extbo")（参数的 MomParameter.Value）。
                ext = com.Keep(ComUtil.Call(com.Broker, "GetResult", new object[] { "extbo" }));
            }
            if (ext == null || MoUpdateExt.Count(ext) == 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有返回加载的生产订单");
            }
            return ext;
        }

        // InvokeApi 返回 false：IPC 错误和 U8 拒绝（MoRefused）都交给调用方回读，不直接报 409（传输层失败时 U8 可能已提交）。
        static Exception Settle(WorkContext ctx, bool ok, string err)
        {
            if (err.Length > 0)
            {
                CoRows.Note(ctx.Item, MoApi.FirstLine(err));
            }
            if (ok)
            {
                return null;
            }
            if (MoApi.IsIpc(err))
            {
                return new InvalidOperationException(err);
            }
            return new MoRefused(err);
        }
    }

    // 扩展实体（ExtBO）的字段名只写在这里。行、子件字段带 D 前缀；自定义项是 DDefine_22…（APISetDataHelper，前缀 D）。
    internal static class MoExtNames
    {
        public const string Details = "Mom_OrderDetail";
        public const string Allocates = "Mom_MoAllocate";
        public const string SortSeq = "DSortSeq";
        public const string InvCode = "DInvCode";
        public const string Qty = "DQty";
        public const string MrpQty = "DMrpQty";
        public const string AuxQty = "DAuxQty";
        public const string DueDate = "DDueDate";
        public const string Remark = "DRemark";
        public const string ProductType = "DProductType";
        public const string BaseQtyN = "DBaseQtyN";
        public const string BaseQtyD = "DBaseQtyD";
        public const string AuxBaseQtyN = "DAuxBaseQtyN";
        public const string ChangeRate = "DChangeRate";
        public const string WhCode = "DWhCode";
        public const string InvFree = "DInvFree_";

        // define22 → DDefine_22
        public static string Define(string key)
        {
            return "DDefine_" + key.Substring("define".Length);
        }
    }

    // 在加载出的实体上改值。每个子对象都登记到 com，调用结束统一 ReleaseOne。
    internal static class MoUpdateExt
    {
        public static void Apply(MoUpdCom com, object ext, MoPlan plan)
        {
            object head = com.Keep(ComUtil.GetAt(ext, "Item", new object[] { 0 }));
            object body = com.Keep(ComUtil.Call(head, "GetSubEntity", new object[] { MoExtNames.Details }));
            int n = Count(body);
            if (n != plan.Lines.Count)
            {
                throw Mismatch("U8 加载的生产订单行数与库里不一致");
            }
            for (int i = 0; i < n; i++)
            {
                object item = com.Keep(ComUtil.GetAt(body, "Item", new object[] { i }));
                int seq = ToInt(ComUtil.Call(item, "GetValue", new object[] { MoExtNames.SortSeq }));
                MoTarget target;
                if (!plan.BySeq.TryGetValue(seq, out target))
                {
                    throw Mismatch("U8 加载的生产订单行号与库里不一致");
                }
                Line(item, target);
                object subs = com.Keep(ComUtil.Call(item, "GetSubEntity", new object[] { MoExtNames.Allocates }));
                Allocates(com, subs, target, plan);
            }
        }

        static void Line(object item, MoTarget t)
        {
            if (!t.Touched)
            {
                return;
            }
            if (t.QtyChanged)
            {
                LineQty(item, t);
            }
            // Load 已把 DStartDate / DDueDate 填成原值，U8 总会重写日期；开工日期不开放修改（MoUpdateReq.NoStart），完工日期改了才覆盖。
            if (t.Due != t.Row.Due)
            {
                Put(item, MoExtNames.DueDate, Day(t.Due));
            }
            if (t.Remark.Length > 0 && t.Remark != t.Row.Remark)
            {
                Put(item, MoExtNames.Remark, t.Remark);
            }
            LineDefines(item, t);
        }

        static void LineQty(object item, MoTarget t)
        {
            Put(item, MoExtNames.Qty, t.Qty);
            // 普通订单 U8 改数量时同步 MrpQty，但 Update 随后会用 Load 带回的旧 DMrpQty 覆盖，所以一起改（未经实测）。
            if (t.Row.MoClass == "1" && t.Row.MrpQty == t.Row.Qty)
            {
                Put(item, MoExtNames.MrpQty, t.Qty);
            }
            if (t.AuxQty >= 0m)
            {
                Put(item, MoExtNames.AuxQty, t.AuxQty);
            }
        }

        static void LineDefines(object item, MoTarget t)
        {
            foreach (KeyValuePair<string, string> kv in t.Defines)
            {
                string old;
                t.Row.Defines.TryGetValue(kv.Key, out old);
                if ((kv.Value ?? "").Length > 0 && kv.Value != old)
                {
                    Put(item, MoExtNames.Define(kv.Key), kv.Value);
                }
            }
        }

        // 加载出的子件按（行号、存货）与快照对应；对不上或数量不同就不调用 Update。
        static void Allocates(MoUpdCom com, object subs, MoTarget line, MoPlan plan)
        {
            List<MoAllocTarget> targets = plan.Allocs[line.Row.MoDId];
            int n = Count(subs);
            if (n != targets.Count)
            {
                throw Mismatch("U8 加载的第 " + line.Row.SortSeq.ToString(CultureInfo.InvariantCulture) + " 行子件数与库里不一致");
            }
            bool[] used = new bool[targets.Count];
            for (int i = 0; i < n; i++)
            {
                object item = com.Keep(ComUtil.GetAt(subs, "Item", new object[] { i }));
                MoAllocTarget t = Match(targets, used, LoadedKey(item, plan.QtyDigits), plan.QtyDigits);
                if (t == null)
                {
                    throw Mismatch("U8 加载的第 " + line.Row.SortSeq.ToString(CultureInfo.InvariantCulture) + " 行子件与库里不一致");
                }
                Allocate(item, t, line.QtyChanged);
            }
        }

        // 加载出的子件的对应键，与 MoAllocRow.KeyAt 同形（用量按同一小数位）。未覆盖：Load 的数量精度是否就是 iStrsQuanDecDgt。
        static string LoadedKey(object item, int digits)
        {
            string[] free = new string[10];
            for (int i = 0; i < 10; i++)
            {
                free[i] = Text(item, MoExtNames.InvFree + (i + 1).ToString(CultureInfo.InvariantCulture));
            }
            return MoAllocRow.MakeKey(ToInt(Value(item, MoExtNames.SortSeq)), Text(item, MoExtNames.InvCode),
                new decimal[] { DecOf(item, MoExtNames.BaseQtyN), DecOf(item, MoExtNames.BaseQtyD) },
                Text(item, MoExtNames.WhCode), free, digits);
        }

        static object Value(object item, string name)
        {
            return ComUtil.Call(item, "GetValue", new object[] { name });
        }

        static string Text(object item, string name)
        {
            return Values.Text(Value(item, name));
        }

        // 数值按不变区域转文本再解析，不受线程区域的小数点影响。
        static decimal DecOf(object item, string name)
        {
            object v = Value(item, name);
            return v == null || v is DBNull ? 0m : MoUpdateSql.Dec(Convert.ToString(v, CultureInfo.InvariantCulture));
        }

        static MoAllocTarget Match(List<MoAllocTarget> targets, bool[] used, string key, int digits)
        {
            for (int i = 0; i < targets.Count; i++)
            {
                if (!used[i] && targets[i].Row.KeyAt(digits) == key)
                {
                    used[i] = true;
                    return targets[i];
                }
            }
            return null;
        }

        // 用量和数量总是按快照原值写回（Load 按数量精度舍入过）；改了数量的行写重算值。
        static void Allocate(object item, MoAllocTarget t, bool scale)
        {
            MoAllocRow a = t.Row;
            Put(item, MoExtNames.BaseQtyN, a.BaseN);
            Put(item, MoExtNames.BaseQtyD, a.BaseD);
            if (a.AuxUnit.Length > 0 && a.AuxBaseN != 0m)
            {
                Put(item, MoExtNames.AuxBaseQtyN, a.AuxBaseN);
            }
            // Load 把子件换算率按件数小数位舍入（__GetAuxValue），写回快照原值。
            if (a.AuxUnit.Length > 0 && a.ChangeRate != 0m)
            {
                Put(item, MoExtNames.ChangeRate, a.ChangeRate);
            }
            Put(item, MoExtNames.Qty, scale ? t.Qty : a.Qty);
            if (scale && t.AuxQty >= 0m)
            {
                Put(item, MoExtNames.AuxQty, t.AuxQty);
            }
            else if (!scale && a.AuxQty.Length > 0)
            {
                Put(item, MoExtNames.AuxQty, MoUpdateSql.Dec(a.AuxQty));
            }
            AllocText(item, a);
        }

        static void AllocText(object item, MoAllocRow a)
        {
            if (a.Remark.Length > 0)
            {
                Put(item, MoExtNames.Remark, a.Remark);
            }
            if (a.ProductType.Length > 0)
            {
                Put(item, MoExtNames.ProductType, MoUpdateSql.Int(a.ProductType));
            }
        }

        static void Put(object item, string name, object value)
        {
            ComUtil.Call(item, "SetValue", new object[] { name, value });
        }

        static DateTime Day(string text)
        {
            return DateTime.ParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        internal static int Count(object entity)
        {
            return ToInt(ComUtil.Get(entity, "ItemCount"));
        }

        static int ToInt(object value)
        {
            return MoUpdateSql.Int(Values.Text(value));
        }

        static BridgeException Mismatch(string text)
        {
            return new BridgeException(409, "state_mismatch", text + "，未修改");
        }
    }

    // 本次调用建的 COM 对象：实体的子对象只 ReleaseOne，自己 Create 的 Env / Broker 在 Disconnect 之后 Final。
    internal sealed class MoUpdCom
    {
        public object Env;
        public object Broker;
        public bool Connected;
        public bool Invoked;
        readonly List<object> _items = new List<object>();
        readonly List<object> _retired = new List<object>();

        public object Keep(object item)
        {
            _items.Add(item);
            return item;
        }

        // Load 用过的 Env / Broker（已 Disconnect）移到待释放列表，最后与实体子对象一起释放。
        public void Retire()
        {
            _retired.Add(Broker);
            _retired.Add(Env);
            Broker = null;
            Env = null;
        }

        public void Release()
        {
            for (int i = _items.Count - 1; i >= 0; i--)
            {
                ComUtil.ReleaseOne(_items[i]);
            }
            MoApi.Disconnect(Broker, Connected);
            ComUtil.Final(Broker);
            ComUtil.Final(Env);
            for (int i = 0; i < _retired.Count; i++)
            {
                ComUtil.Final(_retired[i]);
            }
        }
    }

    // MOrderUpdate 的 InvokeApi 返回 false 且不是 IPC 错误：消息是 U8 原文，由 MoUpdate 回读后决定是否按 409 报。
    internal sealed class MoRefused : Exception
    {
        public MoRefused(string message)
            : base(message)
        {
        }
    }
}
