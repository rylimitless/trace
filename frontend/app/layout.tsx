import type { Metadata } from "next";
import { Kalam } from "next/font/google";
import { MantineProvider, ColorSchemeScript, mantineHtmlProps } from "@mantine/core";
import "./globals.css";

const kalam = Kalam({
  variable: "--font-kalam-google",
  subsets: ["latin"],
  weight: ["300", "400", "700"],
});

/* Resolves absolute URLs for social open-graph / twitter images and canonical
 * links. Set NEXT_PUBLIC_SITE_URL in production; falls back gracefully. */
const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: "TRACE | Catch Spoilage Early",
  description:
    "Quality-graded, fully traceable produce from Jamaican smallholder farms.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={kalam.variable} {...mantineHtmlProps}>
      <head>
        <ColorSchemeScript defaultColorScheme="light" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap"
        />
      </head>
      <body className="min-h-screen font-kalam" suppressHydrationWarning>
        <MantineProvider defaultColorScheme="light">
          {children}
        </MantineProvider>
      </body>
    </html>
  );
}
