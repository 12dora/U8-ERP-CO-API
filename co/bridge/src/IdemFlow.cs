using System;
using System.Collections.Generic;
using System.Net;
using System.Threading;

namespace U8Co
{
    // 带 idempotency_key 的新建类请求。在 HTTP 线程上、入队之前查记录，不碰 STA：
    // 同键已完成则先校验这一次的登录（IdemLogin），再原样重放；同键还在跑则等第一个的结果，绝不再发第二次 COM 调用；
    // 第一个没有真正执行（排队问题、登录失败、执行前的拒绝）就不占用键，等待者接着自己执行。
    // 结果怎样落盘、哪些不占用键，见 IdemSettle.cs。
    internal static partial class IdemFlow
    {
        const string ReplayHeader = "Idempotent-Replayed";
        // 剩余时间不够再排一次队时直接让调用方重试，不再抢键。
        const int MinLeadMs = 1000;

        static readonly object Gate = new object();
        static readonly Dictionary<string, Slot> Slots = new Dictionary<string, Slot>(StringComparer.Ordinal);

        sealed class Claimed
        {
            public Slot Own;
            public Slot Other;
            public IdemRecord Done;
        }

        public static void Run(HttpListenerContext ctx, AuditDraft draft, WorkItem item)
        {
            IdemAsk ask = item.Idem;
            string id = ask.StoreId(item.Acc, item.Path);
            long deadline = DateTime.UtcNow.Ticks + (long)item.WaitMs * TimeSpan.TicksPerMillisecond;
            IdemStore.PruneSoon();
            CoRows.Note(item, "幂等键 " + ask.Key);
            draft.Detail = item.Detail;
            while (true)
            {
                if (RemainingMs(deadline) < MinLeadMs)
                {
                    throw new BridgeException(503, "busy_timeout", "排队超时，可以重试");
                }
                Claimed got = Claim(id, ask, item);
                if (got.Done != null)
                {
                    int status = got.Done.Status;
                    string body = ReplayBody(got.Done, ref status);
                    Replay(ctx, draft, item, deadline, status, body);
                    return;
                }
                if (got.Other != null)
                {
                    if (!got.Other.Wait(RemainingMs(deadline)))
                    {
                        throw new BridgeException(504, "outcome_unknown", "同一幂等键的请求仍在执行，结果未知");
                    }
                    if (got.Other.Released)
                    {
                        continue;
                    }
                    Replay(ctx, draft, item, deadline, got.Other.Status, got.Other.Response);
                    return;
                }
                Lead(ctx, draft, item, id, got.Own, RemainingMs(deadline));
                return;
            }
        }

        internal static int RemainingMs(long deadlineTicks)
        {
            long left = (deadlineTicks - DateTime.UtcNow.Ticks) / TimeSpan.TicksPerMillisecond;
            if (left <= 0)
            {
                return 0;
            }
            return left > int.MaxValue ? int.MaxValue : (int)left;
        }

        static Claimed Claim(string id, IdemAsk ask, WorkItem item)
        {
            Claimed got = new Claimed();
            lock (Gate)
            {
                Slot slot;
                if (Slots.TryGetValue(id, out slot))
                {
                    RequireSame(slot.BodySha, ask.BodySha);
                    got.Other = slot;
                    return got;
                }
                IdemRecord rec = Load(id);
                if (rec != null)
                {
                    RequireSame(rec.BodySha, ask.BodySha);
                    got.Done = rec.State == IdemStore.InFlight ? Orphan(id, rec) : rec;
                    return got;
                }
                IdemRecord seed = Fresh(ask, item);
                Create(id, seed);
                got.Own = new Slot(ask.BodySha, seed);
                Slots[id] = got.Own;
            }
            return got;
        }

        static IdemRecord Load(string id)
        {
            try
            {
                IdemRecord rec = IdemStore.Read(id);
                // 旧版本存下的 failed（执行前的拒绝）不再占用键，与过期同样处理。
                bool stale = rec != null && (rec.State == IdemStore.Failed || IdemStore.Expired(rec, DateTime.UtcNow));
                if (stale)
                {
                    IdemStore.Delete(id);
                    return null;
                }
                return rec;
            }
            catch (Exception)
            {
                throw StoreDown();
            }
        }

        // 第一次写 in_flight：CreateNew + WriteThrough，写不进去就不执行。
        static void Create(string id, IdemRecord rec)
        {
            try
            {
                IdemStore.Create(id, rec);
            }
            catch (Exception)
            {
                throw StoreDown();
            }
        }

        static BridgeException StoreDown()
        {
            return new BridgeException(503, "store_unavailable", "幂等记录无法读写，请稍后重试");
        }

        // 记录内容坏了（摘要为空）时不比对，按结果未知处理。
        static void RequireSame(string stored, string asked)
        {
            if (stored != null && stored.Length > 0 && !string.Equals(stored, asked, StringComparison.Ordinal))
            {
                throw new BridgeException(409, "idempotency_mismatch", "该幂等键已用于内容不同的请求");
            }
        }

        static IdemRecord Fresh(IdemAsk ask, WorkItem item)
        {
            IdemRecord rec = new IdemRecord();
            rec.State = IdemStore.InFlight;
            rec.BodySha = ask.BodySha;
            rec.Status = 0;
            rec.Response = "";
            rec.CreatedTicks = DateTime.UtcNow.Ticks;
            rec.Route = item.Path;
            rec.Acc = item.Acc;
            rec.Caller = ask.Caller;
            rec.Operator = item.Operator ?? "";
            return rec;
        }

        // 磁盘上有 in_flight、本进程却没有在跑：上次执行中服务中断。改记为结果未知，不再自动重做。
        static IdemRecord Orphan(string id, IdemRecord rec)
        {
            rec.State = IdemStore.Unknown;
            rec.Status = 504;
            rec.Response = ErrorJson("outcome_unknown", "上次请求执行中服务中断，结果未知；请先用读取或列表核对");
            try
            {
                IdemStore.Write(id, rec);
            }
            catch (Exception)
            {
                // 改写失败时磁盘上仍是 in_flight，下次照样按结果未知处理。
            }
            return rec;
        }

        // 记录里只有 ok 和结果未知两种终态；内容缺失时按结果未知回 504。
        static string ReplayBody(IdemRecord rec, ref int status)
        {
            if (rec.Response == null || rec.Response.Length == 0 || status < 200)
            {
                status = 504;
                return ErrorJson("outcome_unknown", "该幂等键的上次请求结果未知；请先用读取或列表核对");
            }
            return rec.Response;
        }

        static void Lead(HttpListenerContext ctx, AuditDraft draft, WorkItem item, string id, Slot own, int waitMs)
        {
            item.Done = new ManualResetEvent(false);
            bool queued = false;
            bool late = false;
            try
            {
                // 先等过同键的幂等请求时，剩余时间只有 waitMs：截止时间按它算，免得写入晚开工、提交时调用方已 504。
                item.DeadlineUtcTicks = DateTime.UtcNow.Ticks + (long)waitMs * TimeSpan.TicksPerMillisecond;
                int got = StaWorker.TryEnqueue(item);
                if (got != StaWorker.Enqueued)
                {
                    BridgeException full = HttpServer.QueueError(got);
                    Settle(id, own, ApiResult.From(full));
                    throw full;
                }
                queued = true;
                if (!item.Done.WaitOne(waitMs))
                {
                    BridgeException wait = HttpServer.WaitError(item, draft);
                    late = wait.Status == 504;
                    if (late)
                    {
                        Later(item, id, own);
                    }
                    else
                    {
                        Settle(id, own, ApiResult.From(wait));
                    }
                    throw wait;
                }
                draft.Audited = true;
                Settle(id, own, item.Result);
                Json.WriteRaw(ctx.Response, own.Status, own.Response);
                draft.Responded = true;
            }
            finally
            {
                if (!queued)
                {
                    // 入队本身抛异常时兜底：没有入队，键不占用。
                    Settle(id, own, ApiResult.From(new BridgeException(503, "busy", "服务忙，请稍后重试")));
                }
                if (!late)
                {
                    CloseDone(item);
                }
            }
        }

        // 第一个请求超过 HTTP 等待时限仍在执行：已回 504，任务跑完后再把真实结果记下，之后同键重放真实结果。
        // 起不了后台线程时直接记为结果未知，不让这个键一直卡在执行中。
        static void Later(WorkItem item, string id, Slot own)
        {
            try
            {
                Thread thread = new Thread(delegate()
                {
                    try
                    {
                        item.Done.WaitOne();
                        Settle(id, own, item.Result);
                    }
                    catch (Exception)
                    {
                        Settle(id, own, ApiResult.From(new BridgeException(504, "outcome_unknown", "请求已在执行，结果未知")));
                    }
                    finally
                    {
                        CloseDone(item);
                    }
                });
                thread.IsBackground = true;
                thread.Name = "u8co-idem-late";
                thread.Start();
            }
            catch (Exception)
            {
                Settle(id, own, ApiResult.From(new BridgeException(504, "outcome_unknown", "请求已在执行，结果未知")));
            }
        }

        // 重放前先确认这一次的操作员登录有效；登录失败照常返回 422，不给出存下的响应。
        static void Replay(HttpListenerContext ctx, AuditDraft draft, WorkItem item, long deadline, int status, string body)
        {
            try
            {
                IdemLogin.Verify(item, deadline);
            }
            finally
            {
                item.Password = null;
            }
            ctx.Response.AddHeader(ReplayHeader, "true");
            Json.WriteRaw(ctx.Response, status, body);
            draft.Responded = true;
            AuditLog.Write(item.Config, draft, "idempotent_replay", "status " + status, null);
        }

        static void CloseDone(WorkItem item)
        {
            try
            {
                item.Done.Close();
            }
            catch (Exception)
            {
            }
        }
    }
}
