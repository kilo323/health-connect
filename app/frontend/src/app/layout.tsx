import type { Metadata } from "next";
import "./globals.css";
import SyncBanner from "@/components/SyncBanner";

export const metadata: Metadata = {
  title: "Health Tracker",
  description: "Track your health metrics, documents, and AI analysis",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <SyncBanner />
        {children}
      </body>
    </html>
  );
}
