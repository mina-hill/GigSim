import "@fontsource/instrument-serif/latin-400.css";
import "@fontsource-variable/inter";
import "./globals.css";

import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "GigBid · Simulator workspace",
  description: "Review simulated freelance jobs, evidence and proposals in GigBid.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
