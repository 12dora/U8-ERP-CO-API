using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;

namespace U8Co
{
    internal sealed class Line
    {
        readonly StringBuilder _buf = new StringBuilder();
        bool _comma;

        public Line()
        {
            _buf.Append('{');
        }

        public void Bool(string key, bool value)
        {
            Sep(key);
            _buf.Append(value ? "true" : "false");
        }

        public void Num(string key, int value)
        {
            Sep(key);
            _buf.Append(value.ToString(CultureInfo.InvariantCulture));
        }

        public void Null(string key)
        {
            Sep(key);
            _buf.Append("null");
        }

        public void Str(string key, string value)
        {
            if (value == null)
            {
                return;
            }
            Sep(key);
            _buf.Append('"');
            _buf.Append(Esc(value));
            _buf.Append('"');
        }

        public void Raw(string key, string json)
        {
            Sep(key);
            _buf.Append(json);
        }

        public string End()
        {
            _buf.Append('}');
            return _buf.ToString();
        }

        public static string Quote(string text)
        {
            return "\"" + Esc(text ?? "") + "\"";
        }

        void Sep(string key)
        {
            if (_comma)
            {
                _buf.Append(',');
            }
            _comma = true;
            _buf.Append('"');
            _buf.Append(key);
            _buf.Append("\":");
        }

        static string Esc(string text)
        {
            StringBuilder buf = new StringBuilder();
            for (int i = 0; i < text.Length; i++)
            {
                Append(buf, text[i]);
            }
            return buf.ToString();
        }

        static void Append(StringBuilder buf, char c)
        {
            if (c == '\\' || c == '"')
            {
                buf.Append('\\');
                buf.Append(c);
                return;
            }
            if (c == '\r')
            {
                buf.Append("\\r");
                return;
            }
            if (c == '\n')
            {
                buf.Append("\\n");
                return;
            }
            if (c == '\t')
            {
                buf.Append("\\t");
                return;
            }
            if (c < ' ')
            {
                buf.Append("\\u");
                buf.Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                return;
            }
            buf.Append(c);
        }
    }

    internal static class ProbeOut
    {
        public static Line Begin(ProbeRun run, Dictionary<string, object> step, bool ok)
        {
            Line line = new Line();
            line.Bool("ok", ok);
            line.Num("i", run.Index);
            line.Str("op", StepIo.Op(step));
            return line;
        }

        public static void Emit(Line line)
        {
            Console.WriteLine(line.End());
        }

        public static void Keep(ProbeRun run, Dictionary<string, object> step, object value, Line line)
        {
            string name = StepIo.Opt(step, "var");
            if (name == null || name.Length == 0)
            {
                return;
            }
            run.Put(name, value);
            line.Str("var", name);
        }

        public static void Scalar(Line line, object value)
        {
            if (value == null || value is DBNull)
            {
                line.Null("value");
                return;
            }
            if (value is bool)
            {
                line.Bool("value", (bool)value);
                return;
            }
            if (value is string)
            {
                line.Str("value", Cut((string)value, 4000));
                return;
            }
            if (IsNumber(value))
            {
                line.Raw("value", Nums.Invariant(value));
                return;
            }
            line.Bool("com", true);
        }

        public static void Fail(ProbeRun run, int index, string op, Exception ex)
        {
            Exception cur = Unwrap(ex);
            Line line = new Line();
            line.Bool("ok", false);
            if (index >= 0)
            {
                line.Num("i", index);
            }
            if (op != null && op.Length > 0)
            {
                line.Str("op", op);
            }
            Stamp(line, cur);
            line.Str("message", Clean(run, Message(cur)));
            Console.WriteLine(line.End());
        }

        static void Stamp(Line line, Exception cur)
        {
            BridgeException be = cur as BridgeException;
            if (be != null)
            {
                line.Num("status", be.Status);
                line.Str("code", be.Code);
            }
            COMException com = cur as COMException;
            if (com != null)
            {
                line.Num("hresult", com.ErrorCode);
            }
        }

        static string Message(Exception cur)
        {
            if (cur.Message != null && cur.Message.Length > 0)
            {
                return cur.Message;
            }
            return cur.GetType().Name;
        }

        public static string Clean(ProbeRun run, string text)
        {
            string raw = ProbeHost.Scrub(text ?? "");
            if (run != null && run.Secret != null && run.Secret.Length > 0)
            {
                raw = raw.Replace(run.Secret, "***");
            }
            return Cut(raw, 2000);
        }

        public static string Cut(string text, int max)
        {
            if (text == null)
            {
                return "";
            }
            if (text.Length <= max)
            {
                return text;
            }
            return text.Substring(0, max);
        }

        static bool IsNumber(object value)
        {
            return value is short || value is int || value is long || value is float
                || value is double || value is decimal;
        }

        public static string Rows(List<Dictionary<string, object>> rows)
        {
            StringBuilder buf = new StringBuilder();
            buf.Append('[');
            for (int i = 0; i < rows.Count; i++)
            {
                if (i > 0)
                {
                    buf.Append(',');
                }
                buf.Append(Row(rows[i]));
            }
            buf.Append(']');
            return buf.ToString();
        }

        static string Row(Dictionary<string, object> row)
        {
            StringBuilder buf = new StringBuilder();
            buf.Append('{');
            bool comma = false;
            foreach (KeyValuePair<string, object> kv in row)
            {
                if (comma)
                {
                    buf.Append(',');
                }
                comma = true;
                buf.Append(Line.Quote(kv.Key));
                buf.Append(':');
                buf.Append(Val(kv.Value));
            }
            buf.Append('}');
            return buf.ToString();
        }

        static string Val(object value)
        {
            if (value == null)
            {
                return "null";
            }
            return Line.Quote(Cut(Convert.ToString(value), 8000));
        }

        static Exception Unwrap(Exception ex)
        {
            Exception cur = ex;
            while (cur is TargetInvocationException && cur.InnerException != null)
            {
                cur = cur.InnerException;
            }
            return cur;
        }
    }
}
