import HeaderBox from '@/components/HeaderBox';


const Home = () => {
  const loggedIn = { firstName: 'Tero' };



  return (
    <section className="home">
      <div className="home-content">
        <header className="home-header">
          <HeaderBox
            type="greeting"
            title="Welcome"
            user={loggedIn.firstName || 'Guest'}
            subtext=""
          />
        </header>

      </div>
    </section>
  );
};

export default Home;