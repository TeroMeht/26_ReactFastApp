"use client";

import HeaderBox from "@/components/HeaderBox";
import PortfolioTable from "@/components/risk-levels/Portfolio";
import PendingOrdersTable from "@/components/risk-levels/PendingOrders";


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

        {/*  Show Alpaca pending orders table first */}
        <div className="mt-6">
          <PendingOrdersTable></PendingOrdersTable>
          <PortfolioTable></PortfolioTable>
        </div>

      </div>
      
    </section>
  );
};

export default Risklevels;
