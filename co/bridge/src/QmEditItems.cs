using System;
using System.Collections.Generic;

namespace U8Co
{
    // 检验单修改的检验项目（head.items）：按（检验项目, 检验指标）对到单据已有的检验项目行，覆盖检验值、单项判定；
    // 不增不删行，对不上 400。单项判定变了时把这一行的抽检量挪到指标合格数或不合格数（同生单 QmItems：U8 审核时核对
    // 抽检量 = 指标合格数 + 指标不合格数）。改过的行 editprop=M，没改的行不动。
    internal static class QmEditItems
    {
        public static void Apply(QmEditJob job, object body, bool vo)
        {
            List<object> rows = DomRows.RowsOf(body);
            try
            {
                List<string> schema = vo ? null : DomRows.Schema(body);
                for (int i = 0; i < job.Ask.Items.Count; i++)
                {
                    QmItemAsk item = job.Ask.Items[i];
                    object row = Find(rows, item);
                    if (row == null)
                    {
                        throw BridgeException.BadField(FieldPath.Item("head.items", i),
                            "单据中没有检验项目 " + item.ItemCode + " 指标 " + item.GuideCode);
                    }
                    Edit(body, row, schema, item);
                }
            }
            finally
            {
                QmEditDom.Release(rows);
            }
        }

        static object Find(List<object> rows, QmItemAsk item)
        {
            string key = QmItems.Key(item.ItemCode, item.GuideCode);
            for (int i = 0; i < rows.Count; i++)
            {
                string got = QmItems.Key(DomRows.Get(rows[i], "CCHKITEMCODE"), DomRows.Get(rows[i], "CCHKGUIDECODE"));
                if (string.Equals(got, key, StringComparison.OrdinalIgnoreCase))
                {
                    return rows[i];
                }
            }
            return null;
        }

        static void Edit(object dom, object row, List<string> schema, QmItemAsk item)
        {
            if (item.Value != null)
            {
                QmEditDom.Write(dom, row, schema, "CCHECKVALUE", item.Value);
            }
            string old = DomRows.Get(row, "CTARGETQJUG").Trim();
            if (item.Judge != null && item.Judge != old)
            {
                QmEditDom.Write(dom, row, schema, "CTARGETQJUG", item.Judge);
                Move(dom, row, schema, item.Judge == QmReq.Qualified);
            }
            QmEditDom.Write(dom, row, schema, "editprop", QmEditDom.Modified);
        }

        // 抽检量：行上的 FDTQUANTITYB，没有时取原来的指标合格数 + 指标不合格数。
        static void Move(object dom, object row, List<string> schema, bool qualified)
        {
            decimal reg = QmSql.Dec(DomRows.Get(row, "FQUIDEREGQUANTITY"));
            decimal dis = QmSql.Dec(DomRows.Get(row, "FQUIDEDISQUANTITY"));
            decimal dt = QmSql.Dec(DomRows.Get(row, "FDTQUANTITYB"));
            string sample = QmSql.Num(dt > 0m ? dt : reg + dis);
            QmEditDom.Write(dom, row, schema, "FQUIDEREGQUANTITY", qualified ? sample : "0");
            QmEditDom.Write(dom, row, schema, "FQUIDEDISQUANTITY", qualified ? "0" : sample);
        }

        // 保存后核对：每个请求项在 QMCHECKVOUCHERS 回读行里的检验值、单项判定；返回不符的说明。
        internal static List<string> Diff(QmEditJob job, List<Dictionary<string, object>> got)
        {
            List<string> diff = new List<string>();
            if (job.Ask.Items == null)
            {
                return diff;
            }
            for (int i = 0; i < job.Ask.Items.Count; i++)
            {
                QmItemAsk item = job.Ask.Items[i];
                Dictionary<string, object> row = Row(got, item);
                string label = "检验项目 " + item.ItemCode + "/" + item.GuideCode;
                if (row == null)
                {
                    diff.Add(label + " 不在");
                    continue;
                }
                if (item.Value != null && !QmEditSaved.Same(item.Value, CoRows.Col(row, "CCHECKVALUE")))
                {
                    diff.Add(label + " 检验值");
                }
                if (item.Judge != null && !QmEditSaved.Same(item.Judge, CoRows.Col(row, "CTARGETQJUG")))
                {
                    diff.Add(label + " 单项判定");
                }
            }
            return diff;
        }

        static Dictionary<string, object> Row(List<Dictionary<string, object>> rows, QmItemAsk item)
        {
            string key = QmItems.Key(item.ItemCode, item.GuideCode);
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                string got = QmItems.Key(CoRows.Col(rows[i], "CCHKITEMCODE"), CoRows.Col(rows[i], "CCHKGUIDECODE"));
                if (string.Equals(got, key, StringComparison.OrdinalIgnoreCase))
                {
                    return rows[i];
                }
            }
            return null;
        }
    }
}
