import { cn } from "@/lib/utils";

export function Progress({
  value,
  indeterminate = false,
  className,
}: {
  value?: number;
  /** Work is happening but there's no percentage to show yet — renders a sweeping segment. */
  indeterminate?: boolean;
  className?: string;
}) {
  const clamped = Math.min(100, Math.max(0, value ?? 0));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(clamped)}
      className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)}
    >
      {indeterminate ? (
        <div className="h-full w-1/3 animate-indeterminate rounded-full bg-primary" />
      ) : (
        <div
          className="h-full rounded-full bg-primary transition-all duration-500"
          style={{ width: `${clamped}%` }}
        />
      )}
    </div>
  );
}
