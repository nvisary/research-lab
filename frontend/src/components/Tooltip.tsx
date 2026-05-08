import type { ReactNode } from "react";

type Props = {
  text?: string | undefined;
  children: ReactNode;
  /** "below" (default), "above", "right" */
  side?: "below" | "above" | "right";
};

/**
 * CSS-only tooltip. Wraps the child in a span with a dotted underline to
 * signal hoverability; on hover, an absolutely-positioned bubble appears.
 *
 * No JS, no portals, no dependencies. The bubble does not capture clicks
 * (pointer-events-none) so it can overlap other interactive elements
 * harmlessly.
 */
export function Tooltip({ text, children, side = "below" }: Props) {
  if (!text) return <>{children}</>;

  const pos =
    side === "above"
      ? "left-1/2 -translate-x-1/2 bottom-full mb-2"
      : side === "right"
      ? "left-full top-0 ml-2"
      : "left-0 top-full mt-1.5";

  return (
    <span className="group relative inline-block align-baseline">
      <span className="cursor-help border-b border-dotted border-slate-600 hover:border-slate-300">
        {children}
      </span>
      <span
        className={
          "invisible group-hover:visible opacity-0 group-hover:opacity-100 " +
          "transition-opacity duration-100 " +
          "absolute z-50 w-72 max-w-[80vw] " +
          "rounded-md bg-slate-950/95 border border-slate-700 px-3 py-2 " +
          "text-xs leading-relaxed text-slate-200 shadow-xl shadow-black/50 " +
          "whitespace-normal pointer-events-none " +
          pos
        }
      >
        {text}
      </span>
    </span>
  );
}
