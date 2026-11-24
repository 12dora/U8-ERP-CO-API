using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的成本核算期间数据 ca_inputs 部分（不连库）：表定义、SQL 的 ? 与参数一一对应、参数校验与缺省、
    // 行的折叠（成本中心 / 成本核算对象 / 成本项目 / 数值列）、成本计算期间文本。
    internal static class CaInputsSelfTest
    {
        public static void Run()
        {
            CheckDefs();
            CheckSql();
            CheckParse();
            CheckRefused();
            CheckRows();
            CheckPermit();
            CheckStatus();
        }

        static readonly string[] Days = new string[] { "2026-10-01", "2026-11-01" };

        // 没指定表时只留有权限的表，其余进 skipped；指定表缺权限 403；一张都读不了 403。
        static void CheckPermit()
        {
            PermContext p = new PermContext();
            p.Funcs.Add("CA0303");
            p.Funcs.Add("CA0308");
            List<object> skipped = new List<object>();
            List<CaTable> got = ReportsCaInputs.Permit(p, ReportsCaInputsDef.Select(new string[0]), false, skipped);
            Expect("permit partial", got.Count == 3 && got[0].Name == "work_hours" && got[1].Name == "daily_hours" && got[2].Name == "labor"
                && skipped.Count == 6);
            Dictionary<string, object> first = (Dictionary<string, object>)skipped[0];
            Expect("permit skipped", (string)first["table"] == "overhead" && (string)first["reason"] == "no_permission");
            Expect("permit explicit", Denied(p, new string[] { "labor", "overhead" }, true));
            Expect("permit none", Denied(new PermContext(), new string[0], false));
            PermContext boss = new PermContext();
            boss.Supervisor = true;
            List<object> none = new List<object>();
            Expect("permit all", ReportsCaInputs.Permit(boss, ReportsCaInputsDef.Select(new string[0]), false, none).Count == 9 && none.Count == 0);
        }

        static bool Denied(PermContext p, string[] names, bool explicitTables)
        {
            try
            {
                ReportsCaInputs.Permit(p, ReportsCaInputsDef.Select(names), explicitTables, new List<object>());
            }
            catch (BridgeException ex)
            {
                return ex.Status == 403;
            }
            return false;
        }

        static void CheckDefs()
        {
            Expect("defs count", ReportsCaInputsDef.All.Length == 9);
            for (int i = 0; i < ReportsCaInputsDef.All.Length; i++)
            {
                CaTable t = ReportsCaInputsDef.All[i];
                Expect("def unique " + t.Name, ReportsCaInputsDef.Find(t.Name) == t);
                Expect("def fields " + t.Name, t.Title.Length > 0 && t.Table.StartsWith("CA_", StringComparison.Ordinal)
                    && t.Auths.Length > 0 && t.Order.StartsWith("t.", StringComparison.Ordinal));
            }
            Expect("def find none", ReportsCaInputsDef.Find("direct_cost") == null);
            Expect("def select all", ReportsCaInputsDef.Select(new string[0]).Count == 9);
            List<CaTable> two = ReportsCaInputsDef.Select(new string[] { "product_qty", "labor" });
            Expect("def select order", two.Count == 2 && two[0].Name == "labor" && two[1].Name == "product_qty");
            string[] ids = ReportsCaInputsDef.AllAuths();
            Expect("def auths", ids.Length == 7 && Array.IndexOf(ids, "CA0303") >= 0 && Array.IndexOf(ids, "CA0308") >= 0);
        }

        // 每张表的 SQL：? 个数等于参数个数，形状对应的连接和条件齐全。
        static void CheckSql()
        {
            for (int i = 0; i < ReportsCaInputsDef.All.Length; i++)
            {
                CheckTableSql(ReportsCaInputsDef.All[i]);
            }
            CheckSqlTime();
        }

        static void CheckTableSql(CaTable t)
        {
            StringBuilder sql = new StringBuilder();
            List<object> ps = new List<object>();
            ReportsCaInputsSql.Compose(t, sql, ps, 2026, 10, Days);
            string text = sql.ToString();
            Expect("sql marks " + t.Name, Marks(text) == ps.Count && text.StartsWith("SELECT t.*, COUNT(*) OVER() ca_n")
                && text.IndexOf(" FROM " + t.Table + " t") > 0);
            Expect("sql object " + t.Name, (text.IndexOf("Caq_CoReal") > 0) == (t.Shape == CaTable.Object)
                && (text.IndexOf("Caq_CoBase") > 0) == t.IsBase);
            Expect("sql center " + t.Name, (text.IndexOf("CA_CostCenter") > 0) == t.HasCenter
                && (text.IndexOf("CA_CostCD") > 0) == (t.Shape == CaTable.Item));
        }

        // 各时间条件的参数，制造费用的成本项目连接和排序。
        static void CheckSqlTime()
        {
            StringBuilder day = new StringBuilder();
            List<object> dayArgs = new List<object>();
            ReportsCaInputsSql.Compose(ReportsCaInputsDef.Find("daily_hours"), day, dayArgs, 2026, 2,
                new string[] { "2026-01-26", "2026-02-26" });
            Expect("sql date", dayArgs.Count == 2 && (string)dayArgs[0] == "2026-01-26" && (string)dayArgs[1] == "2026-02-26");
            StringBuilder year = new StringBuilder();
            List<object> yearArgs = new List<object>();
            ReportsCaInputsSql.Compose(ReportsCaInputsDef.Find("wip_fixed"), year, yearArgs, 2026, 10, null);
            Expect("sql year", yearArgs.Count == 1 && (int)yearArgs[0] == 2026 && year.ToString().EndsWith(ReportsCaInputsSql.WhereYear));
            StringBuilder month = new StringBuilder();
            List<object> monthArgs = new List<object>();
            ReportsCaInputsSql.Compose(ReportsCaInputsDef.Find("overhead"), month, monthArgs, 2026, 10, null);
            Expect("sql period", monthArgs.Count == 1 && (int)monthArgs[0] == 202610
                && month.ToString().IndexOf("i.cAmotype=" + ReportsCaInputsSql.OverheadElement) > 0);
            Expect("sql order", ReportsCaInputsDef.Find("overhead").Order.IndexOf("CONVERT(int, t.cAmoID)") > 0
                && ReportsCaInputsSql.NumericOrder("t.cAmoID").StartsWith("CASE WHEN LEN(t.cAmoID)"));
        }

        static void CheckParse()
        {
            CaInputsArgs none = ReportsCaParse.Inputs(new Dictionary<string, object>());
            Expect("parse default", none.Period == 0 && none.Tables.Length == 0 && none.Limit == 5000);
            Dictionary<string, object> body = One("period", 10);
            body["limit"] = 20000;
            body["tables"] = new object[] { " labor ", "overhead" };
            CaInputsArgs full = ReportsCaParse.Inputs(body);
            Expect("parse values", full.Period == 10 && full.Limit == 20000 && full.Tables.Length == 2 && full.Tables[0] == "labor");
        }

        static void CheckRefused()
        {
            Refused("period 0", One("period", 0), "period");
            Refused("period 13", One("period", 13), "period");
            Refused("period text", One("period", "9"), "period");
            Refused("limit 0", One("limit", 0), "limit");
            Refused("limit 20001", One("limit", 20001), "limit");
            Refused("tables empty", One("tables", new object[0]), "tables");
            Refused("tables text", One("tables", "labor"), "tables");
            Refused("tables too many", One("tables", new object[10]), "tables");
            Refused("tables unknown", One("tables", new object[] { "labor", "CA_DirMA" }), "tables.1");
            Refused("tables dup", One("tables", new object[] { "labor", "labor" }), "tables.1");
            Refused("tables number", One("tables", new object[] { 1 }), "tables.0");
        }

        static void CheckRows()
        {
            Dictionary<string, object> hours = Row("cPPID", "2232", "cBatch", "", "iPeriod", "202610", "iRealWkt", "12.5", "iOnpWkt", "0");
            hours["cRealWktType"] = "1";
            hours["ca_cc"] = "CC001";
            hours["ca_cc_name"] = "文本A";
            hours["ca_inv"] = "9909001";
            hours["ca_inv_name"] = "存货A";
            hours["ca_mo"] = "MO-001";
            Dictionary<string, object> item = ReportsCaInputs.Row(ReportsCaInputsDef.Find("work_hours"), hours);
            Dictionary<string, object> values = (Dictionary<string, object>)item["values"];
            Expect("row object", All((int)item["object_id"] == 2232, (string)item["period"] == "2026-10", (string)item["cost_center"] == "CC001",
                (string)item["cost_center_name"] == "文本A", (string)item["inv_code"] == "9909001", (string)item["mo_code"] == "MO-001",
                (string)item["type"] == "1", !item.ContainsKey("batch"), (decimal)values["iRealWkt"] == 12.5m, (decimal)values["iOnpWkt"] == 0m));
            Dictionary<string, object> lost = ReportsCaInputs.Row(ReportsCaInputsDef.Find("product_qty"),
                Row("cPPID", "9", "cBatch", "B1", "iPeriod", "202610", "iQua", "3.1234567"));
            Expect("row unresolved", All((string)lost["batch"] == "B1", !lost.ContainsKey("inv_code"), !lost.ContainsKey("cost_center"),
                (decimal)((Dictionary<string, object>)lost["values"])["iQua"] == 3.123457m,
                !((Dictionary<string, object>)lost["values"]).ContainsKey("iWasteQua")));
            CheckCenterRows();
        }

        static void CheckCenterRows()
        {
            Dictionary<string, object> dep = Row("cDeptID", "CC002", "iPeriod", "202609", "iDepreciation", "100.5", "cFrom", "  ");
            dep["ca_cc_name"] = "文本B";
            Dictionary<string, object> depItem = ReportsCaInputs.Row(ReportsCaInputsDef.Find("depreciation"), dep);
            Expect("row center", All((string)depItem["cost_center"] == "CC002", (string)depItem["cost_center_name"] == "文本B",
                !((Dictionary<string, object>)depItem["values"]).ContainsKey("cFrom"), !depItem.ContainsKey("object_id")));
            dep["cFrom"] = "FA";
            Expect("row text", (string)((Dictionary<string, object>)ReportsCaInputs.Row(ReportsCaInputsDef.Find("depreciation"), dep)["values"])["cFrom"] == "FA");
            Dictionary<string, object> over = Row("cDeptID", "CC001", "cAmoID", "4", "iPeriod", "202610", "imanAmo", "140");
            over["ca_item_name"] = "项目A";
            Dictionary<string, object> overItem = ReportsCaInputs.Row(ReportsCaInputsDef.Find("overhead"), over);
            Expect("row item", All((string)overItem["item_code"] == "4", (string)overItem["item_name"] == "项目A", !overItem.ContainsKey("element")));
            Dictionary<string, object> day = ReportsCaInputs.Row(ReportsCaInputsDef.Find("daily_hours"),
                Row("cPPID", "1", "dDate", "2026-10-31 00:00:00", "iRealWkt", "1"));
            Expect("row date", (string)day["date"] == "2026-10-31" && !day.ContainsKey("period"));
            Dictionary<string, object> year = ReportsCaInputs.Row(ReportsCaInputsDef.Find("wip_fixed"), Row("cPPID", "1", "iYear", "2026", "iQuot", "1"));
            Expect("row year", (int)year["year"] == 2026 && !year.ContainsKey("period"));
            Dictionary<string, object> baseRow = ReportsCaInputs.Row(ReportsCaInputsDef.Find("wip_fixed"),
                Row("cPPID", "-1", "iYear", "2026", "iQuot", "1"));
            Expect("row base", (int)baseRow["base_object_id"] == -1 && !baseRow.ContainsKey("object_id") && !baseRow.ContainsKey("inv_code")
                && !baseRow.ContainsKey("mo_code"));
            Dictionary<string, object> distr = Row("cPPID", "1", "cBatch", "", "iPeriod", "202610", "autoid", "7", "cprksl", "153", "iLastMOnp", "0.5");
            distr["ca_n"] = "1";
            distr["ca_inv"] = "9909001";
            distr["cNote"] = "文本A";
            Dictionary<string, object> distrValues = (Dictionary<string, object>)ReportsCaInputs.Row(ReportsCaInputsDef.Find("distr_input"), distr)["values"];
            Expect("row distr", distrValues.Count == 2 && (decimal)distrValues["cprksl"] == 153m && (decimal)distrValues["iLastMOnp"] == 0.5m);
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            Expect("table empty", (int)ReportsCaInputs.Table(ReportsCaInputsDef.Find("labor"), rows)["count"] == 0);
            Dictionary<string, object> counted = Row("cDeptID", "CC002", "iPeriod", "202610", "ca_n", "40");
            rows.Add(counted);
            Dictionary<string, object> table = ReportsCaInputs.Table(ReportsCaInputsDef.Find("labor"), rows);
            Expect("table count", (int)table["count"] == 40 && ((List<object>)table["rows"]).Count == 1);
        }

        static void CheckStatus()
        {
            Expect("calc text", All((string)ReportsCaInputs.CalculatedText("202610") == "2026-10", ReportsCaInputs.CalculatedText("0") == null,
                ReportsCaInputs.CalculatedText(null) == null, ReportsCaInputs.CalculatedText("202613") == null,
                ReportsCaInputs.CalculatedText("") == null));
        }

        // 行的键值对：偶数位是列名，奇数位是值。
        static Dictionary<string, object> Row(params string[] pairs)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                row[pairs[i]] = pairs[i + 1];
            }
            return row;
        }

        static Dictionary<string, object> One(string key, object value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[key] = value;
            return body;
        }

        static int Marks(string sql)
        {
            int n = 0;
            for (int i = 0; i < sql.Length; i++)
            {
                n += sql[i] == '?' ? 1 : 0;
            }
            return n;
        }

        static void Refused(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                ReportsAcctReq.Check("ca_inputs", body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("ca_inputs " + name);
        }

        static bool All(params bool[] checks)
        {
            for (int i = 0; i < checks.Length; i++)
            {
                if (!checks[i])
                {
                    return false;
                }
            }
            return true;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("ca_inputs " + name);
            }
        }
    }
}
