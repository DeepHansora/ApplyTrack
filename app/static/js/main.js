/**
 * ApplyTrack Mobile Navigation Menu
 * Lightweight, accessible vanilla JS implementation.
 */
document.addEventListener('DOMContentLoaded', () => {
  const toggleBtn = document.querySelector('.menu-toggle');
  const navMenu = document.querySelector('#primary-navigation');

  if (!toggleBtn || !navMenu) return;

  const toggleMenu = (open) => {
    const isOpen = open !== undefined ? open : toggleBtn.getAttribute('aria-expanded') !== 'true';
    toggleBtn.setAttribute('aria-expanded', String(isOpen));
    navMenu.classList.toggle('site-nav--open', isOpen);
    document.body.classList.toggle('nav-open', isOpen);
  };

  toggleBtn.addEventListener('click', () => {
    toggleMenu();
  });

  // Close menu on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && toggleBtn.getAttribute('aria-expanded') === 'true') {
      toggleMenu(false);
      toggleBtn.focus();
    }
  });

  // Close menu when clicking outside header
  document.addEventListener('click', (e) => {
    if (toggleBtn.getAttribute('aria-expanded') === 'true') {
      const header = document.querySelector('.site-header');
      if (header && !header.contains(e.target)) {
        toggleMenu(false);
      }
    }
  });
});
