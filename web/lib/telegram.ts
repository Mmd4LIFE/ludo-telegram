// Telegram Mini App bridge. Reads the injected WebApp SDK (window.Telegram.WebApp),
// exposes initData for auth, deep-link start params, haptics and a native share sheet.
//
// In a plain browser (no Telegram) initData is empty; use /api/auth/dev locally.

export interface TelegramWebApp {
  initData: string;
  initDataUnsafe?: { start_param?: string };
  colorScheme: "light" | "dark";
  expand: () => void;
  ready: () => void;
  HapticFeedback?: {
    impactOccurred: (s: string) => void;
    notificationOccurred: (s: string) => void;
  };
  openTelegramLink?: (url: string) => void;
  setHeaderColor?: (c: string) => void;
  setBackgroundColor?: (c: string) => void;
  disableVerticalSwipes?: () => void;
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
    w.setHeaderColor?.("#0e1320");
    w.setBackgroundColor?.("#0e1320");
    w.disableVerticalSwipes?.();
  } catch {
    /* ignore */
  }
}

export function getInitData(): string {
  return tg()?.initData ?? "";
}

/** Deep-link payload: from the SDK first, then the URL (?startapp / ?tgWebAppStartParam). */
export function startParam(): string | null {
  const p = tg()?.initDataUnsafe?.start_param;
  if (p) return p;
  if (typeof window !== "undefined") {
    const q = new URLSearchParams(window.location.search);
    return q.get("startapp") || q.get("tgWebAppStartParam") || q.get("code");
  }
  return null;
}

export function haptic(kind: "light" | "medium" | "heavy" | "rigid" | "soft" = "light"): void {
  try {
    tg()?.HapticFeedback?.impactOccurred(kind);
  } catch {
    /* ignore */
  }
}

export function notify(type: "success" | "warning" | "error" = "success"): void {
  try {
    tg()?.HapticFeedback?.notificationOccurred(type);
  } catch {
    /* ignore */
  }
}

export function openTelegramLink(url: string): void {
  const w = tg();
  if (w?.openTelegramLink) w.openTelegramLink(url);
  else if (typeof window !== "undefined") window.open(url, "_blank");
}

/** Deep link that lands a friend in the bot chat (so they become reachable) then the room. */
export function roomInviteLink(botUsername: string, code: string): string {
  return `https://t.me/${botUsername}?start=rm-${code}`;
}

/** Open Telegram's native share sheet with the room invite. */
export function shareRoom(botUsername: string, code: string, text: string): void {
  const url = roomInviteLink(botUsername, code);
  openTelegramLink(
    `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`
  );
}
