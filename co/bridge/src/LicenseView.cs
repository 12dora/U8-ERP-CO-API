using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 由 LicenseState 的快照算出各子系统与总体的许可状态，给健康检查和状态变化事件用。
    // ok：正常；near：登记的工作站数 ≥ 总数 − licenseWarnFree；full：15 分钟内 U8 回过「已饱和」，
    // 或数字来自加密服务器租约（Exact，是所在产品包的数）且已用 ≥ 总数；
    // unknown：从没采样过（或租约回落到 UA_TaskLog 后还没采到），也没见过饱和。总体取最差的一个。
    // 只含桥登录的子系统（LicenseSampler.LoginModules 加 licenseLimits 的键）；别的模块、产品包只进 license_packs。
    internal static class LicenseView
    {
        public const string Unknown = "unknown";
        const long FullWindowTicks = 15L * TimeSpan.TicksPerMinute;
        static readonly string[] Names = new string[] { "ok", "near", "full" };
        static readonly object Gate = new object();
        static string _last = Unknown;

        static int Rank(LicenseSub s, int warnFree, long now)
        {
            if (s.LastFull != 0 && now - s.LastFull <= FullWindowTicks)
            {
                return 2;
            }
            if (s.Exact && s.Limit > 0 && s.Used >= s.Limit)
            {
                return 2;
            }
            if (s.Limit > 0 && s.Used >= 0 && s.Used >= s.Limit - warnFree)
            {
                return 1;
            }
            return 0;
        }

        public static string Overall(Dictionary<string, LicenseSub> subs, bool sampled, int warnFree, long now)
        {
            bool known = sampled;
            int worst = 0;
            foreach (LicenseSub s in subs.Values)
            {
                if (s.LastFull != 0)
                {
                    known = true;
                }
                worst = Math.Max(worst, Rank(s, warnFree, now));
            }
            return known ? Names[worst] : Unknown;
        }

        // 追加到健康检查 JSON 末尾（以逗号开头）：总体状态和按子系统的数字。没有工作站名，也没有身份信息。
        public static string HealthFragment()
        {
            bool sampled;
            Dictionary<string, LicenseSub> subs = LicenseState.Snapshot(out sampled);
            long now = DateTime.UtcNow.Ticks;
            StringBuilder buf = new StringBuilder();
            buf.Append(",\"license\":\"");
            buf.Append(Overall(subs, sampled, LicenseState.WarnFree, now));
            buf.Append("\",\"license_detail\":{");
            List<string> keys = new List<string>(subs.Keys);
            keys.Sort(StringComparer.Ordinal);
            bool first = true;
            for (int i = 0; i < keys.Count; i++)
            {
                LicenseSub s = subs[keys[i]];
                int full24 = Full24(s, now);
                if (s.Used < 0 && s.Limit < 0 && full24 == 0)
                {
                    continue;
                }
                if (!first)
                {
                    buf.Append(',');
                }
                first = false;
                AppendSub(buf, keys[i], s, full24);
            }
            buf.Append('}');
            LicensePackView.Append(buf, LicenseState.Source, LicenseState.Packs());
            return buf.ToString();
        }

        // 键只会是两位大写字母（LicenseState 只收这种），不需要转义。没采到的 used、不知道的 limit 省略。
        static void AppendSub(StringBuilder buf, string sub, LicenseSub s, int full24)
        {
            buf.Append('"').Append(sub).Append("\":{");
            if (s.Used >= 0)
            {
                buf.Append("\"used\":").Append(s.Used.ToString(CultureInfo.InvariantCulture)).Append(',');
            }
            if (s.Limit > 0)
            {
                buf.Append("\"limit\":").Append(s.Limit.ToString(CultureInfo.InvariantCulture)).Append(',');
            }
            buf.Append("\"full_24h\":").Append(full24.ToString(CultureInfo.InvariantCulture)).Append('}');
        }

        static int Full24(LicenseSub s, long now)
        {
            long cutoff = now - TimeSpan.TicksPerDay;
            int count = 0;
            for (int i = 0; i < s.Fulls.Count; i++)
            {
                if (s.Fulls[i] >= cutoff)
                {
                    count++;
                }
            }
            return count;
        }

        // 采样线程每轮之后调用：总体状态变了才写一行事件，变成 near 记 license_near，其余记 license_state。
        public static void Transition()
        {
            bool sampled;
            Dictionary<string, LicenseSub> subs = LicenseState.Snapshot(out sampled);
            long now = DateTime.UtcNow.Ticks;
            int warnFree = LicenseState.WarnFree;
            string state = Overall(subs, sampled, warnFree, now);
            string previous;
            lock (Gate)
            {
                previous = _last;
                if (previous == state)
                {
                    return;
                }
                _last = state;
            }
            Dictionary<string, object> evt = new Dictionary<string, object>();
            evt["state"] = state;
            evt["previous"] = previous;
            evt["subs"] = NotOk(subs, warnFree, now);
            AuditEvent.Write(state == "near" ? "license_near" : "license_state", evt);
        }

        // 不是 ok 的子系统，形如 "SA:full,PU:near"。
        static string NotOk(Dictionary<string, LicenseSub> subs, int warnFree, long now)
        {
            List<string> keys = new List<string>(subs.Keys);
            keys.Sort(StringComparer.Ordinal);
            List<string> parts = new List<string>();
            for (int i = 0; i < keys.Count; i++)
            {
                int rank = Rank(subs[keys[i]], warnFree, now);
                if (rank > 0)
                {
                    parts.Add(keys[i] + ":" + Names[rank]);
                }
            }
            return string.Join(",", parts.ToArray());
        }
    }
}
