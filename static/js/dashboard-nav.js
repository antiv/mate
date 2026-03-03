/**
 * MATE Dashboard - Mobile navigation (hamburger + drawer)
 */

function toggleMobileSidebar() {
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  if (!sidebar || !backdrop) return;

  const isOpen = sidebar.classList.contains('mobile-sidebar-open');
  if (isOpen) {
    closeMobileSidebar();
  } else {
    sidebar.classList.add('mobile-sidebar-open');
    backdrop.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  }
}

function closeMobileSidebar() {
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  if (!sidebar || !backdrop) return;

  sidebar.classList.remove('mobile-sidebar-open');
  backdrop.classList.add('hidden');
  document.body.style.overflow = '';
}

function initMobileNav() {
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  const hamburger = document.getElementById('hamburgerBtn');
  const sidebarLinks = sidebar ? sidebar.querySelectorAll('a[href]') : [];

  if (hamburger) {
    hamburger.addEventListener('click', toggleMobileSidebar);
  }

  if (backdrop) {
    backdrop.addEventListener('click', closeMobileSidebar);
  }

  // Close drawer when navigating (link click)
  sidebarLinks.forEach((link) => {
    link.addEventListener('click', () => {
      closeMobileSidebar();
    });
  });

  // Close on escape
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeMobileSidebar();
    }
  });
}

document.addEventListener('DOMContentLoaded', initMobileNav);
