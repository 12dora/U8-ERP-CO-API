using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 账套写入限额（写入策略第 7 步，只在桥上）：按账套数已提交的写入（非预演、2xx；504 outcome_unknown 可能已在 U8
    // 提交，从严也计数）。
    // 分钟额度是滑动 60 秒窗口，只在内存；日额度按本机日历日计，存在 auditLog 目录的 write-quota.json
    // （临时文件 + 原子改名），重启后当天的数接着算。
    // 出队后（WorkRun.RequireAccount，在策略检查之后）先占一个名额，任务结束时（WorkRun.Run 的 finally）
    // 成功（含结果未知）的转为已提交、其余退回，同账套并行写入也不会超额。预演只检查、不占名额、不计数。
    // 占名额与结算在同一个工作线程上（[ThreadStatic]）。写入策略关闭（State 为 off）时什么都不做。
    internal static class WriteQuota
    {
        public const string Code = "write_quota";
        public const string Message = "已超过账套写入限额";
        public const string FileName = "write-quota.json";

        static readonly object Gate = new object();
        static QuotaBook _book;
        [ThreadStatic]
        static string _held;

        // 自检用：代替本机时间、写入策略的额度（{每分钟, 每天}）和策略开关。用完设回 null。
        internal static Func<DateTime> Now;
        internal static Func<string, int[]> Limits;
        internal static Func<bool> Active;

        // 超额抛 429 write_quota，detail.retry_after_seconds 是建议等待秒数。桥本身不发 HTTP 头 Retry-After，
        // 由 API（co_bridge）按 detail 补；直接调用桥的客户端读 detail。
        public static void Require(WorkItem item)
        {
            if (item == null || _held != null || !On() || !WriteGate.IsWrite(item))
            {
                return;
            }
            string acc = item.Acc ?? "";
            int[] lim = LimitsOf(acc);
            DateTime now = LocalNow();
            lock (Gate)
            {
                QuotaBook book = Book(item.Config);
                int wait = book.WaitSeconds(acc, lim[0], lim[1], now);
                if (wait > 0)
                {
                    throw Refuse(wait);
                }
                // 预演以分类结果为准（自动核销的 dry_run 不经 DryRunReq）。
                bool dry = item.DryRun || (item.WriteInfo != null && item.WriteInfo.DryRun);
                if (!dry)
                {
                    book.Reserve(acc);
                    _held = acc;
                }
            }
        }

        // 任务结束时调用，不抛异常。本线程占过名额才生效：2xx 和 504 outcome_unknown 记一次已提交并落盘，其余只退回名额。
        public static void Settle(WorkItem item)
        {
            string acc = _held;
            if (acc == null)
            {
                return;
            }
            _held = null;
            try
            {
                bool ok = Committed(item == null ? null : item.Result);
                lock (Gate)
                {
                    QuotaBook book = _book;
                    if (book == null)
                    {
                        return;
                    }
                    book.Release(acc);
                    if (ok)
                    {
                        book.Commit(acc, LocalNow());
                        Save(book);
                    }
                }
            }
            catch (Exception)
            {
                // 计数失败不能影响已经返回的结果。
            }
        }

        // 2xx，或 504 outcome_unknown（已提交、回读失败，可能已写进 U8）：从严计入额度。
        internal static bool Committed(ApiResult result)
        {
            if (result == null)
            {
                return false;
            }
            if (result.Status >= 200 && result.Status < 300)
            {
                return true;
            }
            return result.Status == 504 && result.Code == "outcome_unknown";
        }

        public static BridgeException Refuse(int waitSeconds)
        {
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["retry_after_seconds"] = waitSeconds;
            return new BridgeException(429, Code, Message).WithDetail(detail);
        }

        // 自检用：丢掉内存里的计数和本线程的名额，下次按文件重新加载。
        internal static void ResetForTest()
        {
            lock (Gate)
            {
                _book = null;
            }
            _held = null;
        }

        internal static int DayCount(string acc)
        {
            lock (Gate)
            {
                return _book == null ? 0 : _book.DayOf(acc, LocalNow());
            }
        }

        static bool On()
        {
            Func<bool> hook = Active;
            if (hook != null)
            {
                return hook();
            }
            return WritePolicy.State != "off";
        }

        static int[] LimitsOf(string acc)
        {
            Func<string, int[]> hook = Limits;
            int[] lim = hook != null ? hook(acc) : WriteLimits.Quota(acc);
            if (lim == null || lim.Length < 2)
            {
                return new int[] { 0, 0 };
            }
            return lim;
        }

        static DateTime LocalNow()
        {
            Func<DateTime> hook = Now;
            DateTime t = hook != null ? hook() : DateTime.Now;
            return t.Kind == DateTimeKind.Utc ? t.ToLocalTime() : t;
        }

        static QuotaBook Book(BridgeConfig cfg)
        {
            if (_book == null)
            {
                string dir = cfg == null ? null : cfg.AuditLog;
                string file = string.IsNullOrEmpty(dir) ? null : Path.Combine(dir, FileName);
                _book = QuotaBook.Load(file);
            }
            return _book;
        }

        static void Save(QuotaBook book)
        {
            if (book.File == null)
            {
                return;
            }
            try
            {
                IdemFile.Replace(book.File, Encoding.UTF8.GetBytes(book.Render()));
            }
            catch (Exception ex)
            {
                // 落盘失败只影响重启后的日计数；内存里照常计。
                Dictionary<string, object> fields = new Dictionary<string, object>();
                fields["error"] = ex.GetType().Name;
                AuditEvent.Write("write_quota_save_failed", fields);
            }
        }
    }

    // 计数本身（不加锁，由 WriteQuota 的锁保护）。日计数只保留当天，换日时清零。
    internal sealed class QuotaBook
    {
        const int PendingOnlyWaitSeconds = 5;

        public string File;
        string _date = "";
        readonly Dictionary<string, int> _day = new Dictionary<string, int>(StringComparer.Ordinal);
        readonly Dictionary<string, int> _pending = new Dictionary<string, int>(StringComparer.Ordinal);
        readonly Dictionary<string, List<long>> _minute = new Dictionary<string, List<long>>(StringComparer.Ordinal);

        // 0 表示可以写；否则是超额时建议等待的秒数（至少 1）。在途（已占名额未结算）的也算进去。
        public int WaitSeconds(string acc, int perMinute, int perDay, DateTime now)
        {
            Roll(now);
            int pending = Get(_pending, acc);
            if (perDay > 0 && Get(_day, acc) + pending >= perDay)
            {
                return ToMidnight(now);
            }
            if (perMinute > 0)
            {
                List<long> hits = Window(acc, now);
                if (hits.Count + pending >= perMinute)
                {
                    return MinuteWait(hits, now);
                }
            }
            return 0;
        }

        public void Reserve(string acc)
        {
            _pending[acc] = Get(_pending, acc) + 1;
        }

        public void Release(string acc)
        {
            int left = Get(_pending, acc) - 1;
            if (left > 0)
            {
                _pending[acc] = left;
            }
            else
            {
                _pending.Remove(acc);
            }
        }

        public void Commit(string acc, DateTime now)
        {
            Roll(now);
            _day[acc] = Get(_day, acc) + 1;
            Window(acc, now).Add(now.Ticks);
        }

        public int DayOf(string acc, DateTime now)
        {
            Roll(now);
            return Get(_day, acc);
        }

        public string Render()
        {
            Dictionary<string, object> counts = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, int> pair in _day)
            {
                counts[pair.Key] = pair.Value;
            }
            Dictionary<string, object> root = new Dictionary<string, object>();
            root["date"] = _date;
            root["counts"] = counts;
            return new JavaScriptSerializer().Serialize(root);
        }

        // 文件不存在、读不了或格式不对都从零开始（坏文件记一条审计事件）；不是当天的数在第一次使用时清掉。
        public static QuotaBook Load(string file)
        {
            QuotaBook book = new QuotaBook();
            book.File = file;
            if (file == null || !System.IO.File.Exists(file))
            {
                return book;
            }
            try
            {
                book.Parse(System.IO.File.ReadAllText(file, Encoding.UTF8));
            }
            catch (Exception ex)
            {
                book._date = "";
                book._day.Clear();
                Dictionary<string, object> fields = new Dictionary<string, object>();
                fields["error"] = ex.GetType().Name;
                AuditEvent.Write("write_quota_load_failed", fields);
            }
            return book;
        }

        internal void Parse(string text)
        {
            Dictionary<string, object> root = new JavaScriptSerializer().DeserializeObject(text) as Dictionary<string, object>;
            if (root == null || !(root.ContainsKey("date") && root["date"] is string))
            {
                throw new FormatException("write-quota");
            }
            Dictionary<string, object> counts = root.ContainsKey("counts") ? root["counts"] as Dictionary<string, object> : null;
            if (counts == null)
            {
                throw new FormatException("write-quota");
            }
            foreach (KeyValuePair<string, object> pair in counts)
            {
                if (!(pair.Value is int) || (int)pair.Value < 0)
                {
                    throw new FormatException("write-quota");
                }
                _day[pair.Key] = (int)pair.Value;
            }
            _date = (string)root["date"];
        }

        void Roll(DateTime now)
        {
            string today = now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (_date != today)
            {
                _date = today;
                _day.Clear();
            }
        }

        List<long> Window(string acc, DateTime now)
        {
            List<long> hits;
            if (!_minute.TryGetValue(acc, out hits))
            {
                hits = new List<long>();
                _minute[acc] = hits;
            }
            long cutoff = now.Ticks - TimeSpan.TicksPerMinute;
            int stale = 0;
            while (stale < hits.Count && hits[stale] <= cutoff)
            {
                stale++;
            }
            if (stale > 0)
            {
                hits.RemoveRange(0, stale);
            }
            return hits;
        }

        // 窗口里最早的一次滑出去之后就能写；窗口是空的（名额全被在途写入占着）时给一个短等待。
        static int MinuteWait(List<long> hits, DateTime now)
        {
            if (hits.Count == 0)
            {
                return PendingOnlyWaitSeconds;
            }
            long ticks = hits[0] + TimeSpan.TicksPerMinute - now.Ticks;
            return Seconds(ticks);
        }

        static int ToMidnight(DateTime now)
        {
            return Seconds(now.Date.AddDays(1).Ticks - now.Ticks);
        }

        static int Seconds(long ticks)
        {
            long s = (ticks + TimeSpan.TicksPerSecond - 1) / TimeSpan.TicksPerSecond;
            return s < 1 ? 1 : (int)s;
        }

        static int Get(Dictionary<string, int> map, string acc)
        {
            int n;
            return map.TryGetValue(acc, out n) ? n : 0;
        }
    }
}
