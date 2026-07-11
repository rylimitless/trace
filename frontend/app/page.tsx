export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <div className="bg-white border-2 border-primary rounded-card shadow-hard active-press px-8 py-6 -rotate-1">
        <h1 className="text-3xl font-bold text-primary">TRACE</h1>
        <p className="mt-2 text-lg text-primary">
          Scaffold + Hand-Drawn tokens are wired up.
        </p>
        <span className="mt-4 inline-block text-accent font-bold">
          accent color check
        </span>
      </div>
    </main>
  );
}
