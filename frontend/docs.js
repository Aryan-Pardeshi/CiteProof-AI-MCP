/*
 * CiteProof documentation shell behaviour: search, copy buttons, the
 * "On this page" outline and the mobile navigation drawer. No framework, no
 * network calls and no third-party code.
 */
(function () {
  'use strict';

  /* The docs are designed for the dark theme. Drop the light-mode class that
     landing.js adds from a saved preference, without changing that preference. */
  function forceDark() {
    document.documentElement.classList.remove('light-mode');
  }
  forceDark();
  document.addEventListener('DOMContentLoaded', forceDark);
  window.addEventListener('load', forceDark);

  /* Page titles and headings only: small enough to ship inline and accurate
     enough to find a section without a search service. */
  var DOCS_INDEX = [
    {
      url: '/docs',
      title: 'Documentation overview',
      headings: [
        ['what-citeproof-does', 'What CiteProof does'],
        ['choose-your-workflow', 'Choose your workflow'],
        ['first-run', 'Your first run'],
        ['where-to-go-next', 'Where to go next'],
        ['keeping-docs-accurate', 'Keeping these docs accurate']
      ]
    },
    {
      url: '/docs/research-workflow',
      title: 'Research workflow',
      headings: [
        ['scope', 'Define the question and scope'],
        ['pipeline', 'How the research pipeline works'],
        ['review-gates', 'Human review gates'],
        ['sources', 'Sources, citations and evidence'],
        ['outputs', 'Reports and exports'],
        ['automated-vs-human', 'What is automated and what needs you'],
        ['limits', 'Limits and expectations']
      ]
    },
    {
      url: '/docs/self-hosting',
      title: 'Self-hosting',
      headings: [
        ['requirements', 'Requirements'],
        ['configure', 'Clone and configure'],
        ['docker', 'Run with Docker'],
        ['local', 'Run for development'],
        ['data', 'Where your data lives'],
        ['limits', 'Time, cost and limits'],
        ['mcp-separate', 'The MCP server is separate']
      ]
    },
    {
      url: '/docs/troubleshooting',
      title: 'Troubleshooting',
      headings: [
        ['app', 'App and run problems'],
        ['mcp-setup', 'Claude Science and MCP setup'],
        ['mcp-tools', 'Missing tools or failed tool calls'],
        ['data', 'Review data and storage'],
        ['reporting-bugs', 'Report a reproducible bug'],
        ['authoritative-sources', 'Authoritative sources']
      ]
    },
    {
      url: '/docs/mcp',
      title: 'MCP reference',
      headings: [
        ['understand', 'How Claude Science and CiteProof MCP fit together'],
        ['architecture', 'Architecture and data flow'],
        ['does-not', 'What the server does not do'],
        ['connect', 'Connect CiteProof MCP to Claude Science'],
        ['prerequisites', 'Prerequisites'],
        ['claude-science-steps', 'Install the Claude Science connector'],
        ['bundle-fallback', 'Fallback: build a bundle'],
        ['confirm-tools', 'Confirm the tools are available'],
        ['install-vs-use', 'Installing is not using'],
        ['desktop-extension', 'Claude Desktop extension and npx'],
        ['workflow', 'Follow the end-to-end review workflow'],
        ['tool-reference', 'Tool reference'],
        ['tools-core', 'Review setup and core checks'],
        ['tool-review-create', 'review_create'],
        ['tool-review-list', 'review_list'],
        ['tool-verify-dois', 'verify_dois'],
        ['tool-resolve-citations', 'resolve_citations'],
        ['tool-lint-prose', 'lint_prose'],
        ['tools-protocol', 'Protocol'],
        ['tool-protocol-record', 'protocol_record'],
        ['tools-prisma', 'PRISMA and screening'],
        ['tool-prisma-record-search', 'prisma_record_search'],
        ['tool-dedup-papers', 'dedup_papers'],
        ['tool-prisma-record-dedup', 'prisma_record_dedup'],
        ['tool-reviewer-record', 'reviewer_record'],
        ['tool-screening-stage-set', 'screening_stage_set'],
        ['tool-screening-stage-close', 'screening_stage_close'],
        ['tool-screening-record', 'screening_record'],
        ['tool-screening-record-many', 'screening_record_many'],
        ['tool-screening-list', 'screening_list'],
        ['tool-screening-agreement', 'screening_agreement'],
        ['tool-prisma-status', 'prisma_status'],
        ['tool-prisma-flow-diagram', 'prisma_flow_diagram'],
        ['tool-prisma-checklist', 'prisma_checklist'],
        ['tools-identity', 'Paper and study identity'],
        ['tool-paper-resolve', 'paper_resolve'],
        ['tool-paper-alias-record', 'paper_alias_record'],
        ['tool-study-link', 'study_link'],
        ['tool-study-unlink', 'study_unlink'],
        ['tool-study-overview', 'study_overview'],
        ['tools-validation', 'Search validation'],
        ['tool-recall-seed-record', 'recall_seed_record'],
        ['tool-recall-check', 'recall_check'],
        ['tools-evidence', 'Evidence extraction'],
        ['tool-evidence-record', 'evidence_record'],
        ['tool-evidence-table', 'evidence_table'],
        ['tools-stats', 'Statistical checks'],
        ['tool-stats-check-ratio-ci', 'stats_check_ratio_ci'],
        ['tool-stats-recompute-2x2', 'stats_recompute_2x2'],
        ['tool-stats-check-evidence', 'stats_check_evidence'],
        ['tools-claims', 'Claims and risk of bias'],
        ['tool-claim-record', 'claim_record'],
        ['tool-claim-report', 'claim_report'],
        ['tool-rob-record', 'rob_record'],
        ['tool-rob-summary', 'rob_summary'],
        ['tools-reporting', 'Reporting and manuscript checks'],
        ['tool-prisma-excluded-studies', 'prisma_excluded_studies'],
        ['tool-manuscript-check', 'manuscript_check'],
        ['tools-review', 'History, whole-review views and export'],
        ['tool-review-history', 'review_history'],
        ['tool-review-status', 'review_status'],
        ['tool-export-bundle', 'export_bundle'],
        ['limits', 'Limits and validation rules'],
        ['paper-refs', 'Paper identity'],
        ['caps', 'Input and list caps'],
        ['quote-checks', 'How quote checks work'],
        ['data', 'Data handling and privacy'],
        ['storage', 'Local storage'],
        ['network', 'Network calls'],
        ['privacy', 'What stays on your machine'],
        ['errors', 'Handling ok:false'],
        ['compatibility', 'Client compatibility'],
        ['troubleshooting', 'Troubleshooting'],
        ['maintaining', 'Keeping these docs accurate']
      ]
    },
    {
      url: '/mcp',
      title: 'Claude Science setup hub',
      headings: [
        ['capabilities', 'What the MCP server does'],
        ['review-flow', 'How a review flows'],
        ['local-data', 'Local by default'],
        ['why-citeproof', 'Why CiteProof with Claude Science'],
        ['setup', 'Set up CiteProof'],
        ['compatibility', 'Compatibility'],
        ['next-steps', 'Next steps'],
        ['mcp-final-title', 'Keep every review decision in view.']
      ]
    }
  ];

  var sidebar = document.getElementById('docs-sidebar');
  var overlay = document.getElementById('docs-overlay');
  var menuToggle = document.getElementById('docs-menu-toggle');

  function setDrawer(open) {
    if (!sidebar || !overlay || !menuToggle) {
      return;
    }
    sidebar.classList.toggle('is-open', open);
    overlay.classList.toggle('is-open', open);
    document.body.classList.toggle('docs-drawer-open', open);
    menuToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  if (menuToggle) {
    menuToggle.addEventListener('click', function () {
      setDrawer(!sidebar.classList.contains('is-open'));
    });
  }

  if (overlay) {
    overlay.addEventListener('click', function () {
      setDrawer(false);
    });
  }

  if (sidebar) {
    sidebar.addEventListener('click', function (event) {
      var link = event.target && event.target.closest ? event.target.closest('a') : null;
      if (link && window.matchMedia('(max-width: 960px)').matches) {
        setDrawer(false);
      }
    });
  }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      var wasOpen = Boolean(sidebar && sidebar.classList.contains('is-open'));
      setDrawer(false);
      if (wasOpen && menuToggle) {
        menuToggle.focus();
      }
    }
  });

  /* ------------------------------------------------------------------------
     Copy buttons
     ------------------------------------------------------------------------ */

  var installTabs = Array.prototype.slice.call(
    document.querySelectorAll('[data-mcp-command]')
  );
  var installCommand = document.getElementById('mcp-install-command');
  var installPanel = document.getElementById('mcp-install-panel');
  var installCopy = document.querySelector('.mcp-command .mcp-copy-btn');

  function activateInstallTab(tab, moveFocus) {
    if (!installCommand || !installPanel) {
      return;
    }
    installTabs.forEach(function (item) {
      var active = item === tab;
      item.classList.toggle('is-active', active);
      item.setAttribute('aria-selected', active ? 'true' : 'false');
      item.setAttribute('tabindex', active ? '0' : '-1');
    });
    installCommand.textContent = tab.getAttribute('data-mcp-command') || '';
    installPanel.setAttribute('aria-labelledby', tab.id);
    if (installCopy) {
      installCopy.setAttribute(
        'aria-label',
        tab.getAttribute('data-mcp-label') || 'Copy the install command'
      );
      installCopy.removeAttribute('data-copied');
      var label = installCopy.querySelector('[data-copy-label]');
      var status = installCopy.querySelector('[data-copy-status]');
      if (label) {
        label.textContent = 'Copy';
      }
      if (status) {
        status.textContent = '';
      }
    }
    if (moveFocus) {
      tab.focus();
    }
  }

  installTabs.forEach(function (tab, index) {
    tab.addEventListener('click', function () {
      activateInstallTab(tab, false);
    });
    tab.addEventListener('keydown', function (event) {
      var next = index;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        next = (index + 1) % installTabs.length;
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        next = (index + installTabs.length - 1) % installTabs.length;
      } else if (event.key === 'Home') {
        next = 0;
      } else if (event.key === 'End') {
        next = installTabs.length - 1;
      } else {
        return;
      }
      event.preventDefault();
      /* landing.js also handles arrow keys on every tablist; handle each key once. */
      event.stopPropagation();
      activateInstallTab(installTabs[next], true);
    });
  });

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.top = '-1000px';
      document.body.appendChild(area);
      area.select();
      try {
        if (document.execCommand('copy')) {
          resolve();
        } else {
          reject(new Error('copy failed'));
        }
      } catch (error) {
        reject(error);
      } finally {
        document.body.removeChild(area);
      }
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('.docs-code'), function (block) {
    var code = block.querySelector('pre code');
    if (!code) {
      return;
    }
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'docs-copy';
    button.textContent = 'Copy';
    button.setAttribute('aria-label', 'Copy code example');
    button.addEventListener('click', function () {
      copyText(code.innerText.replace(/\n$/, '')).then(function () {
        button.textContent = 'Copied';
        button.setAttribute('data-copied', 'true');
        window.setTimeout(function () {
          button.textContent = 'Copy';
          button.removeAttribute('data-copied');
        }, 1500);
      }).catch(function () {
        button.textContent = 'Select and copy';
        window.setTimeout(function () {
          button.textContent = 'Copy';
        }, 2000);
      });
    });
    block.appendChild(button);
  });

  /* Buttons that copy a named element or a supplied string. The visible label
     and a polite live region both report the result. */
  Array.prototype.forEach.call(
    document.querySelectorAll('[data-copy-target], [data-copy-text]'),
    function (button) {
      var label = button.querySelector('[data-copy-label]');
      var status = button.querySelector('[data-copy-status]');
      var resetTimer = null;

      button.addEventListener('click', function () {
        var targetId = button.getAttribute('data-copy-target');
        var text = button.getAttribute('data-copy-text') || '';
        if (targetId) {
          var target = document.getElementById(targetId);
          if (!target) {
            return;
          }
          text = target.innerText;
        }
        text = text.replace(/\s+$/, '');
        if (!text) {
          return;
        }
        copyText(text).then(function () {
          button.setAttribute('data-copied', 'true');
          if (label) {
            label.textContent = 'Copied';
          }
          if (status) {
            status.textContent = 'Copied to clipboard';
          }
          window.clearTimeout(resetTimer);
          resetTimer = window.setTimeout(function () {
            button.removeAttribute('data-copied');
            if (label) {
              label.textContent = 'Copy';
            }
            if (status) {
              status.textContent = '';
            }
          }, button.classList.contains('mcp-copy-btn') ? 1500 : 2000);
        }).catch(function () {
          if (label) {
            label.textContent = 'Press Ctrl+C';
          }
          if (status) {
            status.textContent = 'Copy failed. Select the command and copy it manually.';
          }
          window.clearTimeout(resetTimer);
          resetTimer = window.setTimeout(function () {
            button.removeAttribute('data-copied');
            if (label) {
              label.textContent = 'Copy';
            }
            if (status) {
              status.textContent = '';
            }
          }, 3000);
        });
      });
    }
  );

  /* ------------------------------------------------------------------------
     "On this page" outline
     ------------------------------------------------------------------------ */

  var article = document.querySelector('.docs-article');
  var toc = document.getElementById('docs-toc-list');

  if (article && toc) {
    var headings = Array.prototype.filter.call(
      article.querySelectorAll('h2[id], h3[id]'),
      function (heading) {
        return heading.textContent.trim().length > 0;
      }
    );

    headings.forEach(function (heading) {
      var item = document.createElement('li');
      item.className = heading.tagName === 'H2' ? 'docs-toc-h2' : 'docs-toc-h3';
      var link = document.createElement('a');
      link.href = '#' + heading.id;
      link.textContent = heading.textContent.trim();
      item.appendChild(link);
      toc.appendChild(item);
    });

    if (!headings.length) {
      var tocColumn = toc.closest('.docs-toc');
      if (tocColumn) {
        tocColumn.hidden = true;
      }
    } else if ('IntersectionObserver' in window) {
      var links = Array.prototype.slice.call(toc.querySelectorAll('a'));
      var byId = {};
      links.forEach(function (link) {
        byId[link.getAttribute('href').slice(1)] = link;
      });
      var visible = {};
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            visible[entry.target.id] = true;
          } else {
            delete visible[entry.target.id];
          }
        });
        var active = null;
        headings.forEach(function (heading) {
          if (!active && visible[heading.id]) {
            active = heading.id;
          }
        });
        links.forEach(function (link) {
          link.classList.toggle('is-active', active !== null && link === byId[active]);
        });
      }, { rootMargin: '-80px 0px -68% 0px', threshold: 0 });
      headings.forEach(function (heading) {
        observer.observe(heading);
      });
    }
  }

  /* ------------------------------------------------------------------------
     Search
     ------------------------------------------------------------------------ */

  var input = document.getElementById('docs-search-input');
  var results = document.getElementById('docs-search-results');

  function search(query) {
    var term = query.trim().toLowerCase();
    if (!term) {
      return [];
    }
    var hits = [];
    DOCS_INDEX.forEach(function (page) {
      if (page.title.toLowerCase().indexOf(term) !== -1) {
        hits.push({ url: page.url, page: page.title, label: page.title });
      }
      page.headings.forEach(function (heading) {
        if (heading[1].toLowerCase().indexOf(term) !== -1) {
          hits.push({
            url: page.url + '#' + heading[0],
            page: page.title,
            label: heading[1]
          });
        }
      });
    });
    return hits.slice(0, 12);
  }

  function renderResults(query) {
    if (!results) {
      return;
    }
    var hits = search(query);
    results.textContent = '';
    if (!query.trim()) {
      results.hidden = true;
      return;
    }
    results.hidden = false;
    if (!hits.length) {
      var empty = document.createElement('p');
      empty.textContent = 'No matching page or heading.';
      results.appendChild(empty);
      return;
    }
    var list = document.createElement('ul');
    hits.forEach(function (hit) {
      var item = document.createElement('li');
      var link = document.createElement('a');
      link.href = hit.url;
      var pageName = document.createElement('span');
      pageName.className = 'docs-search-hit-page';
      pageName.textContent = hit.page;
      link.appendChild(pageName);
      link.appendChild(document.createTextNode(hit.label));
      item.appendChild(link);
      list.appendChild(item);
    });
    results.appendChild(list);
  }

  if (input && results) {
    input.addEventListener('input', function () {
      renderResults(input.value);
    });
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        input.value = '';
        renderResults('');
        input.blur();
        return;
      }
      if (event.key === 'ArrowDown' || event.key === 'Enter') {
        var first = results.querySelector('a');
        if (first) {
          event.preventDefault();
          first.focus();
        }
      }
    });
    document.addEventListener('keydown', function (event) {
      var tag = event.target && event.target.tagName;
      if (event.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') {
        event.preventDefault();
        input.focus();
      }
    });
  }
})();
