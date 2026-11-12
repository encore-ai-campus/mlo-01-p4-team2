(() => {
  const mobileNotice = document.querySelector('#mobile-pc-notice');
  const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if (mobileNotice && isMobile) mobileNotice.hidden = false;
  const password = document.querySelector('#id_password');
  const toggle = document.querySelector('.password-toggle');
  toggle.addEventListener('click', () => {
    const visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    toggle.setAttribute('aria-pressed', String(visible));
    toggle.setAttribute('aria-label', visible ? '비밀번호 숨기기' : '비밀번호 표시');
  });
  const username = document.querySelector('#id_username');
  const remember = document.querySelector('#remember-id');
  const key = 'playdata.savedUsername';
  try {
    const saved = localStorage.getItem(key);
    if (saved) { if (!username.value) username.value = saved; remember.checked = true; }
  } catch (_) { /* Browser storage can be disabled. */ }
  remember.addEventListener('change', () => {
    if (!remember.checked) { try { localStorage.removeItem(key); } catch (_) {} }
  });
  document.querySelector('.login-form').addEventListener('submit', () => {
    try {
      if (remember.checked) localStorage.setItem(key, username.value);
      else localStorage.removeItem(key);
    } catch (_) {}
  });
  const dialog = document.querySelector('.recovery-dialog');
  document.querySelector('.recovery-button').addEventListener('click', () => dialog.showModal());
})();
