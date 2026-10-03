using System;
using System.Collections.Generic;

namespace U8Co
{
    // 检验单表体 = 检验项目行（至少 1 行，否则 U8「表体行数必须大于0行！」），每个检验方案行（QMCHECKPROJECTS）一行。
    // 缺省检验值 = 标准值 CSTANDARD、单项判定「合格」；请求的 items 按（检验项目, 检验指标）对到方案行覆盖，对不上 400。
    // 计量单位组与计量单位成对写（只写一个 U8 报「计量单位组或计量单位有一项不为空，则另一项也不能为空！」）。
    internal static class QmItems
    {
        public static void Fill(QmChkJob job, object dom)
        {
            Dictionary<string, QmItemAsk> asked = Asked(job);
            for (int i = 0; i < job.ProjectLines.Count; i++)
            {
                Dictionary<string, object> src = job.ProjectLines[i];
                QmItemAsk ask;
                asked.TryGetValue(Key(CoRows.Col(src, "CCHKITEMCODE"), CoRows.Col(src, "CCHKGUIDECODE")), out ask);
                using (QmRow r = QmRow.Add(dom))
                {
                    Row(r, job, src, ask);
                }
            }
        }

        // 请求的 items 必须都在方案里（QmOthChkDom 同样用它）。
        internal static Dictionary<string, QmItemAsk> Asked(QmChkJob job)
        {
            Dictionary<string, QmItemAsk> map = new Dictionary<string, QmItemAsk>(StringComparer.OrdinalIgnoreCase);
            if (job.Ask.Items == null)
            {
                return map;
            }
            HashSet<string> scheme = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < job.ProjectLines.Count; i++)
            {
                scheme.Add(Key(CoRows.Col(job.ProjectLines[i], "CCHKITEMCODE"), CoRows.Col(job.ProjectLines[i], "CCHKGUIDECODE")));
            }
            for (int i = 0; i < job.Ask.Items.Count; i++)
            {
                QmItemAsk item = job.Ask.Items[i];
                string key = Key(item.ItemCode, item.GuideCode);
                if (!scheme.Contains(key))
                {
                    throw new BridgeException(400, "bad_request", "检验方案中没有检验项目 " + item.ItemCode + " 指标 " + item.GuideCode);
                }
                map[key] = item;
            }
            return map;
        }

        static void Row(QmRow r, QmChkJob job, Dictionary<string, object> src, QmItemAsk ask)
        {
            string standard = CoRows.Col(src, "CSTANDARD");
            string dt = QmSql.Num(job.Dt);
            r.Put("CCHKITEMCODE", CoRows.Col(src, "CCHKITEMCODE"));
            r.Put("CCHKGUIDECODE", CoRows.Col(src, "CCHKGUIDECODE"));
            r.Put("CSTANDARD", standard);
            r.Put("CGUIDEUNIT", CoRows.Col(src, "CGUIDEUNIT"));
            r.Put("BGUIDETYPE", CoRows.Col(src, "BGUIDETYPE"));
            r.Put("CCHECKVALUE", ask != null && ask.Value != null ? ask.Value : standard);
            string judge = ask != null && ask.Judge != null ? ask.Judge : QmReq.Qualified;
            r.Put("CTARGETQJUG", judge);
            // 每行抽检量按单项判定记为指标合格数或不合格数，审核时 U8 核对两者之和。
            r.Put(judge == QmReq.Qualified ? "FQUIDEREGQUANTITY" : "FQUIDEDISQUANTITY", dt);
            r.Put("IDTMETHODB", Or(CoRows.Col(src, "IDTMETHOD"), "1"));
            r.Put("FCHKQUANTITY", dt);
            r.Put("FCHKVALIDQTY", dt);
            r.Put("FTARGETQTY", dt);
            r.Put("FDTQUANTITYB", dt);
            r.Put("FGQUANTITY", job.LineAsk.Qty);
            r.Put("FGCHANGERATE", "1");
            Unit(r, job);
            r.Put("DCHECKDATE", job.Date);
            r.Put("CCHECKTIME", job.Time);
            r.Put("BMUSTCHECK", Or(CoRows.Col(src, "BMUSTCHECK"), "0"));
            r.Put("CBUGGRADE", Or(CoRows.Col(src, "CBUGGRADE"), "0"));
            r.Raw("editprop", QmDom.Added);
        }

        static void Unit(QmRow r, QmChkJob job)
        {
            string group = CoRows.Col(job.Line, "cGroupCode");
            string unit = CoRows.Col(job.Line, "cComUnitCode");
            if (group.Length == 0 || unit.Length == 0)
            {
                return;
            }
            r.Put("CGROUPCODE", group);
            r.Put("CGCOMUNITCODE", unit);
        }

        internal static string Key(string item, string guide)
        {
            return (item ?? "").Trim() + "\n" + (guide ?? "").Trim();
        }

        static string Or(string value, string fallback)
        {
            return value != null && value.Length > 0 ? value : fallback;
        }
    }
}
