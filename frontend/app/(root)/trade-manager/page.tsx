"use client";

import { useState, useCallback } from "react";

import HeaderBox from "@/components/HeaderBox";
import PortfolioTable from "@/components/trade-manager/Portfolio";
import PendingOrdersTable from "@/components/trade-manager/PendingOrders";
import EntryAttemptsTable from "@/components/trade-manager/EntryAttemptsTable";
import LiveOrders from "@/components/trade-manager/LiveOrders";



const TradeManager = () => {
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
                title="Trade Manager"
                subtext="Manage trading activities"
              />
            </div>

            <div className="w-80 shrink-0">
              <EntryAttemptsTable refreshTrigger={refreshTrigger} />
            </div>
          </div>
        </header>

        {/*  Pending manual orders on top, live IB order status below, portfolio last */}
        <div>
          <PendingOrdersTable onRefreshed={bumpRefresh} />
          <LiveOrders />
          <PortfolioTable />
        </div>
      </div>
    </section>
  );
};

export default TradeManager;
