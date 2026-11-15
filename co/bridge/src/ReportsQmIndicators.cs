using System;
using System.Collections.Generic;

namespace U8Co
{
    // 检验指标统计 qm_indicators：来料（QM03）或产品（QM04）检验单在日期区间内，按（存货、检验指标）汇总：
    // 检验行数、数值行的均值 / 最小 / 最大 / 样本标准差、不良行数、逐月走势和最近的不良批次。
    // 默认只含已审核的检验单（表头 CVERIFIER 非空）。数值只统计能解析成小数的检验值（文字结果只计行数）。
    // 明细行读进内存统计，超过 MaxLines 行时 400，请缩小日期或给存货条件。
    internal static class ReportsQmIndicators
    {
        internal const int MaxLines = 100000;

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            QmReportArgs q = ReportsQmReq.Parse(ReportsQmReq.IndicatorsName, ctx.Item.Body);
            string to = ReportsStockSql.DateOr(ctx, q.DateTo);
            ReportsQmReq.CheckRange(q.DateFrom, to);
            List<object> ps = new List<object>(new object[] { q.VoucherType, q.DateFrom, to });
            string sql = ReportsQmIndicatorsSql.Lines.Replace("{FILTER}", ReportsQmFilter.Build(ctx, q, ps));
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), MaxLines + 1);
            if (rows.Count > MaxLines)
            {
                throw GlReq.Bad("区间内检验明细超过 " + MaxLines + " 行，请缩小日期范围或指定存货", "date_from");
            }
            List<QmGroup> groups = Groups(rows);
            List<object> items = new List<object>();
            for (int i = 0; i < groups.Count && i < q.Limit; i++)
            {
                items.Add(Item(groups[i]));
            }
            Dictionary<string, object> body = Reports.Body();
            body["kind"] = q.Kind;
            body["date_from"] = q.DateFrom;
            body["date_to"] = to;
            body["include_unverified"] = q.Unverified;
            body["items"] = items;
            body["count"] = items.Count;
            body["truncated"] = groups.Count > q.Limit;
            return ApiResult.Ok(body);
        }

        // 按（存货、指标）归并明细行，再按存货编码、指标编码排序。
        internal static List<QmGroup> Groups(List<Dictionary<string, object>> rows)
        {
            Dictionary<string, QmGroup> map = new Dictionary<string, QmGroup>(StringComparer.Ordinal);
            List<QmGroup> groups = new List<QmGroup>();
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, object> row = rows[i];
                string key = GlSql.Col(row, "inv") + "\u001f" + GlSql.Col(row, "guide");
                QmGroup g;
                if (!map.TryGetValue(key, out g))
                {
                    g = Open(row);
                    map.Add(key, g);
                    groups.Add(g);
                }
                if (g.GuideUnit.Length == 0)
                {
                    g.GuideUnit = GlSql.Col(row, "guide_unit");
                }
                ReportsQmStat.Add(g, row);
            }
            ReportsQmStat.SortGroups(groups);
            return groups;
        }

        static QmGroup Open(Dictionary<string, object> row)
        {
            QmGroup g = new QmGroup();
            g.InvCode = GlSql.Col(row, "inv");
            g.InvName = GlSql.Col(row, "inv_name");
            g.GuideCode = GlSql.Col(row, "guide");
            g.GuideName = GlSql.Col(row, "guide_name");
            return g;
        }

        internal static Dictionary<string, object> Item(QmGroup g)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["inv_code"] = g.InvCode;
            item["inv_name"] = g.InvName.Length == 0 ? null : g.InvName;
            item["guide_code"] = g.GuideCode;
            item["guide_name"] = g.GuideName.Length == 0 ? null : g.GuideName;
            item["guide_unit"] = g.GuideUnit.Length == 0 ? null : g.GuideUnit;
            item["standard"] = ReportsQmStat.TopStandard(g.Standards);
            item["n"] = g.Lines;
            item["numeric_n"] = g.Values.Count;
            item["mean"] = ReportsQmStat.Round6(ReportsQmStat.Mean(g.Values), g.Values.Count);
            item["min"] = ReportsQmStat.Min(g.Values);
            item["max"] = ReportsQmStat.Max(g.Values);
            item["stdev"] = ReportsQmStat.Stdev(g.Values);
            item["bad_n"] = g.BadLines;
            item["months"] = Months(g);
            item["bad_batches"] = BadBatches(g);
            return item;
        }

        static List<object> Months(QmGroup g)
        {
            List<object> list = new List<object>();
            foreach (KeyValuePair<string, QmMonth> pair in g.Months)
            {
                QmMonth m = pair.Value;
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["month"] = pair.Key;
                row["n"] = m.N;
                row["mean"] = m.NumericN == 0 ? null : ReportsQmStat.Round6(m.Sum / m.NumericN, m.NumericN);
                row["bad_n"] = m.Bad;
                list.Add(row);
            }
            return list;
        }

        static List<object> BadBatches(QmGroup g)
        {
            List<object> list = new List<object>();
            List<QmBadBatch> newest = ReportsQmStat.NewestBad(g.Bad);
            for (int i = 0; i < newest.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["date"] = newest[i].Date;
                row["check_code"] = newest[i].Code;
                row["batch"] = newest[i].Batch.Length == 0 ? null : newest[i].Batch;
                row["value"] = newest[i].Value;
                list.Add(row);
            }
            return list;
        }
    }
}
