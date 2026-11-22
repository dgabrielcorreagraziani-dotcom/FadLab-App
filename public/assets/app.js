if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

document.addEventListener('submit', (event) => {
  if (event.defaultPrevented || event.target.method !== 'post') return;
  const button = event.submitter;
  if (!(button instanceof HTMLButtonElement)) return;
  button.setAttribute('aria-busy', 'true');
  window.setTimeout(() => {
    button.disabled = true;
  }, 0);
});

window.addEventListener('pageshow', () => {
  document.querySelectorAll('button[aria-busy="true"]').forEach((button) => {
    button.removeAttribute('aria-busy');
    button.disabled = false;
  });
});

const resetCode = document.querySelector('#reset-code');
if (resetCode) {
  const code = window.location.hash.slice(1);
  if (/^[A-Za-z0-9_-]{43}$/.test(code)) {
    resetCode.value = code;
    history.replaceState(null, '', window.location.pathname);
  }
  if (resetCode.value) document.querySelector('#reset-code-label').hidden = true;
}
