using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Threading;

namespace U8Co
{
    // 许可采样：后台线程每 licenseSampleMinutes 分钟先读点数租约（LicenseHooks.TryReadLeases，实际占用，按产品包算；
    // licenseLeases 为 false 或此版本不含租约读取时不读）；
    // 读不到才回落到旧办法：用最近一次登录的连接串查一次 UA_TaskLog（LicenseSql），
    // 每 6 小时读一次许可总数（LicenseTotals，在单独的 AppDomain 里读；读不到保留上一次的，从没读到过用 licenseLimits）。不为采样登录 U8。
    // 任何异常只进 LicenseState.LastError，不影响请求和看门狗。
    internal static class LicenseSampler
    {
        const int TickMs = 15 * 1000;
        const long TotalsTicks = 6L * TimeSpan.TicksPerHour;
        const int MaxErrorLength = 200;
        // 桥登录 U8 用到的全部子系统，健康检查的 license / license_detail 只看这些（加上 licenseLimits 的键），
        // license_packs 仍列出全部产品包。来源：单据类型的 SubId（SA、PU、ST）和 VerifySub（MO 生产订单、BO 物料清单），
        // 应收应付 AR / AP，总账凭证和报表 GL，质量 QM（QmCo.LoginSub），档案等固定登录 AS。
        // 新增登录子系统要加在这里；自检（LicenseStateSelfTest）核对 Kinds 的子系统都在表里。
        public static readonly string[] LoginModules = new string[] { "SA", "AS", "PU", "ST", "QM", "AR", "AP", "GL", "MO", "BO" };
        const string LeasesOff = "licenseLeases 为 false，不读租约";
        static BridgeConfig _cfg;
        static long _nextSample;
        // 回落到 UA_TaskLog 时还没有人登录过：等到第一次登录后的下一轮（约 15 秒）就采，不等满一个间隔。
        static bool _pendingTaskLog;
        static long _nextTotals;
        static string _noted;
        static string _source;

        public static void Start(BridgeConfig cfg)
        {
            LicenseState.Configure(cfg, Modules());
            if (cfg.LicenseSampleMinutes <= 0)
            {
                return;
            }
            _cfg = cfg;
            Thread thread = new Thread(Loop);
            thread.IsBackground = true;
            thread.Name = "u8co-license";
            // ADO 是套间模型，在自己的 STA 里建，不走跨套间封送。
            thread.SetApartmentState(ApartmentState.STA);
            thread.Start();
        }

        public static List<string> Modules()
        {
            return new List<string>(LoginModules);
        }

        static void Loop()
        {
            while (true)
            {
                Thread.Sleep(TickMs);
                try
                {
                    Tick();
                }
                catch (Exception ex)
                {
                    LicenseState.NoteError(Describe(ex));
                }
                try
                {
                    LicenseView.Transition();
                    NoteErrorOnce();
                }
                catch (Exception)
                {
                }
            }
        }

        static void Tick()
        {
            long now = DateTime.UtcNow.Ticks;
            if (now < _nextSample)
            {
                PendingTaskLog(now);
                return;
            }
            // 本轮读不读得到都等下一个间隔，不每 15 秒调一次本机库。
            _nextSample = now + _cfg.LicenseSampleMinutes * TimeSpan.TicksPerMinute;
            _pendingTaskLog = false;
            // 先读加密服务器的租约（实际占用，不需要登录过）；读不到才回落到 UA_TaskLog。
            if (TryLeases())
            {
                return;
            }
            string conn = LicenseState.ConnString;
            if (conn == null)
            {
                // 还没有人登录过：不为采样登录 U8，等第一次成功登录后的下一轮再采。
                _pendingTaskLog = true;
                return;
            }
            TaskLog(conn, now);
        }

        // 回落时还没有连接串：有了就马上采一次 UA_TaskLog（不重读租约）。
        static void PendingTaskLog(long now)
        {
            if (!_pendingTaskLog)
            {
                return;
            }
            string conn = LicenseState.ConnString;
            if (conn != null && LicenseState.Source == LicenseState.SourceTaskLog)
            {
                _pendingTaskLog = false;
                TaskLog(conn, now);
            }
        }

        // 回落：UA_TaskLog 的工作站数（下限）加 LicenseTotals 读到的总数。
        static void TaskLog(string conn, long now)
        {
            object db = LicenseSql.Open(conn);
            try
            {
                LicenseState.ApplyUsed(LicenseSql.StationCounts(db));
                LicenseState.NoteError(null);
                if (now >= _nextTotals)
                {
                    // 读失败、结果为空或超时都返回 null：保留上一次的总数，6 小时后再读。
                    _nextTotals = now + TotalsTicks;
                    Dictionary<string, int> totals = LicenseTotals.Read(db, _cfg);
                    if (totals != null)
                    {
                        LicenseState.ApplyLimits(totals);
                    }
                }
            }
            finally
            {
                AdoXml.Close(db);
            }
        }

        static bool TryLeases()
        {
            if (!_cfg.LicenseLeases)
            {
                LicenseState.UseTaskLog();
                NoteSource(LicenseState.SourceTaskLog, LeasesOff, -1);
                return false;
            }
            LicenseLeaseSnapshot snap;
            string reason;
            if (LicenseHooks.TryReadLeases(_cfg, out snap, out reason))
            {
                try
                {
                    LicenseState.ApplyLeases(snap);
                    LicenseState.NoteError(null);
                    NoteSource(LicenseState.SourceLeases, null, snap.BridgeRows);
                    return true;
                }
                catch (Exception ex)
                {
                    reason = "应用租约失败：" + ex.GetType().Name;
                }
            }
            LicenseState.UseTaskLog();
            NoteSource(LicenseState.SourceTaskLog, reason, -1);
            return false;
        }

        // 来源变了（含启动后第一次定下来）才写一行 license_source：来源、之前的来源、回落原因（固定文字），
        // 切到租约时带本机占的租约行数。没有工作站、用户和授权方信息。
        static void NoteSource(string source, string reason, int bridgeRows)
        {
            if (source == _source)
            {
                return;
            }
            Dictionary<string, object> evt = new Dictionary<string, object>();
            evt["source"] = source;
            evt["previous"] = _source ?? "none";
            if (reason != null)
            {
                evt["reason"] = reason;
            }
            if (bridgeRows >= 0)
            {
                evt["bridge_rows"] = bridgeRows;
            }
            _source = source;
            AuditEvent.Write("license_source", evt);
        }

        // 采样错误变了才写一行 license_sample_error，恢复后不再写。
        static void NoteErrorOnce()
        {
            string text = LicenseState.LastError;
            if (text == null || text == _noted)
            {
                _noted = text;
                return;
            }
            _noted = text;
            Dictionary<string, object> evt = new Dictionary<string, object>();
            evt["message"] = text;
            AuditEvent.Write("license_sample_error", evt);
        }

        // 异常的类型名和首行消息，去掉口令，截到 200 字。
        public static string Describe(Exception ex)
        {
            Exception cur = ex;
            while (cur is System.Reflection.TargetInvocationException && cur.InnerException != null)
            {
                cur = cur.InnerException;
            }
            if (cur == null)
            {
                return "未知错误";
            }
            string text = cur.GetType().Name + ": " + (cur.Message ?? "");
            int line = text.IndexOfAny(new char[] { '\r', '\n' });
            if (line >= 0)
            {
                text = text.Substring(0, line);
            }
            text = Regex.Replace(text, "(?i)(Password|Pwd|User ID|UID)\\s*=\\s*[^;\\s]*", "$1=***");
            return text.Length > MaxErrorLength ? text.Substring(0, MaxErrorLength) : text;
        }
    }
}
