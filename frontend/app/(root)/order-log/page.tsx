"use client";

import HeaderBox from "@/components/HeaderBox";
import OrderLogTable from "@/components/order-log/OrderLogTable";

const OrderLogPage = () => {
  return (
    <section className="home">
      <div className="home-content">
        <header className="home-header">
          <HeaderBox
            type="greeting"
            title="Order log"
            subtext="Every order status transition since the backend started"
          />
        </header>

        <div>
          <OrderLogTable />
        </div>
      </div>
    </section>
  );
};

export default OrderLogPage;
