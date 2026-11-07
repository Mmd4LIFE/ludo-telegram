"use client";

import { useEffect, useState } from "react";
import {
  ArrowLeft,
  Bot,
  Plus,
  Sparkles,
  Wand2,
} from "lucide-react";
import {
  api,
  ArenaChampion,
  ArenaInfo,
  MatchLobby,
  MatchSummary,
  Profile,
} from "@/lib/api";
import { Card as CardDef, rarityColor } from "@/lib/cards";
import { haptic } from "@/lib/telegram";
import { FantasyHandCard, FantasyView } from "@/lib/ws";
import { Button } from "@/components/ui/button";
import { Card, SectionLabel } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const FALLBACK_CHAMPS: ArenaChampion[] = [
  { id: "knight", name: "Knight", icon: "🛡️", blurb: "Shields last one extra round. Starts with 2 mana." },
  { id: "rogue", name: "Rogue", icon: "🗡️", blurb: "Every capture gives +2 mana instead of +1." },
  { id: "sage", name: "Sage", icon: "📜", blurb: "Draws a card every 2nd turn instead of every 3rd." },
  { id: "trickster", name: "Trickster", icon: "🃏", blurb: "Targeted cards cost 1 mana less." },
];

const FALLBACK_EVENTS: ArenaChampion[] = [
  { id: "eclipse", name: "Eclipse", icon: "🌑", blurb: "Every neutral star is safe for everyone." },
  { id: "tailwind", name: "Tailwind", icon: "🌬️", blurb: "Every move is one step longer. Leaving the yard still needs a real six." },
  { id: "fog", name: "Fog", icon: "🌫️", blurb: "No captures this round." },
  { id: "harvest", name: "Harvest", icon: "🌾", blurb: "Everyone gains +2 mana." },
  { id: "quake", name: "Quake", icon: "🌋", blurb: "Every piece on the ring slips back two squares." },
];

export function FantasyHub({
  profile,
  busy,
  onBack,
  onPlayBots,
  onCreateRoom,
  onOpen,
}: {
  profile: Profile;
  busy: boolean;
  onBack: () => void;
  onPlayBots: () => void;
  onCreateRoom: () => void;
  onOpen: (match: MatchSummary) => void;
}) {
  const [lobby, setLobby] = useState<MatchLobby>({ mine: [], waiting: [], playing: [] });
  const [arena, setArena] = useState<ArenaInfo | null>(null);
  const [catalog, setCatalog] = useState<Record<string, CardDef>>({});

  useEffect(() => {
    let alive = true;
    let inflight = false;
    const load = async () => {
      if (inflight) return;
      inflight = true;
      try {
        const [rooms, info] = await Promise.all([api.listLobby("fantasy"), api.getArena()]);
        if (alive) {
          setLobby(rooms);
          setArena(info);
        }
      } catch {
        /* ignore */
      } finally {
        inflight = false;
      }
    };
    load();
    const id = setInterval(load, 4000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    api.getCards().then((cs) => setCatalog(Object.fromEntries(cs.map((c) => [c.id, c])))).catch(() => {});
  }, []);

  const played = arena?.played ?? profile.fantasy_played ?? 0;
  const wins = arena?.wins ?? profile.fantasy_wins ?? 0;
  const fav = arena?.favourite;
  const favName = fav ? catalog[fav.card_id]?.name ?? fav.card_id : null;
  const champs = arena?.champions?.length ? arena.champions : FALLBACK_CHAMPS;
  const events = arena?.events?.length ? arena.events : FALLBACK_EVENTS;

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col gap-4 px-4 pt-5 pb-28">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          className="grid size-9 place-items-center rounded-xl bg-secondary text-muted-foreground"
        >
          <ArrowLeft className="size-4" />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Sparkles className="size-5 text-[#f5d76e]" />
            <h1 className="text-xl font-extrabold">Fantasy Arena</h1>
          </div>
          <p className="text-[11px] text-muted-foreground">Draft a hand. Cast before you roll.</p>
        </div>
      </div>

      <Card className="bg-gradient-to-br from-[#3b1d6e] to-[#1a1230] ring-[#f5d76e]/20">
        <div className="grid grid-cols-3 gap-2 text-center">
          <div>
            <div className="text-2xl font-extrabold tabular-nums">{played}</div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-white/60">Played</div>
          </div>
          <div>
            <div className="text-2xl font-extrabold tabular-nums text-[#f5d76e]">{wins}</div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-white/60">Wins</div>
          </div>
          <div>
            <div className="truncate text-sm font-extrabold">{favName ?? "—"}</div>
            <div className="text-[10px] font-bold uppercase tracking-wider text-white/60">
              {fav ? `Most cast · ${fav.count}` : "Favourite"}
            </div>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-2.5">
        <button
          type="button"
          disabled={busy}
          onClick={onPlayBots}
          className="col-span-2 flex flex-col items-center gap-1.5 rounded-2xl border border-[#f5d76e]/20 bg-gradient-to-br from-[#7c3aed] to-[#4c1d95] p-5 text-center transition-transform active:scale-[0.97] disabled:opacity-50"
        >
          <Bot className="size-7 text-white" />
          <span className="font-extrabold text-white">Quick Arena</span>
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onCreateRoom}
          className="col-span-2 flex flex-col items-center gap-1.5 rounded-2xl border border-white/5 bg-gradient-to-br from-secondary to-card p-5 text-center transition-transform active:scale-[0.97] disabled:opacity-50"
        >
          <Plus className="size-7 text-primary" />
          <span className="font-extrabold">Create Arena Room</span>
        </button>
      </div>

      <HubRooms
        title="Your arenas"
        empty="No active arenas."
        rooms={lobby.mine}
        busy={busy}
        action={(m) =>
          m.i_am_seated && m.status === "playing"
            ? "Resume"
            : m.request_status === "pending"
              ? "Waiting"
              : "Open"
        }
        onOpen={onOpen}
      />
      <HubRooms
        title="Open arenas"
        empty="No open arenas right now."
        rooms={lobby.waiting}
        busy={busy}
        action={() => "Request"}
        onOpen={onOpen}
      />
      <HubRooms
        title="Arenas in progress"
        empty="No arenas you can join right now."
        rooms={lobby.playing}
        busy={busy}
        action={() => "Request"}
        onOpen={onOpen}
      />

      <Card>
        <SectionLabel>How it plays</SectionLabel>
        <ul className="mt-2.5 space-y-2 text-sm text-muted-foreground">
          <li><span className="font-bold text-foreground">Mana.</span> +1 at the start of your turn, +1 per capture, +2 per piece home. Spend it to cast.</li>
          <li><span className="font-bold text-foreground">Hand.</span> Draft 3 cards, then draw as you play. Cast one before you roll.</li>
          <li><span className="font-bold text-foreground">Realm.</span> Every eight turns the board shifts for a round.</li>
          <li><span className="font-bold text-foreground">Champion.</span> Pick a passive in the waiting room.</li>
        </ul>
      </Card>

      <Card>
        <SectionLabel>Champions</SectionLabel>
        <div className="mt-2.5 grid grid-cols-2 gap-2">
          {champs.map((c) => (
            <div key={c.id} className="rounded-2xl bg-secondary/60 p-3">
              <div className="text-lg">{c.icon} <span className="text-sm font-extrabold">{c.name}</span></div>
              <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{c.blurb}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <SectionLabel>Realm events</SectionLabel>
        <div className="mt-2.5 flex flex-col gap-2">
          {events.map((e) => (
            <div key={e.id} className="flex items-start gap-2.5">
              <span className="text-lg leading-none">{e.icon}</span>
              <div>
                <div className="text-sm font-bold">{e.name}</div>
                <div className="text-[11px] text-muted-foreground">{e.blurb}</div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </main>
  );
}

function HubRooms({
  title,
  empty,
  rooms,
  busy,
  action,
  onOpen,
}: {
  title: string;
  empty: string;
  rooms: MatchSummary[];
  busy: boolean;
  action: (m: MatchSummary) => string;
  onOpen: (m: MatchSummary) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <SectionLabel className="px-1">{title}</SectionLabel>
      {rooms.length === 0 ? (
        <Card className="text-center text-xs text-muted-foreground">{empty}</Card>
      ) : (
        rooms.map((t) => (
          <button
            key={t.code}
            disabled={busy}
            onClick={() => onOpen(t)}
            className="flex items-center justify-between rounded-2xl bg-card px-4 py-3 text-left ring-1 ring-white/10 transition active:scale-[0.98] disabled:opacity-50"
          >
            <div>
              <div className="font-bold tracking-wider">{t.code}</div>
              <div className="text-[11px] text-muted-foreground">
                {t.host_name ? `${t.host_name} · ` : ""}
                {t.seated}/{t.max_players} seated
                {t.entry_fee ? ` · ${t.entry_fee} coins` : ""}
              </div>
            </div>
            <span className="rounded-full bg-violet-500/20 px-3 py-1 text-xs font-bold text-violet-200">
              {action(t)}
            </span>
          </button>
        ))
      )}
    </div>
  );
}

export function ChampionPicker({
  code,
  mine,
  busy,
  onPicked,
}: {
  code: string;
  mine: string | null | undefined;
  busy: boolean;
  onPicked: (summary: MatchSummary) => void;
}) {
  const [champs, setChamps] = useState<ArenaChampion[]>(FALLBACK_CHAMPS);
  useEffect(() => {
    api.getArena().then((a) => {
      if (a.champions?.length) setChamps(a.champions);
    }).catch(() => {});
  }, []);
  return (
    <Card>
      <SectionLabel>Your champion</SectionLabel>
      <p className="mt-1 text-[11px] text-muted-foreground">Pick a passive. Bots pick at random.</p>
      <div className="mt-2.5 grid grid-cols-2 gap-2">
        {champs.map((c) => {
          const on = mine === c.id;
          return (
            <button
              key={c.id}
              type="button"
              disabled={busy}
              onClick={() => {
                haptic("light");
                api.setChampion(code, c.id).then(onPicked).catch(() => {});
              }}
              className={cn(
                "rounded-2xl p-3 text-left ring-1 transition active:scale-[0.97] disabled:opacity-50",
                on ? "bg-violet-500/20 ring-[#f5d76e]" : "bg-secondary/50 ring-white/10"
              )}
            >
              <div className="text-lg leading-none">{c.icon}</div>
              <div className="mt-1 text-sm font-extrabold">{c.name}</div>
              <div className="mt-0.5 text-[10px] leading-snug text-muted-foreground">{c.blurb}</div>
            </button>
          );
        })}
      </div>
    </Card>
  );
}

export function RealmBanner({ event }: { event: FantasyView["event"] }) {
  if (!event) return null;
  return (
    <div className="shrink-0 rounded-2xl bg-gradient-to-r from-[#4c1d95] to-[#1a1230] px-3 py-2 ring-1 ring-[#f5d76e]/30">
      <div className="flex items-center gap-2">
        <span className="text-lg leading-none">{event.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-extrabold">{event.name}</div>
          <div className="truncate text-[11px] text-white/70">{event.blurb}</div>
        </div>
        <span className="rounded-full bg-[#f5d76e]/15 px-2 py-0.5 text-[10px] font-bold text-[#f5d76e]">
          {event.turns_left} turn{event.turns_left === 1 ? "" : "s"}
        </span>
      </div>
    </div>
  );
}

export function ManaOrbs({ current, max }: { current: number; max: number }) {
  const n = Math.max(1, max);
  return (
    <div className="mt-1.5 flex items-center justify-center gap-1" aria-label={`${current} of ${n} mana`}>
      {Array.from({ length: n }, (_, i) => (
        <span
          key={i}
          className={cn(
            "size-2 rounded-full",
            i < current ? "bg-[#a78bfa] shadow-[0_0_6px_#a78bfa]" : "bg-white/15"
          )}
        />
      ))}
    </div>
  );
}

export function HandTray({
  hand,
  catalog,
  disabled,
  onCast,
}: {
  hand: FantasyHandCard[];
  catalog: Record<string, CardDef>;
  disabled: boolean;
  onCast: (card: FantasyHandCard) => void;
}) {
  if (hand.length === 0) {
    return (
      <div className="mb-2 text-center text-[11px] text-muted-foreground">Empty hand — draw as you play.</div>
    );
  }
  return (
    <div className="mb-2 flex justify-center gap-1.5 overflow-x-auto px-1">
      {hand.map((h, i) => {
        const def = catalog[h.id];
        const rc = rarityColor(def?.rarity ?? "common");
        const dim = disabled || !h.castable;
        return (
          <button
            key={`${h.id}-${i}`}
            type="button"
            disabled={dim}
            onClick={() => {
              haptic("medium");
              onCast(h);
            }}
            className={cn(
              "relative flex h-[88px] w-[68px] shrink-0 flex-col items-center justify-center gap-0.5 rounded-xl border-2 bg-card p-1 text-center transition active:scale-95 disabled:opacity-40",
            )}
            style={{ borderColor: rc, transform: `rotate(${(i - (hand.length - 1) / 2) * 4}deg)` }}
          >
            <span
              className="absolute -top-1.5 -right-1.5 grid size-5 place-items-center rounded-full text-[10px] font-extrabold text-[#1a1230]"
              style={{ background: h.castable ? "#a78bfa" : "#6b7280" }}
            >
              {h.cost}
            </span>
            <Wand2 className="size-4" style={{ color: rc }} />
            <span className="line-clamp-2 text-[9px] font-extrabold leading-tight">
              {def?.name ?? h.id}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function DraftOverlay({
  draft,
  catalog,
  mine,
  onPick,
}: {
  draft: NonNullable<FantasyView["draft"]>;
  catalog: Record<string, CardDef>;
  mine: boolean;
  onPick: (index: number) => void;
}) {
  const [tapped, setTapped] = useState<number | null>(null);
  useEffect(() => setTapped(null), [draft.round, draft.options.join(",")]);
  return (
    <div className="fixed inset-0 z-[70] flex flex-col items-center justify-center bg-black/80 px-6 backdrop-blur-md">
      <div className="mb-1 text-xs font-bold uppercase tracking-widest text-[#f5d76e]">
        Draft · round {draft.round} of {draft.rounds}
      </div>
      <div className="mb-5 text-center text-2xl font-extrabold text-white">
        {draft.picked ? "Waiting for rivals…" : mine ? "Pick a card" : "Drafting…"}
      </div>
      <div className="grid w-full max-w-xs grid-cols-3 gap-2">
        {draft.options.map((id, i) => {
          const c = catalog[id];
          const rc = rarityColor(c?.rarity ?? "common");
          return (
            <button
              key={`${id}-${i}`}
              type="button"
              disabled={!mine || draft.picked}
              onClick={() => {
                if (tapped !== null) return;
                setTapped(i);
                haptic("medium");
                onPick(i);
              }}
              className={cn(
                "flex aspect-[3/4] flex-col items-center justify-center gap-1 rounded-2xl border-2 bg-card p-2 text-center",
                tapped === i && "scale-105",
                draft.picked && "opacity-60"
              )}
              style={{ borderColor: rc }}
            >
              <span className="text-lg">{c?.icon ?? "✦"}</span>
              <span className="text-[11px] font-extrabold leading-tight">{c?.name ?? id}</span>
              <span className="text-[9px] font-bold uppercase" style={{ color: rc }}>{c?.rarity}</span>
            </button>
          );
        })}
      </div>
      {draft.picked && (
        <p className="mt-4 text-xs text-muted-foreground">
          Waiting on {draft.waiting.length} player{draft.waiting.length === 1 ? "" : "s"}
        </p>
      )}
    </div>
  );
}

export function mostCast(castLog: Record<string, number>, catalog: Record<string, CardDef>): string | null {
  let best: string | null = null;
  let n = 0;
  for (const [id, count] of Object.entries(castLog)) {
    if (count > n) {
      best = catalog[id]?.name ?? id;
      n = count;
    }
  }
  return best;
}
