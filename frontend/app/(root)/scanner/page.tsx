import HeaderBox from '@/components/HeaderBox';
import { SequentialScannerTables } from '@/components/scanner/SequentialScanTables';

const Scanner = () => {
  const loggedIn = { firstName: 'Tero' };

  const scannerTables = [

    { title: "High Activity", endpoint: "/api/ib_scanner?preset=high_activity_scan" },
    { title: "Gap Up", endpoint: "/api/ib_scanner?preset=gap_up_scan" },
    { title: "Gap Down", endpoint: "/api/ib_scanner?preset=gap_down_scan" },
  ];

  return (
    <section className="home">
      <div className="home-content">
        <header className="home-header">
          <HeaderBox
            type="greeting"
            title="Welcome"
            user={loggedIn.firstName || 'Guest'}
            subtext="Manage and review performance of Mehtanen Capital trading business"
          />
        </header>

        <main className="home-main mt-6">
          <h2 className="text-lg font-semibold mb-2">IB Scanner Results</h2>
          <SequentialScannerTables tables={scannerTables} />
        </main>
      </div>
    </section>
  );
};

export default Scanner;