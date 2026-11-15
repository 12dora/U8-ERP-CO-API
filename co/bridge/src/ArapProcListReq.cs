using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // arap/process/list 的请求（只读）。登录前（Requests.ApplyProcList）和读线程上（ArapProcList.Run）各解析一次，规则相同。
    // 两种用法：
    // 明细（缺省）：按往来明细主键 Auto_ID 增量读处理行，changed_since 是上一轮的水位，after 是本轮上一页的 next（整数）。
    // 摘要（digest=true）：按（处理方式、处理号）汇总指定期间的批次，after 是上一页的 next（不透明游标）。
    internal sealed class ProcListArgs
    {
        public string Flag = "";
        public int Since;
        public int After;
        public string[] Cursor;
        public int Limit;
        public bool KeysOnly;
        public bool OpenOnly;
        public bool Digest;
        // 摘要：会计年度（0 表示请求没给）和期间（null 表示取未结账期间）。
        public int FiscalYear;
        public int[] Periods;
    }

    internal static class ArapProcListReq
    {
        public const string Path = "/u8co/v1/arap/process/list";
        public const string Action = "arap_process_list";
        // Requests 的字段表：路由，然后是本路由的字段。
        internal static readonly string[] Spec = new string[]
        {
            Path, "flag", "changed_since", "after", "limit", "keys_only", "open_only", "digest", "fiscal_year", "periods"
        };
        public const int DefaultLimit = 100;
        public const int DigestLimit = 500;
        public const int MaxLimit = 500;
        static readonly string[] ListOnly = new string[] { "changed_since", "keys_only", "open_only" };
        static readonly string[] DigestOnly = new string[] { "fiscal_year", "periods" };

        public static bool IsPath(string path)
        {
            return path == Path;
        }

        // 权限规则键：read:arap:process_list:ar|ap（PermRegistry.ProcListRules）。不是本路由返回 null。
        public static string RuleKey(WorkItem item)
        {
            if (item == null || !IsPath(item.Path))
            {
                return null;
            }
            string flag = Requests.Field(item.Body, "flag") as string;
            return KeyOf(flag);
        }

        public static string KeyOf(string flag)
        {
            return "read:arap:process_list:" + (flag == "AP" ? "ap" : "ar");
        }

        public static ProcListArgs Parse(Dictionary<string, object> body)
        {
            ProcListArgs a = new ProcListArgs();
            a.Flag = Requests.Field(body, "flag") as string;
            if (a.Flag != "AR" && a.Flag != "AP")
            {
                throw GlReq.Bad("flag 只能是 AR 或 AP", "flag");
            }
            a.Digest = ListArgs.OptBool(body, "digest", "digest");
            Exclude(body, a.Digest ? ListOnly : DigestOnly, a.Digest ? "digest=true 时不能带 " : "digest=true 时才能带 ");
            if (a.Digest)
            {
                ParseDigest(a, body);
            }
            else
            {
                ParseList(a, body);
            }
            object limit = Requests.Field(body, "limit");
            a.Limit = limit == null ? (a.Digest ? DigestLimit : DefaultLimit) : GlReq.IntIn(limit, "limit", 1, MaxLimit);
            return a;
        }

        static void Exclude(Dictionary<string, object> body, string[] keys, string message)
        {
            foreach (string key in keys)
            {
                if (Requests.Field(body, key) != null)
                {
                    throw GlReq.Bad(message + key, key);
                }
            }
        }

        // 明细：open_only 缺省在增量（changed_since > 0）时为 true，全量时为 false。
        static void ParseList(ProcListArgs a, Dictionary<string, object> body)
        {
            a.Since = Since(Requests.Field(body, "changed_since"));
            object after = Requests.Field(body, "after");
            a.After = after == null ? 0 : GlReq.IntIn(after, "after", 0, int.MaxValue);
            a.KeysOnly = ListArgs.OptBool(body, "keys_only", "keys_only");
            a.OpenOnly = Requests.Field(body, "open_only") == null ? a.Since > 0 : ListArgs.OptBool(body, "open_only", "open_only");
        }

        // changed_since：上一轮的 watermark，十进制数字串或整数，0 到 2147483647。
        internal static int Since(object raw)
        {
            if (raw == null)
            {
                return 0;
            }
            if (raw is int || raw is long)
            {
                return GlReq.IntIn(raw, "changed_since", 0, int.MaxValue);
            }
            string text = raw as string;
            int value;
            if (text == null || text.Length == 0 || text.Length > 10 || !Digits(text)
                || !int.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out value))
            {
                throw GlReq.Bad("changed_since 必须是 0 到 2147483647 的十进制数字串", "changed_since");
            }
            return value;
        }

        static bool Digits(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < '0' || text[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }

        // 摘要：fiscal_year 2000 到 2099；periods 1 到 12 个不重复的期间（1 到 12），省略即未结账期间。
        static void ParseDigest(ProcListArgs a, Dictionary<string, object> body)
        {
            object year = Requests.Field(body, "fiscal_year");
            a.FiscalYear = year == null ? 0 : GlReq.IntIn(year, "fiscal_year", 2000, 2099);
            object periods = Requests.Field(body, "periods");
            if (periods != null)
            {
                a.Periods = Periods(periods);
            }
            object after = Requests.Field(body, "after");
            if (after == null)
            {
                return;
            }
            if (!(after is string))
            {
                throw GlReq.Bad("digest=true 时 after 是上一页的 next（字符串）", "after");
            }
            a.Cursor = Reports.Uncursor((string)after, 2);
        }

        static int[] Periods(object raw)
        {
            // JavaScriptSerializer 把数组解成 object[]；也收 ArrayList。
            object[] arr = raw as object[];
            ArrayList list = arr != null ? new ArrayList(arr) : raw as ArrayList;
            if (list == null || list.Count < 1 || list.Count > 12)
            {
                throw GlReq.Bad("periods 必须是 1 到 12 个期间", "periods");
            }
            List<int> seen = new List<int>();
            for (int i = 0; i < list.Count; i++)
            {
                int p = GlReq.IntIn(list[i], "periods." + i.ToString(CultureInfo.InvariantCulture), 1, 12);
                if (seen.Contains(p))
                {
                    throw GlReq.Bad("periods 有重复的期间", "periods");
                }
                seen.Add(p);
            }
            seen.Sort();
            return seen.ToArray();
        }

        // 登录日期（yyyy-MM-dd）的年份：摘要的缺省会计年度，也是列未结账期间的上限年度（不列以后年度预置的期间）。
        internal static int LoginYear(WorkItem item)
        {
            string date = item == null || item.Date == null ? "" : item.Date;
            int year;
            if (date.Length < 4 || !int.TryParse(date.Substring(0, 4), NumberStyles.None, CultureInfo.InvariantCulture, out year))
            {
                return DateTime.Today.Year;
            }
            return year;
        }
    }

    // arap/process/list 接到请求分派（Requests）上的部分。
    internal static partial class Requests
    {
        // 不带 type、id 的路由（Typeless）：账务处理路由（IsLedgerRoute）和 arap/process/list。
        static bool IsLedgerOrProcList(string path)
        {
            return IsLedgerRoute(path) || ArapProcListReq.IsPath(path);
        }

        // 审计 action（BatchAction 的下一档）：arap_process_list，其余交给 ArapProcAction。
        static string ProcListAction(string path)
        {
            return ArapProcListReq.IsPath(path) ? ArapProcListReq.Action : ArapProcAction(path);
        }

        // 不是 arap/process/list 返回 false，不动任务。登录子系统就是 flag。
        static bool ApplyProcList(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (!ArapProcListReq.IsPath(path))
            {
                return false;
            }
            ProcListArgs a = ArapProcListReq.Parse(body);
            item.SubId = a.Flag;
            return true;
        }
    }

    // 应收 / 应付处理记录（arap/process/list，只读）：U8「取消操作」AR0807（处理包括核销、转账、并账、红票对冲、汇兑损益、票据等，
    // 在 U8 里就是在取消操作界面里列出），另收「应收核销明细表」AR060107 和核销写入认的「选择收款」AR0503；应付 AP0807 / AP060107 / AP0503
    // （按 U8 授权目录核对）。
    // 数据权限按往来明细行的往来单位（必控）、部门、业务员（可空），同核销。列表变少照常 200：事件服务的操作员要有全部
    // 往来单位的数据权限，否则一批处理只看到部分行。
    internal static partial class PermRegistry
    {
        static PermRule[] ProcListRules()
        {
            return new PermRule[]
            {
                R(ArapProcListReq.KeyOf("AR"), "应收处理记录", A("AR0807", "AR060107", "AR0503"),
                    WriteoffObjs(PermObj.Customer)),
                R(ArapProcListReq.KeyOf("AP"), "应付处理记录", A("AP0807", "AP060107", "AP0503"),
                    WriteoffObjs(PermObj.Vendor))
            };
        }
    }
}
