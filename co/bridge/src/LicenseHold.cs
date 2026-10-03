using System;
using System.Collections.Generic;

namespace U8Co
{
    // 许可点数保护（写入策略第 8 步，只在桥上），在 U8Session.Open 调 clsLogin 之前：
    // 1) license.holdWritesWhen 含当前总体许可状态（LicenseView：ok / near / full / unknown）时，写路由（含预演）的新登录
    //    503 u8_license_full，读路由照常登录；复用缓存登录的写任务在出队后由 RequireWrite 同样拦下；
    // 2) license.maxConcurrentLogins：本进程同时在用的 U8 登录（开着 loginReuse 时含缓存着的登录）已到上限时，
    //    任何新登录 503 u8_license_hold。复用缓存里的登录不是新登录，不受限。
    // 在用登录数始终在记（Enter 成功加一，登录失败或 U8Session.Dispose 减一）；写入策略关闭时只记数、不拦。
    // 到上限时先在本线程关掉本线程闲置的缓存登录（LoginCache.DropIdle，同一 STA）再判一次；仍不够就请其他写线程
    // 在下一次空闲清理时关掉此前缓存的登录（LoginCache.RequestEvict），本次照常 503，调用方稍后重试。
    // 同一请求内的嵌套登录（库存期初按启用日期另登录一次，StockOpening.WithLogin）算在父登录的名额里：
    // 在 BeginNested / EndNested 之间的 Enter 不判、不记数，返回 false。
    internal static class LicenseHold
    {
        public const string HoldCode = "u8_license_hold";
        public const string HoldMessage = "接口登录已达上限，请稍后重试";

        static readonly object Gate = new object();
        static int _live;
        [ThreadStatic]
        static int _nested;

        // 自检用：代替写入策略的许可设置和当前总体许可状态。用完设回 null。
        internal static Func<HoldLimits> LimitsHook;
        internal static Func<string> StateHook;

        public static int Live
        {
            get
            {
                lock (Gate)
                {
                    return _live;
                }
            }
        }

        // 放行就占一个在用登录名额并返回 true，之后登录失败必须调 Leave；嵌套登录返回 false，不占名额、不用 Leave。
        public static bool Enter(U8Open ask)
        {
            if (_nested > 0)
            {
                return false;
            }
            HoldLimits lim = LimitsOf();
            bool write = ask != null && ask.Write;
            string state = lim != null && write && lim.HoldWhen.Length > 0 ? CurrentState() : null;
            BridgeException refuse = TryTake(write, lim, state);
            if (refuse != null && refuse.Code == HoldCode)
            {
                // 不在锁里关登录：关闭走 U8Session.Dispose → Leave。
                LoginCache.DropIdle();
                refuse = TryTake(write, lim, state);
                if (refuse != null)
                {
                    LoginCache.RequestEvict();
                }
            }
            if (refuse != null)
            {
                throw refuse;
            }
            return true;
        }

        static BridgeException TryTake(bool write, HoldLimits lim, string state)
        {
            lock (Gate)
            {
                BridgeException refuse = Decide(write, lim, state, _live);
                if (refuse == null)
                {
                    _live++;
                }
                return refuse;
            }
        }

        // 本线程进入 / 离开同一请求内的嵌套登录，成对调用（finally 里 EndNested）。
        public static void BeginNested()
        {
            _nested++;
        }

        public static void EndNested()
        {
            if (_nested > 0)
            {
                _nested--;
            }
        }

        public static void Leave()
        {
            lock (Gate)
            {
                if (_live > 0)
                {
                    _live--;
                }
            }
        }

        // 纯判断，自检直接调用。lim 为 null 表示策略关闭或没有许可设置：一律放行。
        internal static BridgeException Decide(bool write, HoldLimits lim, string state, int live)
        {
            if (lim == null)
            {
                return null;
            }
            if (write)
            {
                BridgeException held = Held(lim, state);
                if (held != null)
                {
                    return held;
                }
            }
            if (lim.MaxLogins > 0 && live >= lim.MaxLogins)
            {
                return new BridgeException(503, HoldCode, HoldMessage);
            }
            return null;
        }

        // 写任务出队后、取登录之前（WorkRun.RequireAccount）：当前总体许可状态在 holdWritesWhen 里时 503 u8_license_full。
        // 复用缓存里的登录不经 Enter，靠这里拦下；新登录在 Enter 里按登录时的状态再判一次。读路由、策略关闭时不判。
        public static void RequireWrite(WorkItem item)
        {
            if (!WriteGate.IsWrite(item))
            {
                return;
            }
            HoldLimits lim = LimitsOf();
            if (lim == null || lim.HoldWhen.Length == 0)
            {
                return;
            }
            BridgeException held = Held(lim, CurrentState());
            if (held != null)
            {
                throw held;
            }
        }

        static BridgeException Held(HoldLimits lim, string state)
        {
            if (state == null || Array.IndexOf(lim.HoldWhen, state) < 0)
            {
                return null;
            }
            string how = state == "full" ? "已满" : "紧张";
            return new BridgeException(503, LicenseRetry.FullCode,
                "U8 许可点数" + how + "（" + state + "），接口暂停写入，请稍后重试");
        }

        // 自检用：把在用登录数清零。
        internal static void ResetForTest()
        {
            lock (Gate)
            {
                _live = 0;
            }
        }

        static HoldLimits LimitsOf()
        {
            Func<HoldLimits> hook = LimitsHook;
            if (hook != null)
            {
                return hook();
            }
            if (WritePolicy.State == "off")
            {
                return null;
            }
            return WriteLimits.License();
        }

        static string CurrentState()
        {
            Func<string> hook = StateHook;
            if (hook != null)
            {
                return hook();
            }
            bool sampled;
            Dictionary<string, LicenseSub> subs = LicenseState.Snapshot(out sampled);
            return LicenseView.Overall(subs, sampled, LicenseState.WarnFree, DateTime.UtcNow.Ticks);
        }
    }

    // 写入策略里许可保护的两项。MaxLogins 为 0 表示不限；HoldWhen 不为 null。
    internal sealed class HoldLimits
    {
        public int MaxLogins;
        public string[] HoldWhen = new string[0];
    }

    // 从写入策略（WritePolicy，W1）取本步骤要的设置，对 WritePolicySnapshot 的依赖只在这里。
    internal static class WriteLimits
    {
        // {每分钟, 每天}，0 为不限。策略不可用时不限（第 1 步已经拒绝写入）。
        public static int[] Quota(string acc)
        {
            WritePolicySnapshot snap = WritePolicy.Current;
            if (snap == null)
            {
                return new int[] { 0, 0 };
            }
            WriteQuotaLimits q = snap.Quota(acc);
            if (q == null)
            {
                return new int[] { 0, 0 };
            }
            return new int[] { Math.Max(0, q.PerMinute), Math.Max(0, q.PerDay) };
        }

        // 没有 license 一节时返回 null（不拦）。
        public static HoldLimits License()
        {
            WritePolicySnapshot snap = WritePolicy.Current;
            if (snap == null || snap.License == null)
            {
                return null;
            }
            HoldLimits lim = new HoldLimits();
            lim.MaxLogins = Math.Max(0, snap.License.MaxConcurrentLogins);
            if (snap.License.HoldWritesWhen != null)
            {
                lim.HoldWhen = snap.License.HoldWritesWhen;
            }
            return lim;
        }
    }
}
