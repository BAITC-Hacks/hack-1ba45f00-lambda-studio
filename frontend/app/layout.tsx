import type { Metadata } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import './globals.css';
export const metadata: Metadata = { title: 'Грибница — анализ сети', description: 'AML-анализ сети переводов' };
const inter = Inter({ subsets: ['cyrillic', 'latin'], variable: '--font-inter' });
const mono = JetBrains_Mono({ subsets: ['cyrillic', 'latin'], variable: '--font-mono' });
export default function RootLayout({ children }: { children: React.ReactNode }) { return <html lang="ru"><body className={`${inter.variable} ${mono.variable}`}>{children}</body></html>; }
