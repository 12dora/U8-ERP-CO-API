using System;
using System.Threading;

namespace U8Co
{
    // U8 登录的暂时性失败：许可点数饱和（ShareString 含「加密点数已饱和」）、Login 成功但 LogState 不是 0。
    // 两种都在 U8Session.Open 里按下面的等待表重试，其他 login_failed（口令错误等）不重试。
    internal static class LicenseRetry
    {
        public const string FullMark = "加密点数已饱和";
        public const string FullCode = "u8_license_full";
        public const string StateMessage = "登录状态不是 0";

        // 第 1 次重试前等 3 秒，第 2 次前等 8 秒。表的长度就是重试次数的上限（licenseRetries 大于 2 也只重试 2 次），
        // 合计等 11 秒。连同各次登录本身，一个请求在登录上总共不超过 BudgetMs：下一次「等待 + 上一次登录用时」会超出就不再重试。
        static readonly int[] WaitMs = new int[] { 3000, 8000 };
        public const long BudgetMs = 20000;

        public static int Count(int configured)
        {
            if (configured <= 0)
            {
                return 0;
            }
            return Math.Min(configured, WaitMs.Length);
        }

        public static bool Transient(BridgeException ex)
        {
            if (ex == null)
            {
                return false;
            }
            if (ex.Code == FullCode)
            {
                return true;
            }
            return ex.Code == "login_failed" && ex.Message == StateMessage;
        }

        // 从 startTicks 起已用的时间，加上第 attempt 次重试前的等待和上一次登录的用时，是否仍在 BudgetMs 之内。
        public static bool Fits(long startTicks, int attempt, long lastMs)
        {
            long usedMs = (DateTime.UtcNow.Ticks - startTicks) / TimeSpan.TicksPerMillisecond;
            int index = Math.Max(0, Math.Min(attempt, WaitMs.Length - 1));
            return usedMs + WaitMs[index] + Math.Max(0, lastMs) <= BudgetMs;
        }

        public static void Wait(int attempt)
        {
            int index = Math.Max(0, Math.Min(attempt, WaitMs.Length - 1));
            Thread.Sleep(WaitMs[index]);
        }
    }
}
