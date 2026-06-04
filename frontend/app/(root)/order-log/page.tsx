"use client";

import { useState } from "react";

import HeaderBox from "@/components/HeaderBox";
import OrderLogTable from "@/components/order-log/OrderLogTable";
import { Button } from "@/components/ui/button";

const OrderLogPage = () => {
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [loading, setLoading] = useState(false);

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

        <div className="flex items-center gap-3 py-2">
          <Button
            variant="outline"
            onClick={() => setRefreshSignal((n) => n + 1)}
            disabled={loading}
          >
            {loading ? "Refreshing..." : "Refresh"}
          </Button>
        </div>

        <div>
          <OrderLogTable
            refreshSignal={refreshSignal}
            onLoadingChange={setLoading}
          />
        </div>
      </div>
    </section>
  );
};

export default OrderLogPage;
