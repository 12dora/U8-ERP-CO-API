using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一行期望签名的核对结果。Status：ok / mismatch / missing / unknown。Note 只是提示，不影响 Status。
    internal sealed class SigEntry
    {
        public SigNeed Need;
        public string Status = "unknown";
        public string Detail = "";
        public string Note = "";
    }

    // 把表里的一行与类型库成员对比。规则：
    // 必填参数个数 ≤ 实参个数 ≤ 参数总数；桥按引用传的槽位类型库必须也是按引用（[out] 或指针），
    // 否则桥读不到写回值（PreferMeasured 的行只记提示）；类型库按引用而桥按值传不算问题（VB6 会自己转换）。
    internal static class SigJudge
    {
        const int InvokeFunc = 1;
        const int InvokeGet = 2;
        const int InvokePut = 4;
        const int InvokePutRef = 8;

        public static void Judge(SigNeed need, List<SigFunc> members, SigEntry entry)
        {
            List<SigFunc> named = Named(members, need.Member);
            if (named.Count == 0)
            {
                Set(entry, "mismatch", "类型库里没有成员 " + need.Member);
                return;
            }
            SigEntry first = null;
            for (int i = 0; i < named.Count; i++)
            {
                if (!InvokeFits(need.Invoke, named[i].Invoke))
                {
                    continue;
                }
                SigEntry trial = new SigEntry();
                Compare(need, named[i], trial);
                if (trial.Status == "ok")
                {
                    Copy(trial, entry);
                    return;
                }
                if (first == null)
                {
                    first = trial;
                }
            }
            if (first == null)
            {
                Set(entry, "mismatch", "成员 " + need.Member + " 的调用方式不是 " + need.Invoke);
                return;
            }
            Copy(first, entry);
        }

        static List<SigFunc> Named(List<SigFunc> members, string name)
        {
            List<SigFunc> list = new List<SigFunc>();
            for (int i = 0; i < members.Count; i++)
            {
                if (string.Equals(members[i].Name, name, StringComparison.OrdinalIgnoreCase))
                {
                    list.Add(members[i]);
                }
            }
            return list;
        }

        // InvokeMember 的 GetProperty / InvokeMethod 在 IDispatch 上对方法和属性读取都能落到。
        static bool InvokeFits(string invoke, int kind)
        {
            if (invoke == "set")
            {
                return kind == InvokePut || kind == InvokePutRef;
            }
            return kind == InvokeFunc || kind == InvokeGet;
        }

        static void Compare(SigNeed need, SigFunc f, SigEntry entry)
        {
            int argc = need.ArgCount;
            if (argc < f.Required)
            {
                Set(entry, "mismatch", Fmt("实参 {0} 个，少于类型库必填的 {1} 个", argc, f.Required));
                return;
            }
            if (argc > f.Total)
            {
                Set(entry, "mismatch", Fmt("实参 {0} 个，多于类型库的 {1} 个参数", argc, f.Total));
                return;
            }
            string refProblem = ByRefProblem(need, f);
            if (refProblem.Length > 0 && !need.PreferMeasured)
            {
                Set(entry, "mismatch", refProblem);
                return;
            }
            Set(entry, "ok", "");
            entry.Note = refProblem.Length > 0 ? refProblem + "（以运行时行为为准）" : TailNote(need, f);
        }

        static string ByRefProblem(SigNeed need, SigFunc f)
        {
            List<string> bad = new List<string>();
            for (int i = 0; i < need.ByRef.Length; i++)
            {
                int slot = need.ByRef[i];
                if (slot >= 0 && slot < f.ByRef.Length && !f.ByRef[slot])
                {
                    bad.Add(slot.ToString(CultureInfo.InvariantCulture));
                }
            }
            if (bad.Count == 0)
            {
                return "";
            }
            return "第 " + string.Join(",", bad.ToArray()) + " 个参数（从 0 起）桥按引用传，类型库是按值";
        }

        static string TailNote(SigNeed need, SigFunc f)
        {
            int tail = f.Total - need.ArgCount;
            if (need.OptionalTail < 0 || tail == need.OptionalTail)
            {
                return "";
            }
            return Fmt("类型库在实参之后有 {0} 个可选参数，表里记的是 {1} 个", tail, need.OptionalTail);
        }

        static string Fmt(string format, int a, int b)
        {
            return string.Format(CultureInfo.InvariantCulture, format, a, b);
        }

        internal static void Set(SigEntry entry, string status, string detail)
        {
            entry.Status = status;
            entry.Detail = detail ?? "";
        }

        static void Copy(SigEntry from, SigEntry to)
        {
            to.Status = from.Status;
            to.Detail = from.Detail;
            to.Note = from.Note;
        }
    }
}
