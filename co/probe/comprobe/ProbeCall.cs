using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

namespace U8Co
{
    internal sealed class Bound
    {
        public object[] Args;
        public string[] Names;
    }

    internal static class ProbeCall
    {
        // 口令占位符：只能当 call 的参数（如网上财务 LoginDB 的口令），不进变量表、不回写、不回显。
        // sql 参数、set / dom.set 的值一律拒绝（否则 select ? 或先 set 再 get 就能把口令打出来）。
        const string SecretRef = "$__secret";

        public static void Exec(ProbeRun run, Dictionary<string, object> step)
        {
            string op = StepIo.Op(step);
            if (op == "create")
            {
                Create(run, step);
                return;
            }
            if (op == "call")
            {
                Call(run, step);
                return;
            }
            if (op == "get")
            {
                Get(run, step);
                return;
            }
            if (op == "set")
            {
                Set(run, step);
                return;
            }
            throw new BridgeException(400, "bad_request", "不认识操作 " + op);
        }

        public static object Arg(ProbeRun run, object node)
        {
            if (node == null)
            {
                return null;
            }
            string text = node as string;
            if (text != null)
            {
                return TextArg(run, text);
            }
            if (Plain(node))
            {
                return node;
            }
            if (node is decimal || node is double || node is float)
            {
                return Convert.ToDouble(node);
            }
            Dictionary<string, object> map = node as Dictionary<string, object>;
            if (map == null)
            {
                throw new BridgeException(400, "bad_request", "参数形式无效");
            }
            return Typed(map);
        }

        public static object[] Scalars(ProbeRun run, ArrayList list)
        {
            if (list == null)
            {
                return new object[0];
            }
            object[] args = new object[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                args[i] = Scalar(run, list[i]);
            }
            return args;
        }

        static void Create(ProbeRun run, Dictionary<string, object> step)
        {
            if (!StepIo.Has(step, "var"))
            {
                throw new BridgeException(400, "bad_request", "需要变量");
            }
            string progid = StepIo.Req(step, "progid");
            object obj = ComUtil.Create(progid);
            if (obj == null)
            {
                throw new BridgeException(503, "com_unavailable", "组件无法创建 " + progid);
            }
            Line line = ProbeOut.Begin(run, step, true);
            ProbeOut.Keep(run, step, obj, line);
            line.Bool("com", true);
            ProbeOut.Emit(line);
        }

        static void Call(ProbeRun run, Dictionary<string, object> step)
        {
            object target = run.NeedRef(StepIo.Req(step, "target"));
            string method = StepIo.Req(step, "method");
            Bound bound = Bind(run, StepIo.List(step, "args"));
            ApplyNull(bound, StepIo.List(step, "dispatch_null"));
            int[] refs = Refs(StepIo.List(step, "byref"), bound.Args.Length);
            object ret = Invoke(target, method, bound.Args, refs);
            WriteBack(run, bound, refs);
            Line line = ProbeOut.Begin(run, step, true);
            ProbeOut.Keep(run, step, ret, line);
            ProbeOut.Scalar(line, ret);
            Written(line, bound, refs);
            ProbeOut.Emit(line);
        }

        static object Invoke(object target, string method, object[] args, int[] refs)
        {
            if (refs == null)
            {
                return ComUtil.Call(target, method, args);
            }
            return ComUtil.CallRef(target, method, args, refs);
        }

        static void Get(ProbeRun run, Dictionary<string, object> step)
        {
            object target = run.NeedRef(StepIo.Req(step, "target"));
            object value = ComUtil.Get(target, StepIo.Req(step, "prop"));
            Line line = ProbeOut.Begin(run, step, true);
            ProbeOut.Keep(run, step, value, line);
            ProbeOut.Scalar(line, value);
            ProbeOut.Emit(line);
        }

        static void Set(ProbeRun run, Dictionary<string, object> step)
        {
            if (!StepIo.Present(step, "value"))
            {
                throw new BridgeException(400, "bad_request", "缺少 value");
            }
            object target = run.NeedRef(StepIo.Req(step, "target"));
            ComUtil.Set(target, StepIo.Req(step, "prop"), Arg(run, step["value"]));
            ProbeOut.Emit(ProbeOut.Begin(run, step, true));
        }

        static object Scalar(ProbeRun run, object node)
        {
            object value = Arg(run, node);
            if (value is short)
            {
                return (int)(short)value;
            }
            if (value != null && Marshal.IsComObject(value))
            {
                throw new BridgeException(400, "bad_request", "参数不是标量");
            }
            return value;
        }

        static object TextArg(ProbeRun run, string text)
        {
            if (text == SecretRef)
            {
                throw new BridgeException(400, "bad_request", "$__secret 只能当 call 的参数");
            }
            if (text.Length > 0 && text[0] == '$')
            {
                return run.NeedRef(text);
            }
            return text;
        }

        static bool Plain(object node)
        {
            return node is bool || node is int || node is long;
        }

        static object Typed(Dictionary<string, object> map)
        {
            if (map.Count != 1)
            {
                throw new BridgeException(400, "bad_request", "参数形式无效");
            }
            if (map.ContainsKey("short"))
            {
                return (short)Nums.AsInt(map["short"]);
            }
            if (map.ContainsKey("int"))
            {
                return Nums.AsInt(map["int"]);
            }
            if (map.ContainsKey("bool"))
            {
                return AsBool(map["bool"]);
            }
            if (map.ContainsKey("date"))
            {
                // VT_DATE：按引用传日期的 VB 参数（如 InitPubs2 的 OprateDate）不收字符串。
                return DateTime.ParseExact(Values.Text(map["date"]), "yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture);
            }
            throw new BridgeException(400, "bad_request", "参数形式无效");
        }

        static bool AsBool(object value)
        {
            if (value is bool)
            {
                return (bool)value;
            }
            throw new BridgeException(400, "bad_request", "需要布尔值");
        }

        static Bound Bind(ProbeRun run, ArrayList list)
        {
            if (list == null)
            {
                list = new ArrayList();
            }
            Bound bound = new Bound();
            bound.Args = new object[list.Count];
            bound.Names = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                Fill(run, bound, list, i);
            }
            return bound;
        }

        static void Fill(ProbeRun run, Bound bound, ArrayList list, int index)
        {
            string text = list[index] as string;
            if (text == SecretRef)
            {
                // 不记名字：按引用传也不写回变量表。
                bound.Args[index] = run.Secret;
                return;
            }
            if (text != null && text.Length > 1 && text[0] == '$')
            {
                bound.Names[index] = ProbeRun.RefName(text);
            }
            bound.Args[index] = Arg(run, list[index]);
        }

        static void ApplyNull(Bound bound, ArrayList indexes)
        {
            if (indexes == null)
            {
                return;
            }
            for (int i = 0; i < indexes.Count; i++)
            {
                int index = Nums.AsInt(indexes[i]);
                if (index < 0 || index >= bound.Args.Length)
                {
                    throw new BridgeException(400, "bad_request", "引用参数序号无效");
                }
                bound.Args[index] = new DispatchWrapper(null);
            }
        }

        static int[] Refs(ArrayList list, int length)
        {
            if (list == null || list.Count == 0)
            {
                return null;
            }
            int[] refs = new int[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                refs[i] = Nums.AsInt(list[i]);
                if (refs[i] < 0 || refs[i] >= length)
                {
                    throw new BridgeException(400, "bad_request", "引用参数序号无效");
                }
            }
            return refs;
        }

        static void WriteBack(ProbeRun run, Bound bound, int[] refs)
        {
            if (refs == null)
            {
                return;
            }
            for (int i = 0; i < refs.Length; i++)
            {
                string name = bound.Names[refs[i]];
                if (name != null)
                {
                    run.Put(name, bound.Args[refs[i]]);
                }
            }
        }

        static void Written(Line line, Bound bound, int[] refs)
        {
            if (refs == null)
            {
                return;
            }
            StringBuilder buf = new StringBuilder();
            int count = AppendNames(buf, bound, refs);
            if (count > 0)
            {
                line.Raw("written", buf.ToString());
            }
        }

        static int AppendNames(StringBuilder buf, Bound bound, int[] refs)
        {
            buf.Append('[');
            int count = 0;
            for (int i = 0; i < refs.Length; i++)
            {
                string name = bound.Names[refs[i]];
                if (name == null)
                {
                    continue;
                }
                if (count > 0)
                {
                    buf.Append(',');
                }
                buf.Append(Line.Quote(name));
                count++;
            }
            buf.Append(']');
            return count;
        }
    }
}
