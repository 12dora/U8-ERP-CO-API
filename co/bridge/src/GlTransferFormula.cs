using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // QM(科目, 月|年, [借|贷], [辅助项]) 的一次取数。
    internal sealed class GlQm
    {
        public string Code;
        // 年：取年末（第 12 期）余额，只在第 12 期生成；月：本期期末余额。
        public bool Year;
        // 空串：按科目余额方向的净额；借 / 贷：只取该方向的余额（另一方向为 0）。
        public string Side = "";
        // 辅助项编码（科目只核算一种辅助项时按它取）；空串取科目合计。
        public string Aux = "";
    }

    // 公式的语法树：数字、QM 取数（Atom 是 Atoms 的下标）、一元负号、四则运算。
    internal sealed class GlFormulaNode
    {
        public char Op;
        public decimal Num;
        public int Atom = -1;
        public GlFormulaNode Left;
        public GlFormulaNode Right;
    }

    internal sealed class GlFormula
    {
        // 整个公式就是 CE()：借贷平衡差额。
        public bool Ce;
        public List<GlQm> Atoms = new List<GlQm>();
        public GlFormulaNode Root;
    }

    // 自定义转账公式（GL_bautotran.cformula）的解析与计算。支持 QM() 和 CE()，再加数字和 + - * / 括号；
    // 其他函数（FS、JE、QC、LFS 等）不猜，400 写明函数名。全角括号、逗号按半角处理。
    internal static class GlTransferFormula
    {
        public static GlFormula Parse(string text, string where)
        {
            string src = Normalize(text);
            GlFormula f = new GlFormula();
            if (src.Equals("CE()", StringComparison.OrdinalIgnoreCase))
            {
                f.Ce = true;
                return f;
            }
            int pos = 0;
            f.Root = Expr(src, ref pos, f, where);
            if (pos != src.Length)
            {
                throw Bad(where, "公式「" + text + "」无法解析（第 " + (pos + 1).ToString(CultureInfo.InvariantCulture) + " 个字符）");
            }
            return f;
        }

        static string Normalize(string text)
        {
            StringBuilder sb = new StringBuilder();
            foreach (char ch in text ?? "")
            {
                if (char.IsWhiteSpace(ch))
                {
                    continue;
                }
                sb.Append(ch == '，' ? ',' : ch == '（' ? '(' : ch == '）' ? ')' : ch);
            }
            return sb.ToString();
        }

        static GlFormulaNode Expr(string s, ref int pos, GlFormula f, string where)
        {
            GlFormulaNode left = Term(s, ref pos, f, where);
            while (pos < s.Length && (s[pos] == '+' || s[pos] == '-'))
            {
                char op = s[pos++];
                left = Bin(op, left, Term(s, ref pos, f, where));
            }
            return left;
        }

        static GlFormulaNode Term(string s, ref int pos, GlFormula f, string where)
        {
            GlFormulaNode left = Factor(s, ref pos, f, where);
            while (pos < s.Length && (s[pos] == '*' || s[pos] == '/'))
            {
                char op = s[pos++];
                left = Bin(op, left, Factor(s, ref pos, f, where));
            }
            return left;
        }

        static GlFormulaNode Factor(string s, ref int pos, GlFormula f, string where)
        {
            if (pos >= s.Length)
            {
                throw Bad(where, "公式「" + s + "」不完整");
            }
            char ch = s[pos];
            if (ch == '-')
            {
                pos++;
                return Bin('~', Factor(s, ref pos, f, where), null);
            }
            if (ch == '(')
            {
                pos++;
                GlFormulaNode inner = Expr(s, ref pos, f, where);
                Expect(s, ref pos, ')', where);
                return inner;
            }
            if (char.IsDigit(ch) || ch == '.')
            {
                return Number(s, ref pos, where);
            }
            return Call(s, ref pos, f, where);
        }

        static GlFormulaNode Number(string s, ref int pos, string where)
        {
            int start = pos;
            while (pos < s.Length && (char.IsDigit(s[pos]) || s[pos] == '.'))
            {
                pos++;
            }
            GlFormulaNode node = new GlFormulaNode();
            node.Op = 'n';
            if (!decimal.TryParse(s.Substring(start, pos - start), NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out node.Num))
            {
                throw Bad(where, "公式「" + s + "」里的数字无法识别");
            }
            return node;
        }

        // 函数调用：只认 QM，参数是不含括号的文本。
        static GlFormulaNode Call(string s, ref int pos, GlFormula f, string where)
        {
            int start = pos;
            while (pos < s.Length && char.IsLetter(s[pos]))
            {
                pos++;
            }
            string name = s.Substring(start, pos - start);
            if (name.Length == 0 || pos >= s.Length || s[pos] != '(')
            {
                throw Bad(where, "公式「" + s + "」无法解析（第 " + (start + 1).ToString(CultureInfo.InvariantCulture) + " 个字符）");
            }
            if (!name.Equals("QM", StringComparison.OrdinalIgnoreCase))
            {
                throw Bad(where, "公式使用了桥暂不支持的函数 " + name + "（只支持 QM、CE），请在 U8 客户端生成");
            }
            int close = s.IndexOf(')', pos);
            if (close < 0)
            {
                throw Bad(where, "公式「" + s + "」缺少右括号");
            }
            string[] args = s.Substring(pos + 1, close - pos - 1).Split(',');
            pos = close + 1;
            f.Atoms.Add(Qm(args, where));
            GlFormulaNode node = new GlFormulaNode();
            node.Op = 'q';
            node.Atom = f.Atoms.Count - 1;
            return node;
        }

        // QM(科目, 月|年, [借|贷], [辅助项])：多于一个辅助项、或第 5 个以后的参数有值，都不支持。
        static GlQm Qm(string[] args, string where)
        {
            if (args.Length < 2 || args[0].Length == 0)
            {
                throw Bad(where, "QM 至少要有科目和期间两个参数");
            }
            GlQm qm = new GlQm();
            qm.Code = args[0];
            if (args[1] != "月" && args[1] != "年")
            {
                throw Bad(where, "QM 的期间参数「" + args[1] + "」桥暂不支持（只支持 月、年）");
            }
            qm.Year = args[1] == "年";
            qm.Side = Side(args.Length > 2 ? args[2] : "", where);
            qm.Aux = args.Length > 3 ? args[3] : "";
            Extra(args, where);
            return qm;
        }

        static string Side(string side, string where)
        {
            if (side.Length > 0 && side != "借" && side != "贷")
            {
                throw Bad(where, "QM 的方向参数「" + side + "」桥暂不支持（只支持 借、贷 或留空）");
            }
            return side;
        }

        static void Extra(string[] args, string where)
        {
            for (int i = 4; i < args.Length; i++)
            {
                if (args[i].Length > 0)
                {
                    throw Bad(where, "QM 的第 " + (i + 1).ToString(CultureInfo.InvariantCulture) + " 个参数桥暂不支持（只支持一个辅助项）");
                }
            }
        }

        static void Expect(string s, ref int pos, char ch, string where)
        {
            if (pos >= s.Length || s[pos] != ch)
            {
                throw Bad(where, "公式「" + s + "」缺少 " + ch);
            }
            pos++;
        }

        static GlFormulaNode Bin(char op, GlFormulaNode left, GlFormulaNode right)
        {
            GlFormulaNode node = new GlFormulaNode();
            node.Op = op;
            node.Left = left;
            node.Right = right;
            return node;
        }

        // values[i] 是 Atoms[i] 的取数结果。除以 0 时 409（定义本身有问题）。
        public static decimal Eval(GlFormulaNode node, decimal[] values)
        {
            switch (node.Op)
            {
                case 'n':
                    return node.Num;
                case 'q':
                    return values[node.Atom];
                case '~':
                    return -Eval(node.Left, values);
                case '+':
                    return Eval(node.Left, values) + Eval(node.Right, values);
                case '-':
                    return Eval(node.Left, values) - Eval(node.Right, values);
                case '*':
                    return Eval(node.Left, values) * Eval(node.Right, values);
                default:
                    return Divide(Eval(node.Left, values), Eval(node.Right, values));
            }
        }

        static decimal Divide(decimal a, decimal b)
        {
            if (b == 0)
            {
                throw GlState.Refuse("自定义转账公式除以 0，请检查 U8 的转账定义");
            }
            return a / b;
        }

        public static bool UsesYear(GlFormula f)
        {
            foreach (GlQm qm in f.Atoms)
            {
                if (qm.Year)
                {
                    return true;
                }
            }
            return false;
        }

        static BridgeException Bad(string where, string message)
        {
            return new BridgeException(400, "bad_request", where + message);
        }
    }
}
