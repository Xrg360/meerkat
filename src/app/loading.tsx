export default function Loading() {
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo skeleton" />
          <div>
            <div className="skeleton sk-line medium" />
            <div className="skeleton sk-line short" />
          </div>
        </div>
      </aside>
      <main className="content">
        <div className="grid summary">
          {Array.from({ length: 4 }).map((_, index) => (
            <section className="card stat" key={index}>
              <div className="skeleton sk-line short" />
              <div className="skeleton sk-value" />
              <div className="skeleton sk-line medium" />
            </section>
          ))}
        </div>
      </main>
    </div>
  );
}
