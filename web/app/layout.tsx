import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "行迹 · 把想去的地方，变成有据可查的行程",
  description: "通过对话规划旅行，查看行程、来源与待核实事项。",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
