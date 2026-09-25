import { CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";
import { PROCESSING_STAGES, STAGE_LABELS, type VideoStatus } from "@/types";
import { cn } from "@/lib/utils";

export function ProcessingStages({ currentStage }: { currentStage: VideoStatus }) {
  const failed = currentStage === "FAILED";
  const currentIndex = PROCESSING_STAGES.indexOf(currentStage);

  return (
    <div className="space-y-3">
      {PROCESSING_STAGES.map((stage, index) => {
        const isDone = !failed && currentIndex > index;
        const isCurrent = !failed && currentIndex === index;
        const isPending = !failed && currentIndex < index;

        return (
          <div key={stage} className="flex items-center gap-3 text-sm">
            {isDone && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
            {isCurrent && <Loader2 className="h-4 w-4 animate-spin text-primary" />}
            {isPending && <CircleDashed className="h-4 w-4 text-muted-foreground" />}
            {failed && <XCircle className="h-4 w-4 text-destructive" />}
            <span
              className={cn(
                "text-muted-foreground",
                (isDone || isCurrent) && "text-foreground font-medium"
              )}
            >
              {STAGE_LABELS[stage]}
            </span>
          </div>
        );
      })}
    </div>
  );
}
