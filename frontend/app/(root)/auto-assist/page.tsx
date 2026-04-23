"use client";

import HeaderBox from "@/components/HeaderBox";
import AutoAssistPanel from "@/components/auto-assist/AutoAssistPanel";

const AutoAssist = () => {
  return (
    <section className="home">
      <div className="home-content">
        {/* Page header */}
        <header className="home-header">
          <HeaderBox
            type="greeting"
            title="Auto Assist"
            subtext="Live tick-driven breakout assistant"
          />
        </header>

        <div>
          <AutoAssistPanel />
        </div>
      </div>
    </section>
  );
};

export default AutoAssist;
