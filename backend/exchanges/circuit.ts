export class RateLimiter {
  private queue: Array<Promise<void>> = [];
  private lastRequestTime = 0;
  
  constructor(private maxRequests: number, private intervalMs: number) {}

  async acquire(): Promise<void> {
    const now = Date.now();
    const elapsed = now - this.lastRequestTime;
    
    if (elapsed > this.intervalMs) {
      // Reset if interval has passed
      this.queue = [];
      this.lastRequestTime = now;
      return;
    }

    if (this.queue.length < this.maxRequests) {
      // Can proceed immediately
      this.queue.push(Promise.resolve());
      return;
    }

    // Wait for next available slot
    const waitPromise = new Promise<void>((resolve) => {
      const check = () => {
        if (this.queue.length < this.maxRequests) {
          this.queue.push(Promise.resolve());
          resolve();
        } else {
          setTimeout(check, 10);
        }
      };
      check();
    });
    
    this.queue.push(waitPromise);
    return waitPromise;
  }
}

export class CircuitBreaker {
  private failures = 0;
  private lastFailureTime = 0;
  private state: 'CLOSED' | 'OPEN' | 'HALF_OPEN' = 'CLOSED';

  constructor(private failureThreshold: number, private resetTimeoutMs: number) {}

  isOpen(): boolean {
    if (this.state === 'OPEN') {
      const now = Date.now();
      if (now - this.lastFailureTime > this.resetTimeoutMs) {
        this.state = 'HALF_OPEN';
        return false;
      }
      return true;
    }
    return false;
  }

  onSuccess(): void {
    if (this.state === 'HALF_OPEN') {
      this.state = 'CLOSED';
      this.failures = 0;
    }
  }

  onFailure(): void {
    this.failures++;
    this.lastFailureTime = Date.now();
    
    if (this.failures >= this.failureThreshold) {
      this.state = 'OPEN';
    }
  }
}
