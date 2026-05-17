import { Link, Outlet } from "react-router-dom";

export function Layout() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-edge bg-panel">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center gap-6">
          <Link to="/" className="font-bold tracking-tight mono">
            researchlab
          </Link>
          <nav className="text-slate-400 text-sm flex gap-4">
            <Link to="/" className="hover:text-slate-100">
              strategies
            </Link>
            <Link to="/portfolio" className="hover:text-slate-100">
              portfolio
            </Link>
            <Link to="/multistrat" className="hover:text-slate-100">
              multistrat
            </Link>
            <Link to="/features" className="hover:text-slate-100">
              features
            </Link>
            <Link to="/forward" className="hover:text-slate-100">
              forward
            </Link>
          </nav>
        </div>
      </header>
      <main className="max-w-6xl mx-auto w-full px-6 py-6 flex-1">
        <Outlet />
      </main>
    </div>
  );
}
