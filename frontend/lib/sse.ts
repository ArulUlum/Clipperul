import { apiBaseUrl, authToken } from "@/lib/api";

export interface VideoStatusEvent {
  video_id?: string;
  job_id?: string;
  stage: string;
  job_status: string;
  error_code?: string | null;
  error_message?: string | null;
}

/**
 * Manually parsed SSE over fetch (not EventSource) so we can send the Authorization
 * header — EventSource can't set custom headers, and we don't want to put JWTs in URLs.
 */
export function subscribeToVideoStatus(
  videoId: string,
  onEvent: (data: VideoStatusEvent) => void,
  onError?: (err: unknown) => void
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const token = authToken();
      const res = await fetch(`${apiBaseUrl()}/videos/${videoId}/stream`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: controller.signal,
      });
      if (!res.body) return;

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // The server sends CRLF ("\r\n") line endings; normalize to LF before splitting on
        // blank lines, or event boundaries never match and no event ever gets parsed.
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";

        for (const rawEvent of events) {
          const dataLine = rawEvent.split("\n").find((line) => line.startsWith("data:"));
          if (!dataLine) continue;
          try {
            onEvent(JSON.parse(dataLine.slice(5).trim()));
          } catch {
            // ignore malformed frames
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") onError?.(err);
    }
  })();

  return () => controller.abort();
}
