"use client";

import HeaderBox from "@/components/HeaderBox";
import PortfolioTable from "@/components/risk-levels/Portfolio";
import PendingOrdersTable from "@/components/risk-levels/PendingOrders";
import AutoAssistPanel from "@/components/risk-levels/AutoAssistPanel";


const Risklevels = () => {
  return (
    <section className="home">
      <div className="home-content">
        {/* Page header */}
        <header className="home-header">
          <HeaderBox
            type="greeting"
            title="Risk levels"
            subtext="Create transparency to the risk you are taking"
          />
        </header>

        {/*  Pending orders (manual) and open portfolio */}
        <div className="mt-6">
          <PendingOrdersTable></PendingOrdersTable>
          <PortfolioTable></PortfolioTable>
        </div>
        {/* Auto Assist — live tick-driven breakout assistant */}
        <div className="mt-6">
          <AutoAssistPanel />
        </div>
      </div>

    </section>
  );
};

export default Risklevels;
