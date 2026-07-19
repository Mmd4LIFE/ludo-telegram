import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-2xl font-semibold transition-[transform,background,filter] active:translate-y-px active:brightness-95 disabled:opacity-45 disabled:pointer-events-none select-none",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-[0_6px_20px_-8px_rgba(245,183,10,0.6)] hover:brightness-105",
        accent: "bg-accent text-white hover:brightness-110",
        secondary: "bg-secondary text-secondary-foreground ring-1 ring-white/10 hover:bg-white/5",
        outline: "ring-1 ring-white/12 text-foreground hover:bg-white/5",
        ghost: "text-muted-foreground hover:bg-white/5 hover:text-foreground",
        win: "bg-win text-[#04140d] hover:brightness-105",
      },
      size: {
        default: "h-11 px-5 text-sm",
        sm: "h-9 px-3.5 text-xs",
        lg: "h-14 px-6 text-base",
        icon: "size-10",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
