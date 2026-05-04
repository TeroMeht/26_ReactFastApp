"use client";

import { useState, useCallback } from "react";

import HeaderBox from "@/components/HeaderBox";
import PortfolioTable from "@/components/risk-levels/Portfolio";
import PendingOrdersTable from "@/components/risk-levels/PendingOrders";
import EntryAttemptsTable from "@/components/risk-levels/EntryAttemptsTable";
import FillsTable from "@/components/risk-levels/FillsTable";


const Risklevels = () => {
  // Shared refresh signal between PendingOrders' Refresh button and the
  // EntryAttemptsTable that lives next to the page header.
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const bumpRefresh = useCallback(() => setRefreshTrigger((t) => t + 1), []);

  return (
    <section className="home">
      <div className="home-content">
        {/* Page header: title on the left, entry-attempts stats on the right */}
        <header className="home-header">
          <div className="flex flex-row items-start justify-between gap-6">
            <div className="flex-1 min-w-0">
              <HeaderBox
                type="greeting"
                title="Risk levels"
                subtext="Create transparency to the risk you are taking"
              />
            </div>

            <div className="w-80 shrink-0">
              <EntryAttemptsTable refreshTrigger={refreshTrigger} />
            </div>
          </div>
        </header>

        {/*  Pending orders (manual), IB fills and open portfolio */}
        <div>
          <PendingOrdersTable onRefreshed={bumpRefresh} />
          <PortfolioTable />
          {/* <FillsTable /> */}
        </div>
      </div>
    </section>
  );
};

export default Risklevels;
