"use client";

import { useState } from "react";

import HeaderBox from "@/components/HeaderBox";
import TradeLogTable from "@/components/trade-log/TradeLogTable";
import { Button } from "@/components/ui/button";

const TradeLogPage = () => {
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [loading, setLoading] = useState(false);

  return (
    <section className="home">
      <div className="home-content">
        <header className="home-header">
          <HeaderBox
            type="greeting"
            title="Trade log"
            subtext="Today's closed positions with realized PnL"
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
          <TradeLogTable
            refreshSignal={refreshSignal}
            onLoadingChange={setLoading}
          />
        </div>
      </div>
    </section>
  );
};

export default TradeLogPage;
