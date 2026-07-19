import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-3xl bg-card ring-1 ring-white/10 p-4", className)}
      {...props}
    />
  );
}

export function SectionLabel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "text-[11px] font-bold uppercase tracking-wider text-muted-foreground",
        className
      )}
      {...props}
    />
  );
}
