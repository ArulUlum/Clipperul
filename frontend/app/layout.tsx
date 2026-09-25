import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Clipperul",
  description: "Turn long videos into AI-generated short clips and schedule them across social media.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
