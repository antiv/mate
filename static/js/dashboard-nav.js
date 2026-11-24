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

/** A count beside a sidebar item. It is a hint: a failed request leaves it as it was. */
async function refreshNavCount(id, url, label) {
  const badge = document.getElementById(id);
  if (!badge) return;
  try {
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) return;
    const total = (await resp.json()).total || 0;
    badge.textContent = total > 99 ? '99+' : String(total);
    badge.hidden = total === 0;
    badge.setAttribute('aria-label', `${total} ${label}`);
    badge.title = `${total} ${label}`;
  } catch (e) { /* see above */ }
}

/** Requests waiting for the inbox. The Approvals page calls it again after a decision. */
function refreshApprovalsCount() {
  return refreshNavCount('approvalsNavCount', '/dashboard/api/approvals?status=pending&inbox=true&limit=1', 'waiting');
}

/** Sign-ups waiting to be let in. */
function refreshUsersCount() {
  return refreshNavCount('usersNavCount', '/dashboard/api/users/waiting', 'waiting to be let in');
}

document.addEventListener('DOMContentLoaded', () => { refreshApprovalsCount(); refreshUsersCount(); });
