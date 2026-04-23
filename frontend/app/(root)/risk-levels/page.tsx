"use client";

import HeaderBox from "@/components/HeaderBox";
import PortfolioTable from "@/components/risk-levels/Portfolio";
import PendingOrdersTable from "@/components/risk-levels/PendingOrders";
import FillsTable from "@/components/risk-levels/FillsTable";


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

        {/*  Pending orders (manual), IB fills and open portfolio */}
        <div>
          <PendingOrdersTable></PendingOrdersTable>
          <FillsTable></FillsTable>
          <PortfolioTable></PortfolioTable>
        </div>
      </div>

    </section>
  );
};

export default Risklevels;
