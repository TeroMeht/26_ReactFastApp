"use client";

import HeaderBox from "@/components/HeaderBox";
import Chart from "@/components/charts/ChartComponent";

const ChartPage = () => {
  return (
    <section className="home">
      <div className="home-content">

        <header className="home-header">
          <HeaderBox
            type="greeting"
            title="Chart viewer"
            subtext="Learning how to plot chart here"
          />
        </header>

        <Chart />

      </div>
    </section>
  );
};

export default ChartPage;