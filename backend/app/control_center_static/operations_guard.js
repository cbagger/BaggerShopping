(() => {
  "use strict";
  const NativeMutationObserver = window.MutationObserver;
  if (!NativeMutationObserver) return;

  window.MutationObserver = class SafeMutationObserver {
    constructor(callback) {
      this.callback = callback;
      this.target = null;
      this.options = null;
      this.native = new NativeMutationObserver((mutations) => {
        this.native.disconnect();
        try {
          this.callback(mutations, this);
        } finally {
          if (this.target && this.options) {
            window.setTimeout(() => this.native.observe(this.target, this.options), 0);
          }
        }
      });
    }
    observe(target, options) {
      this.target = target;
      this.options = options;
      this.native.observe(target, options);
    }
    disconnect() {
      this.target = null;
      this.options = null;
      this.native.disconnect();
    }
    takeRecords() {
      return this.native.takeRecords();
    }
  };
})();
