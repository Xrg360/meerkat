"use client";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="content">
      <section className="card">
        <div className="card-body">
          <h2>Dashboard failed to load</h2>
          <p className="muted">{error.message}</p>
          <button onClick={reset}>Try again</button>
        </div>
      </section>
    </main>
  );
}
