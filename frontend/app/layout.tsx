import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "工作台 | IT 采购协作台",
  description: "企业 IT 资产管理与采购决策工作台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
