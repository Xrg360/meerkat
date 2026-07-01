import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Meerkat Monitor",
  description: "Uptime and homelab monitoring dashboard",
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
