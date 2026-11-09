/**
 * CiteProof Landing Page Script
 * Vanilla JS, no build step, 2 spaces indentation.
 * Handles theme toggling, mobile nav, smooth scrolling, scroll progress & washes,
 * scroll reveals with stagger, shadcn-compliant tabs with keyboard navigation,
 * pointer spotlight, 3D tilt, animation pausing, count-up numerals,
 * and the interactive research output gallery.
 */

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initMobileNav();
  initSmoothScroll();
  initNavbarScroll();
  initScrollProgressAndWashes();
  initHeroPointerGlow();
  initHeroRotator();
  initGithubStars();
  initOutputExpand();
  initScrollReveal();
  initTabs();
  initCardSpotlight();
  initOutputWindowTilt();
  initAnimationPauseController();
  initCountUpMetrics();
  initExampleGallery();
});

/* ==========================================================================
   1. THEME CONTROLLER
   Dark is default. html.light-mode is light.
   Uses localStorage theme key (shared across other pages).
   ========================================================================== */

const THEME_STORAGE_KEY = 'deepresearch_theme';

const SUN_ICON_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>';
const MOON_ICON_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>';

function getSavedTheme() {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) || 'dark';
  } catch {
    return 'dark';
  }
}

function setSavedTheme(theme) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Storage access may be restricted
  }
}

function applyTheme(theme) {
  const isLight = theme === 'light';
  document.documentElement.classList.toggle('light-mode', isLight);
  updateThemeToggleIcon(isLight);
}

function updateThemeToggleIcon(isLight) {
  const btn = document.getElementById('theme-toggle-btn');
  if (!btn) return;
  btn.innerHTML = isLight ? SUN_ICON_SVG : MOON_ICON_SVG;
}

function initTheme() {
  const initialTheme = getSavedTheme();
  applyTheme(initialTheme);

  const toggleBtn = document.getElementById('theme-toggle-btn');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      // Read the DOM, not storage: with storage blocked getSavedTheme() is
      // always 'dark', so the toggle could never switch back.
      const isLight = document.documentElement.classList.contains('light-mode');
      const next = isLight ? 'dark' : 'light';
      setSavedTheme(next);
      applyTheme(next);
    });
  }

  // Cross-tab synchronization
  window.addEventListener('storage', (e) => {
    if (e.key === THEME_STORAGE_KEY && e.newValue) {
      applyTheme(e.newValue);
    }
  });
}

/* ==========================================================================
   2. MOBILE NAVIGATION
   Toggles .mobile-open on #nav-menu, updates aria-expanded, closes on Escape.
   ========================================================================== */

const MENU_ICON_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg>';
const CLOSE_ICON_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';

function initMobileNav() {
  const toggleBtn = document.getElementById('mobile-nav-toggle');
  const navMenu = document.getElementById('nav-menu');
  if (!toggleBtn || !navMenu) return;

  function updateToggleState(isOpen) {
    toggleBtn.setAttribute('aria-expanded', String(isOpen));
    toggleBtn.innerHTML = isOpen ? CLOSE_ICON_SVG : MENU_ICON_SVG;
  }

  function closeMenu() {
    navMenu.classList.remove('mobile-open');
    updateToggleState(false);
  }

  toggleBtn.addEventListener('click', () => {
    const isOpen = navMenu.classList.toggle('mobile-open');
    updateToggleState(isOpen);
  });

  navMenu.querySelectorAll('.nav-link').forEach((link) => {
    link.addEventListener('click', closeMenu);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && navMenu.classList.contains('mobile-open')) {
      closeMenu();
      toggleBtn.focus();
    }
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth > 960 && navMenu.classList.contains('mobile-open')) {
      closeMenu();
    }
  });
}

/* ==========================================================================
   3. SMOOTH SCROLLING FOR IN-PAGE ANCHORS
   ========================================================================== */

function initSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener('click', function (e) {
      const targetId = this.getAttribute('href');
      if (!targetId || targetId === '#') return;
      const targetEl = document.querySelector(targetId);
      if (targetEl) {
        e.preventDefault();
        const headerOffset = 64;
        const elementPosition = targetEl.getBoundingClientRect().top;
        const offsetPosition = elementPosition + window.pageYOffset - headerOffset;

        window.scrollTo({
          top: offsetPosition,
          behavior: 'smooth'
        });

        if (!targetEl.hasAttribute('tabindex')) {
          targetEl.setAttribute('tabindex', '-1');
        }
        targetEl.focus({ preventScroll: true });
      }
    });
  });
}

/* ==========================================================================
   4. DYNAMIC NAVBAR SCROLL BEHAVIOR
   ========================================================================== */

function initNavbarScroll() {
  const header = document.querySelector('.landing-header');
  if (!header) return;

  let ticking = false;
  function updateScroll() {
    if (window.scrollY > 80) {
      header.classList.add('scrolled');
    } else {
      header.classList.remove('scrolled');
    }
    ticking = false;
  }

  window.addEventListener('scroll', () => {
    if (!ticking) {
      window.requestAnimationFrame(updateScroll);
      ticking = true;
    }
  }, { passive: true });

  updateScroll();

  // Allow keyboard navigation (Tab) to reach the header even when hidden at scrollY = 0
  const skipLink = document.querySelector('.skip-link');
  if (skipLink) {
    skipLink.addEventListener('keydown', (e) => {
      if (e.key === 'Tab' && !e.shiftKey) {
        header.style.visibility = 'visible';
        setTimeout(() => {
          header.style.visibility = '';
        }, 0);
      }
    });
  }

  const firstHeroFocusable = document.querySelector('.hero-gh, .hero-section a, .hero-section button');
  if (firstHeroFocusable) {
    firstHeroFocusable.addEventListener('keydown', (e) => {
      if (e.key === 'Tab' && e.shiftKey) {
        header.style.visibility = 'visible';
        setTimeout(() => {
          header.style.visibility = '';
        }, 0);
      }
    });
  }
}

/* ==========================================================================
   5. SCROLL PROGRESS & SECTION COLOR WASHES
   Sets --scroll-progress (0 to 1) on <html> via passive scroll and rAF.
   Sets data-wash on <body> via IntersectionObserver for subtle background wash transitions.
   ========================================================================== */

function initScrollProgressAndWashes() {
  let scrollTicking = false;

  function updateScrollProgress() {
    const docHeight = document.documentElement.scrollHeight - window.innerHeight;
    const progress = docHeight > 0 ? Math.min(Math.max(window.scrollY / docHeight, 0), 1) : 0;
    document.documentElement.style.setProperty('--scroll-progress', progress.toFixed(4));
    scrollTicking = false;
  }

  window.addEventListener('scroll', () => {
    if (!scrollTicking) {
      window.requestAnimationFrame(updateScrollProgress);
      scrollTicking = true;
    }
  }, { passive: true });

  updateScrollProgress();

  // Section Color Wash Observer
  if ('IntersectionObserver' in window) {
    const sections = document.querySelectorAll('section[data-wash]');
    const washObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const washColor = entry.target.getAttribute('data-wash');
          if (washColor) {
            document.body.setAttribute('data-wash', washColor);
          }
        }
      });
    }, {
      threshold: 0.25,
      rootMargin: '-10% 0px -40% 0px'
    });

    sections.forEach((sec) => washObserver.observe(sec));
  }
}

/* ==========================================================================
   6. HERO POINTER GLOW
   Soft radial highlight following pointer inside hero. Disabled on touch & reduced motion.
   ========================================================================== */

function initHeroPointerGlow() {
  const hero = document.getElementById('hero');
  if (!hero) return;

  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!finePointer || prefersReduced) return;

  let glowTicking = false;
  let clientX = 0;
  let clientY = 0;

  hero.addEventListener('pointermove', (e) => {
    clientX = e.clientX;
    clientY = e.clientY;

    if (!glowTicking) {
      window.requestAnimationFrame(() => {
        const rect = hero.getBoundingClientRect();
        const x = clientX - rect.left;
        const y = clientY - rect.top;
        hero.style.setProperty('--mx', `${x}px`);
        hero.style.setProperty('--my', `${y}px`);
        const px = clientX - (rect.left + rect.width / 2);
        const py = clientY - (rect.top + rect.height / 2);
        hero.style.setProperty('--px', `${px.toFixed(1)}px`);
        hero.style.setProperty('--py', `${py.toFixed(1)}px`);
        glowTicking = false;
      });
      glowTicking = true;
    }
  }, { passive: true });
}

/* ==========================================================================
   6b. HERO ROTATOR
   Rotates hero keywords with chained setTimeout (2400ms).
   data-state: active (visible), before (parked above), after (parked below).
   Springy overshoot entrance, pause on hidden tab or out of viewport.
   Respects prefers-reduced-motion: reduce (static first word).
   ========================================================================== */

function initHeroRotator() {
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced) return;

  const rotator = document.querySelector('.hero-rotator');
  if (!rotator) return;

  const words = Array.from(rotator.querySelectorAll('.hero-rotator-word'));
  if (words.length < 2) return;

  let currentIndex = 0;
  words.forEach((word, index) => {
    if (word.getAttribute('data-state') === 'active') {
      currentIndex = index;
    }
  });

  let timerId = null;
  let isTabVisible = document.visibilityState === 'visible';
  let isHeroInView = true;

  function step() {
    const prevIndex = currentIndex;
    currentIndex = (currentIndex + 1) % words.length;

    const prevWord = words[prevIndex];
    const nextWord = words[currentIndex];

    if (currentIndex === 0) {
      // Loop to first: the first goes to "active", old active to "before", all others "after"
      words.forEach((w, idx) => {
        if (idx !== 0 && idx !== prevIndex) {
          w.style.transition = 'none';
          w.setAttribute('data-state', 'after');
          void w.offsetHeight;
          w.style.transition = '';
        }
      });

      // Ensure word 0 slides in from below ('after')
      if (nextWord.getAttribute('data-state') !== 'after') {
        nextWord.style.transition = 'none';
        nextWord.setAttribute('data-state', 'after');
        void nextWord.offsetHeight;
        nextWord.style.transition = '';
      }

      prevWord.setAttribute('data-state', 'before');
      nextWord.setAttribute('data-state', 'active');
    } else {
      // Normal advancement: make sure nextWord is parked below ('after') before animating to 'active'
      if (nextWord.getAttribute('data-state') !== 'after') {
        nextWord.style.transition = 'none';
        nextWord.setAttribute('data-state', 'after');
        void nextWord.offsetHeight;
        nextWord.style.transition = '';
      }

      prevWord.setAttribute('data-state', 'before');
      nextWord.setAttribute('data-state', 'active');
    }
  }

  function scheduleRotation() {
    clearTimeout(timerId);
    if (!isTabVisible || !isHeroInView) return;
    timerId = setTimeout(() => {
      step();
      scheduleRotation();
    }, 2400);
  }

  function updatePauseState() {
    const shouldRun = isTabVisible && isHeroInView;
    if (shouldRun) {
      scheduleRotation();
    } else {
      clearTimeout(timerId);
    }
  }

  document.addEventListener('visibilitychange', () => {
    isTabVisible = document.visibilityState === 'visible';
    updatePauseState();
  });

  if ('IntersectionObserver' in window) {
    const heroSection = document.getElementById('hero');
    if (heroSection) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          isHeroInView = entry.isIntersecting;
          updatePauseState();
        });
      }, { threshold: 0 });
      observer.observe(heroSection);
    }
  }

  scheduleRotation();
}

/* ==========================================================================
   7. SCROLL REVEAL (Fade, 24px Rise, Blur to Sharp, Staggered by --i)
   ========================================================================== */

function initScrollReveal() {
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const elements = document.querySelectorAll('.scroll-reveal');
  if (!elements.length) return;

  // Stagger children within grids
  document.querySelectorAll('.steps-grid, .checks-grid, .checkpoints-grid, .modes-grid, .example-selector-row, .footer-grid').forEach((grid) => {
    Array.from(grid.children).forEach((child, idx) => {
      child.style.setProperty('--i', idx);
    });
  });

  if (prefersReduced || !('IntersectionObserver' in window)) {
    elements.forEach((el) => el.classList.add('in-view'));
    return;
  }

  const observer = new IntersectionObserver((entries, obs) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('in-view');
        obs.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.1,
    rootMargin: '0px 0px -40px 0px'
  });

  elements.forEach((el) => observer.observe(el));
}

/* ==========================================================================
   8. SHADCN TABS CONTROLLER (ARIA, data-state, Arrow Navigation)
   Supports Left, Right, Home, End key navigation for Radix/shadcn parity.
   ========================================================================== */

function initTabs() {
  const tablists = document.querySelectorAll('[role="tablist"]');

  tablists.forEach((tablist) => {
    const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
    if (!tabs.length) return;

    // Keyboard navigation (Left, Right, Home, End)
    tablist.addEventListener('keydown', (e) => {
      const currentTab = document.activeElement;
      const currentIndex = tabs.indexOf(currentTab);
      if (currentIndex === -1) return;

      let targetIndex = null;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault();
        targetIndex = (currentIndex + 1) % tabs.length;
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault();
        targetIndex = (currentIndex - 1 + tabs.length) % tabs.length;
      } else if (e.key === 'Home') {
        e.preventDefault();
        targetIndex = 0;
      } else if (e.key === 'End') {
        e.preventDefault();
        targetIndex = tabs.length - 1;
      }

      if (targetIndex !== null) {
        tabs[targetIndex].focus();
        tabs[targetIndex].click();
      }
    });
  });

  // Output window subtabs click behavior
  const subtabButtons = document.querySelectorAll('.window-subtab-btn');
  const panes = document.querySelectorAll('.window-tab-pane');

  subtabButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const targetTab = btn.getAttribute('data-tab');

      subtabButtons.forEach((b) => {
        const isActive = b === btn;
        b.classList.toggle('active', isActive);
        b.setAttribute('aria-selected', String(isActive));
        b.setAttribute('data-state', isActive ? 'active' : 'inactive');
        b.setAttribute('tabindex', isActive ? '0' : '-1');
      });

      panes.forEach((pane) => {
        const isActive = pane.id === `tab-pane-${targetTab}`;
        pane.classList.toggle('active', isActive);
        pane.setAttribute('data-state', isActive ? 'active' : 'inactive');
      });
    });
  });
}

/* ==========================================================================
   9. CURSOR SPOTLIGHT ON CARDS (Desktop fine pointer only)
   ========================================================================== */

function initCardSpotlight() {
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!finePointer || prefersReduced) return;

  const cards = document.querySelectorAll('.step-card, .check-card, .mode-card, .checkpoint-card, .mcp-card, .mcp-cap-card, .mcp-pillar, .mcp-setup-card');

  cards.forEach((card) => {
    card.addEventListener('pointermove', (e) => {
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      card.style.setProperty('--mx', `${x}px`);
      card.style.setProperty('--my', `${y}px`);
    }, { passive: true });
  });
}

/* ==========================================================================
   10. SUBTLE 3D TILT ON EXAMPLE OUTPUT WINDOW
   Max ~3 degrees perspective tilt following cursor, eases back on leave.
   ========================================================================== */

function initOutputWindowTilt() {
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!finePointer || prefersReduced) return;

  const windowEl = document.querySelector('.output-window, .mcp-install');
  if (!windowEl) return;

  let frameId = null;
  let targetRotateX = 0;
  let targetRotateY = 0;
  let currentRotateX = 0;
  let currentRotateY = 0;
  let isHovered = false;

  function updateTilt() {
    currentRotateX += (targetRotateX - currentRotateX) * 0.12;
    currentRotateY += (targetRotateY - currentRotateY) * 0.12;

    windowEl.style.transform = `perspective(1000px) rotateX(${currentRotateX.toFixed(2)}deg) rotateY(${currentRotateY.toFixed(2)}deg)`;

    if (isHovered || Math.abs(targetRotateX - currentRotateX) > 0.02 || Math.abs(targetRotateY - currentRotateY) > 0.02) {
      frameId = requestAnimationFrame(updateTilt);
    } else {
      windowEl.style.transform = '';
      frameId = null;
    }
  }

  windowEl.addEventListener('pointerenter', () => {
    isHovered = true;
    if (!frameId) frameId = requestAnimationFrame(updateTilt);
  });

  windowEl.addEventListener('pointermove', (e) => {
    const rect = windowEl.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width - 0.5;
    const y = (e.clientY - rect.top) / rect.height - 0.5;
    targetRotateX = -y * 5; // Max 2.5 deg
    targetRotateY = x * 5;
    if (!frameId) frameId = requestAnimationFrame(updateTilt);
  }, { passive: true });

  windowEl.addEventListener('pointerleave', () => {
    isHovered = false;
    targetRotateX = 0;
    targetRotateY = 0;
  });
}

/* ==========================================================================
   11. ANIMATION PAUSE CONTROLLER
   Pauses infinite animations when tab is hidden or section is out of viewport.
   ========================================================================== */

function initAnimationPauseController() {
  // Pause when tab is not focused/visible
  document.addEventListener('visibilitychange', () => {
    document.documentElement.classList.toggle('animations-paused', document.hidden);
  });

  // Pause when animated sections scroll out of view
  if ('IntersectionObserver' in window) {
    const animatedSections = document.querySelectorAll('#hero, .sources-marquee-section, #modes, #cta');
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        entry.target.classList.toggle('anim-paused', !entry.isIntersecting);
      });
    }, { threshold: 0 });

    animatedSections.forEach((sec) => observer.observe(sec));
  }
}

/* ==========================================================================
   12. COUNT-UP NUMBERS
   800ms ease-out count-up animation for metric chips and PRISMA numbers.
   Respects prefers-reduced-motion: reduce (instant exact final value).
   ========================================================================== */

function animateCountUp(element, endVal, suffix = '', prefix = '') {
  if (!element) return;
  const target = parseInt(endVal, 10);
  if (isNaN(target)) {
    element.textContent = `${prefix}${endVal}${suffix}`;
    return;
  }

  // A later call (e.g. the scroll observer firing mid-animation) must reuse the
  // real target, not the intermediate number on screen, and stop this run.
  element.dataset.countUpTarget = String(target);
  const token = (element._countUpToken || 0) + 1;
  element._countUpToken = token;

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced) {
    element.textContent = `${prefix}${target}${suffix}`;
    return;
  }

  const duration = 800;
  const startTime = performance.now();

  function step(now) {
    if (element._countUpToken !== token) return;
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    // Cubic ease-out
    const easeProgress = 1 - Math.pow(1 - progress, 3);
    const currentVal = Math.round(target * easeProgress);

    element.textContent = `${prefix}${currentVal}${suffix}`;

    if (progress < 1) {
      requestAnimationFrame(step);
    } else {
      element.textContent = `${prefix}${target}${suffix}`;
    }
  }

  requestAnimationFrame(step);
}

function initCountUpMetrics() {
  if (!('IntersectionObserver' in window)) return;

  const countSection = document.querySelector('.example-question-box');
  if (!countSection) return;

  const observer = new IntersectionObserver((entries, obs) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        const disc = document.getElementById('metric-discovered');
        const scr = document.getElementById('metric-screened');
        const inc = document.getElementById('metric-included');
        if (disc) animateCountUp(disc, disc.dataset.countUpTarget || disc.textContent.trim());
        if (scr) animateCountUp(scr, scr.dataset.countUpTarget || scr.textContent.trim());
        if (inc) animateCountUp(inc, inc.dataset.countUpTarget || inc.textContent.trim());
        obs.unobserve(entry.target);
      }
    });
  }, { threshold: 0.3 });

  observer.observe(countSection);
}

/* ==========================================================================
   13. REAL RESEARCH OUTPUT GALLERY (assets/examples.json)
   Strictly uses textContent and safe DOM creation. No innerHTML with data.
   ========================================================================== */

let LANDING_EXAMPLES = [];

async function initExampleGallery() {
  const selectBtns = document.querySelectorAll('.example-select-btn');
  if (!selectBtns.length) return;

  try {
    const response = await fetch('/assets/examples.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    LANDING_EXAMPLES = await response.json();
  } catch (e) {
    console.warn('Could not load landing examples.json, keeping static markup:', e);
    return;
  }

  if (!Array.isArray(LANDING_EXAMPLES) || !LANDING_EXAMPLES.length) return;

  selectBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const exampleId = btn.getAttribute('data-example-id');
      const example = LANDING_EXAMPLES.find((e) => e.id === exampleId);
      if (example) {
        renderExample(example, btn);
      }
    });
  });

  renderExample(LANDING_EXAMPLES[0], selectBtns[0]);
}

function plainSnippet(md, max) {
  const text = String(md || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/^\s*[-*_]{3,}\s*$/gm, '')
    .replace(/^\s*#{1,6}\s*(.+?)\s*$/gm, '$1. ')
    .replace(/^\s*\*\*(.+?)\*\*\s*$/gm, '$1. ')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/[*_`>#]/g, '')
    .replace(/^\s*Literature Review:[^.]*\.\s*/i, '')
    .replace(/\s+/g, ' ')
    .replace(/\s+([.,;:])/g, '$1')
    .replace(/(\.\s*){2,}/g, '. ')
    .trim();

  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const lastSpace = cut.lastIndexOf(' ');
  return (lastSpace > 0 ? cut.slice(0, lastSpace) : cut).replace(/[\s.,;:—-]+$/, '') + '…';
}

function prettifyMeta(meta) {
  const NAMES = {
    openalex: 'OpenAlex',
    semantic_scholar: 'Semantic Scholar',
    crossref: 'Crossref',
    pubmed: 'PubMed',
    arxiv: 'arXiv',
    europe_pmc: 'Europe PMC',
    doaj: 'DOAJ',
    datacite: 'DataCite',
    opencitations: 'OpenCitations',
    core: 'CORE',
    unpaywall: 'Unpaywall'
  };
  const ORDER = ['OpenAlex', 'Semantic Scholar', 'Crossref', 'PubMed', 'arXiv', 'Europe PMC', 'DOAJ', 'DataCite', 'OpenCitations'];

  let clean = String(meta || '').replace(/DeepResearch/g, 'CiteProof');
  return clean.replace(/Indexed via\s+(.+)$/i, (_, list) => {
    const sources = [...new Set(list.split(/,\s*/)
      .map((s) => s.trim())
      .filter((s) => s && !/fallback/i.test(s))
      .map((s) => NAMES[s.toLowerCase()] || s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())))];

    sources.sort((a, b) => {
      const ia = ORDER.indexOf(a);
      const ib = ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });

    const shown = sources.slice(0, 5).join(', ');
    return 'Indexed via ' + (sources.length > 5 ? `${shown} +${sources.length - 5} more` : shown);
  });
}

function renderExample(example, activeBtn) {
  document.querySelectorAll('.example-select-btn').forEach((btn) => {
    const isActive = btn === activeBtn;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-selected', String(isActive));
    btn.setAttribute('data-state', isActive ? 'active' : 'inactive');
    btn.setAttribute('tabindex', isActive ? '0' : '-1');
  });

  const questionEl = document.getElementById('example-question-text');
  if (questionEl) {
    questionEl.textContent = `"${example.question}"`;
  }

  const discoveredEl = document.getElementById('metric-discovered');
  const screenedEl = document.getElementById('metric-screened');
  const includedEl = document.getElementById('metric-included');
  if (discoveredEl && example.stats) animateCountUp(discoveredEl, example.stats.discovered);
  if (screenedEl && example.stats) animateCountUp(screenedEl, example.stats.screened);
  if (includedEl && example.stats) animateCountUp(includedEl, example.stats.included);

  const titleEl = document.getElementById('paper-mock-title');
  const metaEl = document.getElementById('paper-mock-meta');
  const abstractEl = document.getElementById('paper-mock-abstract-text');
  const bodyEl = document.getElementById('paper-mock-body-text');
  const citationsEl = document.getElementById('paper-mock-citations-list');

  if (example.paper) {
    if (titleEl) titleEl.textContent = example.paper.title || '';
    if (metaEl) metaEl.textContent = prettifyMeta(example.paper.meta);
    if (abstractEl) abstractEl.textContent = plainSnippet(example.paper.abstract, 400);
    if (bodyEl) bodyEl.textContent = plainSnippet(example.paper.body, 260);

    if (citationsEl) {
      citationsEl.textContent = '';
      if (Array.isArray(example.paper.citations)) {
        example.paper.citations.forEach((cit) => {
          const li = document.createElement('li');
          li.textContent = cit;
          citationsEl.appendChild(li);
        });
      }
    }
  }

  renderPrismaFlow(example.prisma);
  renderEvidenceTable(example.evidence_rows);

  const runBtn = document.getElementById('run-example-btn');
  if (runBtn) {
    runBtn.href = `/app?mode=researchmode&q=${encodeURIComponent(example.question)}`;
  }
}

function renderPrismaFlow(prismaData) {
  const container = document.getElementById('prisma-diagram-preview');
  if (!container || !prismaData) return;
  container.textContent = '';

  function createPrismaBox(titleText, statText, subText, isExcluded) {
    const statStr = statText == null ? '' : String(statText);
    const box = document.createElement('div');
    box.className = isExcluded ? 'prisma-box prisma-box-excluded' : 'prisma-box';

    const title = document.createElement('div');
    title.className = 'prisma-box-title';
    title.textContent = titleText;

    const stat = document.createElement('div');
    stat.className = 'prisma-box-stat';
    stat.textContent = statStr;

    // Run count-up on PRISMA stat number if matchable
    const numMatch = statStr.match(/^(\d+)(.*)$/);
    if (numMatch) {
      animateCountUp(stat, numMatch[1], numMatch[2]);
    }

    const sub = document.createElement('div');
    sub.className = 'prisma-box-sub';
    sub.textContent = subText;

    box.appendChild(title);
    box.appendChild(stat);
    box.appendChild(sub);
    return box;
  }

  function createRow(box1, box2) {
    const row = document.createElement('div');
    row.className = 'prisma-step-row';
    row.appendChild(box1);
    if (box2) row.appendChild(box2);
    return row;
  }

  const p = prismaData;
  if (p.identification && p.dedup_excluded) {
    const b1 = createPrismaBox('1. Identification', p.identification.stat, p.identification.sub, false);
    const b2 = createPrismaBox('Deduplication', p.dedup_excluded.stat, p.dedup_excluded.sub, true);
    container.appendChild(createRow(b1, b2));
  }

  if (p.screening && p.irrelevant_excluded) {
    const b3 = createPrismaBox('2. Screening', p.screening.stat, p.screening.sub, false);
    const b4 = createPrismaBox('Irrelevant', p.irrelevant_excluded.stat, p.irrelevant_excluded.sub, true);
    container.appendChild(createRow(b3, b4));
  }

  if (p.included) {
    const b5 = createPrismaBox('3. Included Corpus', p.included.stat, p.included.sub, false);
    container.appendChild(createRow(b5));
  }
}

function renderEvidenceTable(rows) {
  const tbody = document.getElementById('evidence-table-body');
  if (!tbody || !Array.isArray(rows)) return;
  tbody.textContent = '';

  rows.forEach((row) => {
    const tr = document.createElement('tr');

    const tdStudy = document.createElement('td');
    const strongStudy = document.createElement('strong');
    strongStudy.textContent = row.study || '';
    tdStudy.appendChild(strongStudy);

    const tdFocus = document.createElement('td');
    tdFocus.textContent = row.focus || '';

    const tdMethod = document.createElement('td');
    tdMethod.textContent = row.methodology || '';

    const tdFinding = document.createElement('td');
    tdFinding.textContent = row.finding || '';

    const tdLevel = document.createElement('td');
    const spanLevel = document.createElement('span');
    spanLevel.className = 'evidence-tag';
    spanLevel.textContent = row.level || '';
    tdLevel.appendChild(spanLevel);

    tr.appendChild(tdStudy);
    tr.appendChild(tdFocus);
    tr.appendChild(tdMethod);
    tr.appendChild(tdFinding);
    tr.appendChild(tdLevel);

    tbody.appendChild(tr);
  });
}


/* ==========================================================================
   LIVE GITHUB STAR COUNT (hero, top right)
   Reads the real count from the GitHub API and caches it for 10 minutes.
   If the request fails and nothing is cached, the box just says "Star".
   ========================================================================== */

function initGithubStars() {
  const el = document.getElementById('gh-star-count');
  if (!el) return;

  const KEY = 'citeproof_gh_stars_v1';
  const TTL_MS = 10 * 60 * 1000;
  const API = 'https://api.github.com/repos/Aryan-Pardeshi/CiteProof-AI';

  function format(n) {
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(n);
  }

  function show(n) {
    el.textContent = format(n);
  }

  let cached = null;
  try {
    cached = JSON.parse(localStorage.getItem(KEY) || 'null');
  } catch {
    cached = null;
  }

  if (cached && Number.isFinite(cached.n)) {
    show(cached.n);
    if (Date.now() - cached.t < TTL_MS) return;
  }

  fetch(API, { headers: { Accept: 'application/vnd.github+json' } })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (!data || !Number.isFinite(data.stargazers_count)) return;
      show(data.stargazers_count);
      try {
        localStorage.setItem(KEY, JSON.stringify({ n: data.stargazers_count, t: Date.now() }));
      } catch {
        // Storage may be blocked; the count is still shown
      }
    })
    .catch(() => {
      // Offline or rate limited: keep whatever is shown
    });
}

/* ==========================================================================
   EXPANDABLE OUTPUT WINDOW
   The example output shows its first screen with a fade; the button opens it
   to the full height. Short tabs (no more text than fits) need no button.
   ========================================================================== */

function initOutputExpand() {
  const win = document.querySelector('.output-window');
  const body = document.getElementById('output-window-body');
  const btn = document.getElementById('output-expand');
  if (!win || !body || !btn) return;

  const COLLAPSED_PX = 380;
  const label = btn.querySelector('.output-expand-label');
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function activePaneHeight() {
    const pane = body.querySelector('.window-tab-pane[data-state="active"], .window-tab-pane.active');
    return pane ? pane.scrollHeight : body.scrollHeight;
  }

  function refresh() {
    const short = activePaneHeight() <= COLLAPSED_PX + 40;
    if (short) {
      win.setAttribute('data-short', 'true');
    } else {
      win.removeAttribute('data-short');
    }
  }

  function setOpen(open) {
    win.setAttribute('data-expanded', open ? 'true' : 'false');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (label) label.textContent = open ? 'Collapse output' : 'Expand output';

    if (reduce) {
      body.style.maxHeight = '';
      return;
    }

    if (open) {
      body.style.maxHeight = COLLAPSED_PX + 'px';
      void body.offsetHeight; // start the transition from the collapsed height
      body.style.maxHeight = body.scrollHeight + 'px';
      setTimeout(() => {
        if (win.getAttribute('data-expanded') === 'true') body.style.maxHeight = 'none';
      }, 520);
    } else {
      body.style.maxHeight = body.scrollHeight + 'px';
      void body.offsetHeight;
      body.style.maxHeight = COLLAPSED_PX + 'px';
      win.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }

  btn.addEventListener('click', () => {
    setOpen(win.getAttribute('data-expanded') !== 'true');
  });

  // A different tab or example changes the text length: measure again once it has rendered
  document.addEventListener('click', (e) => {
    if (e.target.closest('.window-subtab-btn, .example-select-btn')) {
      setTimeout(refresh, 80);
      setTimeout(refresh, 700);
    }
  });

  window.addEventListener('resize', refresh, { passive: true });
  setTimeout(refresh, 300);
  setTimeout(refresh, 1200);
}
