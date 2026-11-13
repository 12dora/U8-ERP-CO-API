using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的成对设置来源解析部分（VoucherSoContractLinks）：发货单 / 出库单 / 发票表体行的来源订单（直接、发货单、
    // 两条一致与冲突、iSOsID=0 走发货单、悬空、红冲蓝字一跳与缺失 / 自指）、来源集合与「指向本订单」、关联 SQL 的锁和 NULLIF、
    // 来源附着（本单、找不到表头的订单）、下游列宽 400 和每张单据 200 张来源订单的上限。不连库。由 VoucherSoContractPairSelfTest.Run 调用。
    internal static class VoucherSoContractLinksSelfTest
    {
        public static void Run()
        {
            CheckHop();
            CheckInvoice();
            CheckBlue();
            CheckSources();
            CheckSql();
            CheckAttach();
            CheckWidths();
            CheckSourceCap();
        }

        // 每张下游单据的不同来源订单：200 张放行、201 张 409；重复行只算一张，无来源 0 不计。
        static void CheckSourceCap()
        {
            List<SoDownLine> lines = new List<SoDownLine>();
            for (int i = 1; i <= 200; i++)
            {
                lines.Add(Line(i, i, 0, 0, 0, 0));
                lines.Add(Line(i, i, 0, 0, 0, 0));
            }
            lines.Add(Line(0, 0, 0, 0, 0, 0));
            bool mentions;
            List<int> ids = VoucherSoContractLinks.SourcesOf(lines, 1, out mentions);
            Expect("cap 200 distinct", ids.Count == 201 && ids[0] == 0);
            VoucherSoContractDown.CheckSourceCap(ids, 11);
            lines.Add(Line(201, 201, 0, 0, 0, 0));
            ids = VoucherSoContractLinks.SourcesOf(lines, 1, out mentions);
            try
            {
                VoucherSoContractDown.CheckSourceCap(ids, 11);
            }
            catch (BridgeException ex)
            {
                Expect("cap 201", ex.Status == 409 && ex.Code == "too_many_downstream");
                return;
            }
            throw new InvalidOperationException("so_contract links source cap not refused");
        }

        static void CheckHop()
        {
            Eq("hop none", VoucherSoContractLinks.Hop(0, 0, 0, 0, 0, 0), 0);
            Eq("hop direct", VoucherSoContractLinks.Hop(11, 7, 0, 0, 0, 0), 7);
            Eq("hop direct dangling", VoucherSoContractLinks.Hop(11, 0, 0, 0, 0, 0), -1);
            Eq("hop dispatch", VoucherSoContractLinks.Hop(0, 0, 21, 21, 11, 7), 7);
            Eq("hop dispatch dangling", VoucherSoContractLinks.Hop(0, 0, 21, 0, 0, 0), -1);
            Eq("hop dispatch without order", VoucherSoContractLinks.Hop(0, 0, 21, 21, 0, 0), -1);
            Eq("hop agree", VoucherSoContractLinks.Hop(11, 7, 21, 21, 11, 7), 7);
            Eq("hop conflict", VoucherSoContractLinks.Hop(11, 7, 21, 21, 12, 8), -1);
            Eq("hop direct ok dispatch dangling", VoucherSoContractLinks.Hop(11, 7, 21, 0, 0, 0), -1);
        }

        // 发票行：iSOsID=0（NULLIF 后为 0）且有发货单行 → 走发货单；两条指向不同订单 → 冲突无来源，但仍「指向」两张订单。
        static void CheckInvoice()
        {
            SoDownLine zero = Line(0, 0, 21, 21, 11, 7);
            Eq("invoice zero falls back to dispatch", VoucherSoContractLinks.Resolve(zero), 7);
            SoDownLine conflict = Line(11, 7, 21, 21, 12, 8);
            Eq("invoice conflict no source", VoucherSoContractLinks.Resolve(conflict), 0);
            Expect("invoice conflict mentions both", VoucherSoContractLinks.Mentions(conflict, 7)
                && VoucherSoContractLinks.Mentions(conflict, 8) && !VoucherSoContractLinks.Mentions(conflict, 9));
            Eq("invoice agree", VoucherSoContractLinks.Resolve(Line(11, 7, 21, 21, 11, 7)), 7);
            Eq("invoice no links", VoucherSoContractLinks.Resolve(Line(0, 0, 0, 0, 0, 0)), 0);
        }

        // 红冲蓝字：直接和发货单两条都没有时经 iSBVID 一跳到蓝字行；蓝字行找不到（含自指被 SQL 排除）、蓝字行自己没有来源 → 无来源。
        static void CheckBlue()
        {
            SoDownLine red = Line(0, 0, 0, 0, 0, 0);
            red.Bl = 31;
            red.Bf = 31;
            red.D3 = 11;
            red.O3 = 7;
            Eq("blue direct", VoucherSoContractLinks.Resolve(red), 7);
            Expect("blue mentions", VoucherSoContractLinks.Mentions(red, 7));
            red.D3 = 0;
            red.O3 = 0;
            red.Dl3 = 21;
            red.Lf3 = 21;
            red.D4 = 11;
            red.O4 = 7;
            Eq("blue via dispatch", VoucherSoContractLinks.Resolve(red), 7);
            red.Bf = 0;
            Eq("blue missing", VoucherSoContractLinks.Resolve(red), 0);
            SoDownLine bare = Line(0, 0, 0, 0, 0, 0);
            bare.Bl = 31;
            bare.Bf = 31;
            Eq("blue without source", VoucherSoContractLinks.Resolve(bare), 0);
            SoDownLine own = Line(11, 7, 0, 0, 0, 0);
            own.Bl = 31;
            own.Bf = 31;
            own.D3 = 12;
            own.O3 = 8;
            Eq("blue ignored when direct present", VoucherSoContractLinks.Resolve(own), 7);
        }

        static void CheckSources()
        {
            bool mentions;
            List<SoDownLine> lines = new List<SoDownLine>(new SoDownLine[]
            {
                Line(12, 8, 0, 0, 0, 0), Line(11, 7, 0, 0, 0, 0), Line(11, 7, 0, 0, 0, 0), Line(0, 0, 0, 0, 0, 0),
            });
            List<int> ids = VoucherSoContractLinks.SourcesOf(lines, 7, out mentions);
            Expect("sources sorted distinct", mentions && ids.Count == 3 && ids[0] == 0 && ids[1] == 7 && ids[2] == 8);
            VoucherSoContractLinks.SourcesOf(lines, 9, out mentions);
            Expect("sources not mentioned", !mentions);
            Expect("same ids", VoucherSoContractLinks.SameIds(ids, new List<int>(new int[] { 0, 7, 8 }))
                && !VoucherSoContractLinks.SameIds(ids, new List<int>(new int[] { 7, 8 })));
        }

        static void CheckSql()
        {
            SoDownKind invoice = VoucherSoContractLinks.Kinds[2];
            string find = VoucherSoContractLinks.FindSql(invoice);
            Expect("sql find refs", find.Contains("(s1.ID=? OR s2.ID=? OR s3.ID=? OR s4.ID=?)")
                && VoucherSoContractLinks.FindArgs(invoice, 7).Length == 4);
            Expect("sql nullif", invoice.From.Contains("s1.iSOsID=NULLIF(b.iSOsID,0)")
                && invoice.From.Contains("l.iDLsID=NULLIF(b.iDLsID,0)") && invoice.From.Contains("bb.AutoID=NULLIF(b.iSBVID,0)"));
            Expect("sql blue one hop", invoice.From.Contains("bb.AutoID<>b.AutoID")
                && invoice.From.Contains("NULLIF(b.iSOsID,0) IS NULL AND NULLIF(b.iDLsID,0) IS NULL"));
            foreach (SoDownKind kind in VoucherSoContractLinks.Kinds)
            {
                foreach (string alias in kind.Refs)
                {
                    Expect("sql hold order line " + kind.Type + " " + alias,
                        kind.From.Contains("JOIN SO_SODetails " + alias + " WITH (UPDLOCK, HOLDLOCK) ON"));
                }
                Expect("sql hold " + kind.Type, kind.From.Contains(" WITH (UPDLOCK, HOLDLOCK)")
                    && VoucherSoContractLinks.LinesSql(kind).EndsWith(kind.HeadKey + "=? ORDER BY line", StringComparison.Ordinal));
            }
        }

        static void CheckAttach()
        {
            SoDownDoc doc = new SoDownDoc();
            doc.SourceIds = new List<int>(new int[] { 0, 7, 8, 9 });
            Dictionary<int, SoDownSource> orders = new Dictionary<int, SoDownSource>();
            SoDownSource eight = new SoDownSource();
            eight.Id = 8;
            eight.Contract = "KH1";
            orders[8] = eight;
            VoucherSoContractDown.Attach(doc, orders, 7);
            Expect("attach", doc.Sources.Count == 4 && doc.Sources[0].Id == 0 && doc.Sources[1].Id == 7
                && doc.Sources[2].Contract == "KH1" && doc.Sources[3].Id == 0);
        }

        // 要改的下游列宽不够：400 bad_request，field 为请求字段，消息带单号；跳过的单据不核对。
        static void CheckWidths()
        {
            SoContractAsk ask = new SoContractAsk();
            ask.ContractNo = "KH-2026-0001";
            ask.CustomerNo = "HT202601001";
            SoDownDoc doc = new SoDownDoc();
            doc.Kind = VoucherSoContractLinks.Kinds[1];
            doc.Code = "CK0001";
            doc.Result = VoucherSoContractPair.Updated;
            doc.Limit1 = 12;
            doc.Limit2 = 11;
            List<SoDownDoc> docs = new List<SoDownDoc>(new SoDownDoc[] { doc });
            VoucherSoContractPair.CheckWidths(ask, docs);
            doc.Limit2 = 10;
            Wide("width internal", ask, docs, VoucherSoContract.InternalKey);
            doc.Limit1 = 5;
            Wide("width contract", ask, docs, VoucherSoContract.ContractKey);
            doc.Result = VoucherSoContractPair.Skipped;
            VoucherSoContractPair.CheckWidths(ask, docs);
        }

        static void Wide(string name, SoContractAsk ask, List<SoDownDoc> docs, string field)
        {
            try
            {
                VoucherSoContractPair.CheckWidths(ask, docs);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field && ex.Message.Contains("CK0001"));
                return;
            }
            throw new InvalidOperationException("so_contract links " + name + " not refused");
        }

        static SoDownLine Line(int d1, int o1, int dl, int lf, int d2, int o2)
        {
            SoDownLine x = new SoDownLine();
            x.D1 = d1;
            x.O1 = o1;
            x.Dl = dl;
            x.Lf = lf;
            x.D2 = d2;
            x.O2 = o2;
            return x;
        }

        static void Eq(string name, int got, int want)
        {
            Expect(name + " (" + got + " != " + want + ")", got == want);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("so_contract links " + name);
            }
        }
    }
}
