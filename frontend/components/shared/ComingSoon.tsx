import { Card, CardContent } from "@/components/ui/card";
import type { LucideIcon } from "lucide-react";

export function ComingSoon({
  icon: Icon,
  title,
  phase,
  description,
}: {
  icon: LucideIcon;
  title: string;
  phase: string;
  description: string;
}) {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <Card>
        <CardContent className="flex flex-col items-center gap-3 p-12 text-center">
          <Icon className="h-10 w-10 text-muted-foreground" />
          <p className="text-sm font-medium">Coming in {phase}</p>
          <p className="max-w-md text-sm text-muted-foreground">{description}</p>
        </CardContent>
      </Card>
    </div>
  );
}
