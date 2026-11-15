using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 自定义转账的取数与组行（GlTransferCustom 调用）。
    // QM 的值：公式科目（含下级）已记账的期末余额，借正贷负合计；不给方向时按科目余额方向（借方性质取借-贷，贷方性质取贷-借），
    // 给「借」只取借方余额、给「贷」只取贷方余额（另一方向时为 0）。给了辅助项时科目必须是只核算一种辅助项的末级科目，按该辅助项取。
    // 行的辅助项：定义行上固定的辅助项优先；没有时取本行公式里唯一一个带辅助项的 QM 的辅助项，
    // 类型须与本行科目的辅助核算相同。
    internal static class GlTransferCustomCalc
    {
        // 科目只核算一种辅助项时返回它在 GlTransferBal.AuxCols 里的下标（个人往来是人员，部门随人员）；没有 -1，多种 -2。
        internal static int Kind(GlTransferCodes codes, string code)
        {
            bool[] dims = codes.Dims(code);
            int[] marks = new int[] { dims[1] ? 1 : (dims[0] ? 0 : -1), dims[2] ? 2 : -1, dims[3] ? 3 : -1, dims[5] ? 5 : -1 };
            int kind = -1;
            foreach (int mark in marks)
            {
                if (mark >= 0)
                {
                    kind = kind == -1 ? mark : -2;
                }
            }
            return kind;
        }

        // 科目闸门：不合格返回原因。
        public static string Accounts(GlCustomDef def, GlTransferCodes codes)
        {
            for (int i = 0; i < def.Rows.Count; i++)
            {
                GlTransferDef row = def.Rows[i];
                string at = "第 " + row.Inid.ToString(CultureInfo.InvariantCulture) + " 行";
                if (!codes.Leaf(row.Code))
                {
                    return at + "科目 " + row.Code + " 不存在或不是末级科目";
                }
                string qm = QmProblem(def.Formulas[i], codes);
                if (qm != null)
                {
                    return at + qm;
                }
                if (LineAux(def, i, codes) == null)
                {
                    return at + "科目 " + row.Code + " 需要辅助核算，定义和公式都没有给出，桥暂不支持";
                }
            }
            return null;
        }

        static string QmProblem(GlFormula f, GlTransferCodes codes)
        {
            foreach (GlQm qm in f.Atoms)
            {
                if (codes.Find(qm.Code) == null)
                {
                    return "QM 的科目 " + qm.Code + " 不存在";
                }
                if (qm.Aux.Length > 0 && (!codes.Leaf(qm.Code) || Kind(codes, qm.Code) < 0))
                {
                    return "QM(" + qm.Code + ") 带辅助项参数，科目须是只核算一种辅助项的末级科目，桥暂不支持";
                }
            }
            return null;
        }

        // 第 i 行的辅助项（下标同 AuxCols）；本行科目要的辅助项给不全时返回 null。
        internal static string[] LineAux(GlCustomDef def, int i, GlTransferCodes codes)
        {
            GlTransferDef row = def.Rows[i];
            string[] aux = (string[])row.Aux.Clone();
            int kind = Kind(codes, row.Code);
            GlQm from = Single(def.Formulas[i]);
            if (kind >= 0 && aux[kind].Length == 0 && from != null && Kind(codes, from.Code) == kind)
            {
                aux[kind] = from.Aux;
            }
            bool[] dims = codes.Dims(row.Code);
            // 部门可由人员带出、项目大类可由科目带出（GlCheck.Aux），这两列不要求。
            int[] need = new int[] { dims[1] ? 1 : 0, 2, 3, 5 };
            foreach (int k in need)
            {
                bool wanted = k == 0 ? dims[0] : dims[k];
                if (wanted && aux[k].Length == 0)
                {
                    return null;
                }
            }
            return aux;
        }

        // 公式里唯一一个带辅助项的 QM；没有或多于一个时 null。
        static GlQm Single(GlFormula f)
        {
            GlQm found = null;
            foreach (GlQm qm in f.Atoms)
            {
                if (qm.Aux.Length == 0)
                {
                    continue;
                }
                if (found != null)
                {
                    return null;
                }
                found = qm;
            }
            return found;
        }

        // 组一张凭证；全部为 0 时返回 null。
        // 取数的余额行先过数据权限（GlTransferPerm.Rows），再计算。
        public static GlTransferVoucher Voucher(object conn, GlTransferPlan plan, GlCustomDef def, GlTransferCodes codes)
        {
            HashSet<string> qmCodes = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (GlFormula f in def.Formulas)
            {
                foreach (GlQm qm in f.Atoms)
                {
                    qmCodes.Add(qm.Code);
                }
            }
            List<GlTransferBalRow> rows = GlTransferBal.Codes(conn, plan.Ask, qmCodes, def.Digest);
            GlTransferPerm.Rows(plan.Perm, rows, codes);
            decimal[] amounts = new decimal[def.Rows.Count];
            int ce = -1;
            for (int i = 0; i < def.Rows.Count; i++)
            {
                GlFormula f = def.Formulas[i];
                if (f.Ce)
                {
                    ce = i;
                    continue;
                }
                amounts[i] = Round(GlTransferFormula.Eval(f.Root, Values(f, rows, codes)));
            }
            if (ce >= 0)
            {
                amounts[ce] = Balance(def, amounts, ce);
            }
            return Build(def, amounts, codes);
        }

        static decimal[] Values(GlFormula f, List<GlTransferBalRow> rows, GlTransferCodes codes)
        {
            decimal[] values = new decimal[f.Atoms.Count];
            for (int i = 0; i < values.Length; i++)
            {
                values[i] = Qm(f.Atoms[i], rows, codes);
            }
            return values;
        }

        internal static decimal Qm(GlQm qm, List<GlTransferBalRow> rows, GlTransferCodes codes)
        {
            int kind = qm.Aux.Length > 0 ? Kind(codes, qm.Code) : -1;
            decimal d = 0;
            foreach (GlTransferBalRow row in rows)
            {
                if (!row.Code.StartsWith(qm.Code, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                if (kind >= 0 && !row.Aux[kind].Equals(qm.Aux, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                d += row.Value;
            }
            if (qm.Side == "借")
            {
                return d > 0 ? d : 0;
            }
            if (qm.Side == "贷")
            {
                return d < 0 ? -d : 0;
            }
            return codes.DebitNature(qm.Code) ? d : -d;
        }

        // CE()：其余各行借方合计与贷方合计之差，放在本行的方向上。
        internal static decimal Balance(GlCustomDef def, decimal[] amounts, int ce)
        {
            decimal debit = 0;
            decimal credit = 0;
            for (int i = 0; i < amounts.Length; i++)
            {
                if (i == ce)
                {
                    continue;
                }
                if (def.Rows[i].Debit)
                {
                    debit += amounts[i];
                }
                else
                {
                    credit += amounts[i];
                }
            }
            return def.Rows[ce].Debit ? credit - debit : debit - credit;
        }

        static GlTransferVoucher Build(GlCustomDef def, decimal[] amounts, GlTransferCodes codes)
        {
            GlTransferVoucher v = new GlTransferVoucher();
            v.Sign = def.Sign;
            v.TranId = def.TranId;
            v.Digest = def.Digest;
            v.Draft = new GlDraft();
            v.Draft.Sign = def.Sign;
            v.Draft.Date = "";
            for (int i = 0; i < def.Rows.Count; i++)
            {
                if (amounts[i] == 0)
                {
                    continue;
                }
                GlTransferDef row = def.Rows[i];
                string digest = row.Digest.Length > 0 ? row.Digest : def.Digest;
                v.Draft.Lines.Add(GlTransferLines.Line(row.Code, digest, row.Debit, amounts[i], LineAux(def, i, codes)));
            }
            return v.Draft.Lines.Count == 0 ? null : v;
        }

        static decimal Round(decimal value)
        {
            return decimal.Round(value, 2, MidpointRounding.AwayFromZero);
        }
    }
}
