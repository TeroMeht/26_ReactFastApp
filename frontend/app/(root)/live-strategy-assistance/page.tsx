'use client';
import { useState } from 'react';
import HeaderBox from "@/components/HeaderBox";
import RunScript from '@/components/live-strategy-assistance/RunScript';
import LastRowsTable from '@/components/live-strategy-assistance/RelatrTable';
import TickBoxAll from '@/components/live-strategy-assistance/TickBoxAll';


const Page = () => {
  const [, setSymbols] = useState<string[]>([]);

  // Handle symbols coming from RunScript
  const handleSymbolsChange = (newSymbols: string[]) => {
    const splitSymbols = newSymbols
      .map(s => s.split(',').map(s2 => s2.trim().toUpperCase()))
      .flat()
      .filter(Boolean);

    console.log("Symbols received:", splitSymbols);
    setSymbols(splitSymbols);
  };

  return (
    <section className="home">
      <div className="home-content">
        <header className="home-header mb-6">
          <HeaderBox
            type="greeting"
            title="Live Strategy Assistance"
            subtext=""
          />
        </header>

        {/* Flex row for RunScript and TablesList */}
        <div className="flex flex-col md:flex-row gap-6">
          {/* Left side: RunScript */}
          <div className="flex-1">
            <RunScript onSymbolsChange={handleSymbolsChange} />
          </div>

          {/* Right side: TickBoxAll */}
          <div className="w-full md:w-80">
            <TickBoxAll />
          </div>
        </div>

        {/* LastRowsTable below the flex row */}
        <div className="mt-6">
          <LastRowsTable />
        </div>
      </div>
    </section>
  );
};

export default Page;