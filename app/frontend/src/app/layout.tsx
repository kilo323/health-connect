import type { Metadata } from "next";
import "./globals.css";

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
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
