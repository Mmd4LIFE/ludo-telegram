// Telegram Mini App bridge. Reads the injected WebApp SDK (window.Telegram.WebApp),
// exposes initData for auth and a couple of ergonomics (expand, theme, haptics).
//
// In a browser (no Telegram) initData is empty; use the /api/auth/dev endpoint locally.

export interface TelegramWebApp {
  initData: string;
  colorScheme: "light" | "dark";
  expand: () => void;
  ready: () => void;
  HapticFeedback?: { impactOccurred: (s: string) => void };
  themeParams?: Record<string, string>;
}

export function tg(): TelegramWebApp | null {
  if (typeof window === "undefined") return null;
  // @ts-expect-error injected by Telegram
  return window.Telegram?.WebApp ?? null;
}

export function initTelegram(): void {
  const w = tg();
  if (!w) return;
  try {
    w.ready();
    w.expand();
  } catch {
    /* ignore */
  }
}

export function getInitData(): string {
  return tg()?.initData ?? "";
}

export function haptic(kind: "light" | "medium" | "heavy" = "light"): void {
  try {
    tg()?.HapticFeedback?.impactOccurred(kind);
  } catch {
    /* ignore */
  }
}
