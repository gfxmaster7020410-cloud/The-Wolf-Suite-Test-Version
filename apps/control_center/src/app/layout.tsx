import type { Metadata } from "next";
import { baseNeue } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "Wolf Suite",
  description: "Wolf Suite — unified command screen for all automation modules",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={baseNeue.variable}>
      <body className="font-body antialiased font-normal text-slate-100">
        <div className="min-h-screen">
          <header className="border-b border-white/10 px-6 md:px-10 py-5 flex items-center justify-between gap-4">
            <a href="/" className="group">
              <p className="font-display text-2xl md:text-3xl font-semibold tracking-tight text-wolf-amber group-hover:text-white transition">
                Wolf Suite
              </p>
              <p className="font-label text-[11px] font-medium text-wolf-mist mt-0.5">
                Unified command · All modules
              </p>
            </a>
            <nav className="flex gap-4 md:gap-6 text-sm font-medium text-wolf-mist">
              <a href="/" className="hover:text-white transition">
                Main
              </a>
              <a href="/hitl" className="hover:text-white transition">
                HITL
              </a>
              <a href="/kill-switch" className="hover:text-white transition">
                Kill Switch
              </a>
            </nav>
          </header>
          <main className="px-6 md:px-10 py-8 max-w-7xl mx-auto">{children}</main>
        </div>
      </body>
    </html>
  );
}
