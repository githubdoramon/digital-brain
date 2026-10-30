type RefreshContext = { signal: AbortSignal; isCurrent: () => boolean };

/** One bounded request at a time, active only while the screen is foregrounded. */
export function createChatRefreshLoop(options: {
  readRevision: (signal: AbortSignal) => Promise<string>;
  refresh: (context: RefreshContext) => Promise<boolean>;
  canRefresh: () => boolean;
  intervalMs?: number;
  timeoutMs?: number;
}) {
  let active = false;
  let epoch = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let request: AbortController | undefined;
  let revision: string | undefined;
  let failures = 0;

  function pause() {
    active = false;
    epoch++;
    clearTimeout(timer);
    request?.abort();
  }

  async function tick(generation: number) {
    if (!active || epoch !== generation) return;
    const controller = new AbortController();
    request = controller;
    const deadline = setTimeout(() => controller.abort(), options.timeoutMs ?? 15000);
    const isCurrent = () =>
      active && epoch === generation && !controller.signal.aborted && options.canRefresh();
    let abort: () => void = () => {};
    const interrupted = new Promise<never>((_, reject) => {
      abort = () => reject(new Error('Refresh cancelled'));
      controller.signal.addEventListener('abort', abort, { once: true });
    });
    try {
      await Promise.race([
        interrupted,
        (async () => {
          if (!isCurrent()) return;
          const next = await options.readRevision(controller.signal);
          if (!isCurrent()) return;
          // The first probe also reloads: initial hydration may precede this revision.
          if (next !== revision) {
            if (!(await options.refresh({ signal: controller.signal, isCurrent })) || !isCurrent())
              return;
            revision = next;
          }
        })(),
      ]);
      failures = 0;
    } catch {
      failures++;
    } finally {
      clearTimeout(deadline);
      controller.signal.removeEventListener('abort', abort);
      if (active && epoch === generation) {
        timer = setTimeout(
          () => void tick(generation),
          Math.min(30000, (options.intervalMs ?? 3000) * 2 ** Math.min(failures, 3)),
        );
      }
    }
  }

  return {
    start() {
      pause();
      active = true;
      failures = 0;
      // Revalidate even an unchanged revision after returning to the screen.
      revision = undefined;
      void tick(epoch);
    },
    pause,
  };
}
