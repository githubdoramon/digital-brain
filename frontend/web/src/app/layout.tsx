import type { Metadata } from "next";
import "./globals.css";
import { NavBar } from "@/components/NavBar";

const MAIN_MAX_WIDTH = 960;

export const metadata: Metadata = {
  title: "Digital Brain",
  description: "Personal memory orchestrator frontend",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        style={{
          backgroundColor: "#f5f5f5",
          color: "#111",
          fontFamily: "Inter, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
        }}
      >
        <NavBar />
        <main
          style={{
            maxWidth: `${MAIN_MAX_WIDTH}px`,
            margin: "0 auto",
            padding: "24px",
          }}
        >
          {children}
        </main>
      </body>
    </html>
  );
}
