import type { Metadata, Viewport } from "next";
import AuthProvider from "@/components/AuthProvider";
import AppShell from "@/components/shell/AppShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "C-Suite",
  description: "Your AI-powered virtual executive team",
  // Installed to an iOS home screen, the app opens without Safari chrome and
  // takes its name from `title` here rather than from the manifest.
  appleWebApp: {
    capable: true,
    title: "C-Suite",
    statusBarStyle: "default",
  },
};

export const viewport: Viewport = {
  // `viewportFit: "cover"` lets the page paint under the notch and home
  // indicator — required for `env(safe-area-inset-*)` to report anything
  // other than zero. Without it the bottom tab bar sits under the home
  // indicator on every notched iPhone.
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f7f5" },
    { media: "(prefers-color-scheme: dark)", color: "#0b1220" },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full antialiased bg-surface text-fg">
        <AuthProvider>
          <AppShell>{children}</AppShell>
        </AuthProvider>
      </body>
    </html>
  );
}
