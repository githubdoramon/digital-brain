/** Shared by automatic callers so capture/resume cannot bypass a failed recovery's cooldown. */
export class ConnectionBackoff {
  private failures = 0;
  private retryAt = 0;
  remaining(now = Date.now()): number {
    return Math.max(0, this.retryAt - now);
  }
  failed(now = Date.now()): number {
    this.failures = Math.min(this.failures + 1, 5);
    const delay = Math.min(30 * 60_000, 5 * 60_000 * 2 ** (this.failures - 1));
    this.retryAt = now + delay;
    return delay;
  }
  reset(): void {
    this.failures = 0;
    this.retryAt = 0;
  }
}
