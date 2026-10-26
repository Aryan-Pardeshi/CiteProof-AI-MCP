// Inject cursor blinking keyframes dynamically
const cursorStyle = document.createElement('style');
cursorStyle.innerHTML = `
.streaming-cursor {
    color: var(--accent-purple);
    font-weight: bold;
    display: inline-block;
    margin-left: 2px;
    animation: cursor-blink 0.8s infinite steps(2);
}
@keyframes cursor-blink {
    0%, 100% { opacity: 0; }
    50% { opacity: 1; }
}
`;
document.head.appendChild(cursorStyle);

// Configuration
// The backend serves this page, so the API lives on the same origin. Opening
// index.html straight off disk or from a dev server on another port (e.g. 5500)
// falls back to the default backend port (http://localhost:8000).
const API_BASE_URL = window.API_BASE_URL || (
    (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
        ? (window.location.port === '8000' ? window.location.origin : 'http://localhost:8000')
        : (window.location.protocol === 'file:' ? 'http://localhost:8000' : 'https://deepresearch-ai.fastapicloud.dev')
);
let activeResearchController = null;
let activeRMController = null;

// Application State
const state = {
    mode: 'researchmode', // 'deepsearch' | 'researchmode'
    threadId: null,
    status: 'idle', 
    query: '',
    searchTopic: ['all'],
    ps: '',
    plan: [],
    workers: {},
    finalAnswer: '',
    citations: [],
    error: null,
    
    // Research Mode State
    rm: {
        threadId: null,
        status: 'idle',
        hitlCheckpoint: null,
        problemStatement: '',
        researchObjectives: [],
        researchQuestions: [],
        keywords: [],
        rawPapersCount: 0,
        screenedPapersCount: 0,
        literatureReview: '',
        researchGap: '',
        conceptualFramework: '',
        hypotheses: [],
        researchDesign: '',
        dataCollectionPlan: '',
        dataAnalysisPlan: '',
        results: '',
        discussion: '',
        implications: '',
        limitations: '',
        conclusion: '',
        futureScope: [],
        references: [],
        appendices: '',
        introduction: '',
        abstract: '',
        title: '',
        activeStage: 'scope_definition',
        completedStages: [],
        screenedPapers: [],
        searchProtocol: null,
        evidenceRecordsCount: 0,
        evidenceRecords: [],
        claims: [],
        prismaTracker: null,
        taxonomy: null,
        validationReport: null
    }
};

function setResearchBackdropActive(active) {
    document.body?.classList.toggle('research-active', Boolean(active));
}

// Research Mode Pipeline Stage Metadata (30 numbered stages + 3 checkpoints = 33 pipeline steps)
const RM_STAGES = [
    // ── Phase 1: Planning & Protocol ──────────────────────────────────────────
    { id: 'scope_definition',      name: '1. Scope & PICOC',         role: 'scoper'      },
    { id: 'protocol_agent',        name: '2. Search Protocol',       role: 'strategist'  },
    { id: 'keyword_extractor',     name: '3. Boolean Keywords',      role: 'extractor'   },
    { id: 'checkpoint_1',          name: 'Checkpoint 1: Protocol',   hitl: true          },
    // ── Phase 2: Retrieval & Screening ────────────────────────────────────────
    { id: 'paper_fetcher',         name: '4. Multi-Source Retrieval', role: 'fetcher'   },
    { id: 'citation_expander',     name: '5. Citation Graph Expand',  role: 'expander'  },
    { id: 'metadata_validator',    name: '6. Metadata Cleanup',    role: 'validator' },
    { id: 'paper_screener',        name: '7. Title/Abstract Screen',  role: 'screener'  },
    { id: 'fulltext_eligibility',  name: '8. Full-Text Eligibility',  role: 'screener'  },
    { id: 'quality_appraisal',     name: '9. Quality Appraisal',      role: 'appraiser' },
    // ── Phase 3: Evidence Extraction ─────────────────────────────────────────
    { id: 'evidence_extractor',    name: '10. Evidence Extraction',   role: 'extractor' },
    { id: 'quantitative_extractor',name: '11. Quantitative Data',     role: 'extractor' },
    { id: 'methodology_extractor', name: '12. Methodology Extraction',role: 'extractor' },
    { id: 'limitation_extractor',  name: '13. Limitation Extraction', role: 'extractor' },
    { id: 'provenance_agent',      name: '14. Provenance Anchoring',  role: 'validator' },
    { id: 'checkpoint_2',          name: 'Checkpoint 2: Evidence',   hitl: true          },
    // ── Phase 4: Theoretical Framing ─────────────────────────────────────────
    { id: 'taxonomy_agent',        name: '15. Evidence Taxonomy',    role: 'architect'  },
    { id: 'gap_analysis',          name: '16. Research Gaps',        role: 'analyst'    },
    { id: 'framework',             name: '17. Conceptual Framework', role: 'architect'  },
    { id: 'hypotheses',            name: '18. Hypotheses',           role: 'formulator' },
    { id: 'checkpoint_3',          name: 'Checkpoint 3: Hypotheses', hitl: true          },
    // ── Phase 5: Methodology & Full Paper Synthesis ───────────────────────────
    { id: 'research_design',       name: '19. Research Design',      role: 'methodologist' },
    { id: 'data_collection',       name: '20. Data Collection',      role: 'methodologist' },
    { id: 'data_analysis',         name: '21. Data Analysis',        role: 'methodologist' },
    { id: 'literature_review',     name: '22. Literature Review',    role: 'aggregator'   },
    { id: 'results',               name: '23. Results',              role: 'synthesizer'  },
    { id: 'discussion',            name: '24. Discussion',           role: 'interpreter'  },
    { id: 'limitations',           name: '25. Limitations',          role: 'critic'       },
    { id: 'conclusion',            name: '26. Conclusion',           role: 'summarizer'   },
    { id: 'references',            name: '27. References',           role: 'indexer'      },
    { id: 'introduction',          name: '28. Introduction',         role: 'framer'       },
    { id: 'abstract',              name: '29. Abstract',             role: 'summarizer'   },
    { id: 'title',                 name: '30. Title',                role: 'finalizer'    },
];

// Phased grouping for the 5-phase Evidence Pipeline
const RM_PHASES = [
    {
        id: 'phase_1',
        name: 'Planning & Protocol',
        badge: 'Phase 1 of 5',
        stages: ['scope_definition', 'protocol_agent', 'keyword_extractor', 'checkpoint_1']
    },
    {
        id: 'phase_2',
        name: 'Retrieval & Screening',
        badge: 'Phase 2 of 5',
        stages: ['paper_fetcher', 'citation_expander', 'metadata_validator', 'paper_screener', 'fulltext_eligibility', 'quality_appraisal']
    },
    {
        id: 'phase_3',
        name: 'Evidence Extraction',
        badge: 'Phase 3 of 5',
        stages: ['evidence_extractor', 'quantitative_extractor', 'methodology_extractor', 'limitation_extractor', 'provenance_agent', 'checkpoint_2']
    },
    {
        id: 'phase_4',
        name: 'Theoretical Framing',
        badge: 'Phase 4 of 5',
        stages: ['taxonomy_agent', 'gap_analysis', 'framework', 'hypotheses', 'checkpoint_3']
    },
    {
        id: 'phase_5',
        name: 'Methodology & Synthesis',
        badge: 'Phase 5 of 5',
        stages: [
            'research_design', 'data_collection', 'data_analysis', 'literature_review',
            'results', 'discussion', 'limitations', 'conclusion',
            'references', 'introduction', 'abstract', 'title'
        ]
    }
];

// Hidden nodes running without their own standalone grid tile
const RM_HIDDEN_STAGES = {
    scope_reviser: { label: 'Revising Scope', anchor: 'checkpoint_1' },
    fulltext_fetcher: { label: 'Fetching Full Text', anchor: 'paper_screener' },
    citation_validator: { label: 'Validating Citations', anchor: 'references' },
    claim_validator: { label: 'Checking Numbers', anchor: 'references' },
    integrity_auditor: { label: 'Research Integrity Pass', anchor: 'references' },
    figures: { label: 'Generating Figures', anchor: 'results' },
    future_scope: { label: 'Future Scope', anchor: 'conclusion' },
    appendices: { label: 'Appendices', anchor: 'title' }
};

// Maps the snake_case state payload from the backend onto the camelCase UI state
const RM_STATE_KEY_MAP = {
    hitl_checkpoint: 'hitlCheckpoint',
    problem_statement: 'problemStatement',
    research_objectives: 'researchObjectives',
    research_questions: 'researchQuestions',
    keywords: 'keywords',
    search_protocol: 'searchProtocol',
    raw_papers_count: 'rawPapersCount',
    screened_papers_count: 'screenedPapersCount',
    evidence_records_count: 'evidenceRecordsCount',
    evidence_records: 'evidenceRecords',
    evidence_spans: 'evidenceSpans',
    claims: 'claims',
    review_claims: 'reviewClaims',
    prisma_tracker: 'prismaTracker',
    taxonomy: 'taxonomy',
    validation_report: 'validationReport',
    literature_review: 'literatureReview',
    research_gap: 'researchGap',
    conceptual_framework: 'conceptualFramework',
    hypotheses: 'hypotheses',
    research_design: 'researchDesign',
    data_collection_plan: 'dataCollectionPlan',
    data_analysis_plan: 'dataAnalysisPlan',
    results: 'results',
    discussion: 'discussion',
    implications: 'implications',
    limitations: 'limitations',
    conclusion: 'conclusion',
    future_scope: 'futureScope',
    references: 'references',
    appendices: 'appendices',
    introduction: 'introduction',
    abstract: 'abstract',
    title: 'title'
};

// Keys copied through under their own name because the UI reads them directly.
const RM_PASSTHROUGH_KEYS = ['corpus_stats', 'status', 'hitl_checkpoint', 'prisma_tracker', 'validation_report'];

function applyRMStatePayload(payload) {
    if (!payload || typeof payload !== 'object') return;
    Object.entries(RM_STATE_KEY_MAP).forEach(([snake, camel]) => {
        const value = payload[snake];
        if (value !== undefined && value !== null && value !== '') {
            state.rm[camel] = value;
        }
    });
    RM_PASSTHROUGH_KEYS.forEach(key => {
        const value = payload[key];
        if (value !== undefined && value !== null && value !== '') {
            state.rm[key] = value;
        }
    });

    if (Array.isArray(payload.screened_papers)) {
        state.rm.screenedPapers = payload.screened_papers;
    } else if (Array.isArray(payload.raw_papers) && state.rm.screenedPapers.length === 0) {
        state.rm.screenedPapers = payload.raw_papers;
    }

    if (Array.isArray(payload.raw_papers)) state.rm.rawPapersCount = payload.raw_papers.length;
    if (Array.isArray(payload.screened_papers)) state.rm.screenedPapersCount = payload.screened_papers.length;
    if (Array.isArray(payload.evidence_records)) {
        state.rm.evidenceRecords = payload.evidence_records;
        state.rm.evidenceRecordsCount = payload.evidence_records.length;
    }

    // Corpus stats synchronization
    if (payload.corpus_stats) {
        updateCorpusStats(payload.corpus_stats);
    } else if (state.rm.corpus_stats) {
        updateCorpusStats(state.rm.corpus_stats);
    } else if (state.rm.rawPapersCount || state.rm.screenedPapersCount) {
        updateCorpusStats({
            retrieved: state.rm.rawPapersCount || 0,
            after_dedup: state.rm.rawPapersCount || 0,
            screened: state.rm.screenedPapersCount || 0,
            included: state.rm.screenedPapersCount || 0,
            fulltext_fetched: state.rm.screenedPapersCount || 0
        });
    }
}

// Accurately infers the active checkpoint from explicit checkpoint keys and state content
function inferCurrentRMCheckpoint(suggestedCp) {
    const raw = (suggestedCp || state.rm.hitlCheckpoint || state.rm.hitl_checkpoint || '').replace(/_(approved|revising)$/, '');
    // If explicitly pointing to downstream checkpoints (2, 3, 4), trust it
    if (raw === 'checkpoint_2' || raw === 'checkpoint_3' || raw === 'checkpoint_4') {
        return raw;
    }
    // If raw is empty, or raw is 'checkpoint_1', verify whether downstream stages already generated data
    if (state.rm.researchDesign || state.rm.dataCollectionPlan || state.rm.dataAnalysisPlan) {
        return 'checkpoint_4';
    }
    if (state.rm.hypotheses && Array.isArray(state.rm.hypotheses) && state.rm.hypotheses.length > 0) {
        return 'checkpoint_3';
    }
    if (state.rm.literatureReview || state.rm.conceptualFramework || (state.rm.screenedPapers && state.rm.screenedPapers.length > 0)) {
        return 'checkpoint_2';
    }
    return raw || 'checkpoint_1';
}

// Papers arrive from the backend already relevance-ranked in practice, but
// don't rely on that — sort explicitly so the checkpoint strip and library
// panel always show the strongest matches first regardless of arrival order.
function getScreenedPapers() {
    return [...state.rm.screenedPapers].sort(
        (a, b) => (b.relevance_score || 0) - (a.relevance_score || 0)
    );
}

// Escapes model- and user-authored text before it goes into an innerHTML string.
// Titles, objectives and hypotheses regularly contain <, > and & (e.g. "p < 0.05"),
// which silently swallowed the rest of the panel before this existed.
function escapeHtml(value) {
    if (value === undefined || value === null) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Returns the normalised href only for an http: or https: URL, else null, so a
// javascript: or data: URL from model output or paper metadata never becomes a link.
function safeHttpUrl(value) {
    if (value === undefined || value === null) return null;
    const str = String(value).trim();
    if (!str) return null;
    try {
        const parsed = new URL(str);
        if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.href;
    } catch {
        // Not a URL.
    }
    return null;
}

// marked, DOMPurify and lucide are vendored files. If one fails to load the page must
// fail closed safely instead of passing unescaped content to HTML sinks.
function sanitizeHtml(rawHtml) {
    if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
        return window.DOMPurify.sanitize(rawHtml);
    }
    return escapeHtml(rawHtml || '');
}

function renderMarkdown(md) {
    if (window.marked && typeof window.marked.parse === 'function') {
        const raw = window.marked.parse(md || '');
        if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
            return window.DOMPurify.sanitize(raw);
        }
        return `<pre class="markdown-fallback">${escapeHtml(md || '')}</pre>`;
    }
    return `<pre class="markdown-fallback">${escapeHtml(md || '')}</pre>`;
}

// Same as renderMarkdown but HTML-escapes first, so model text that contains
// `<`, `>` or `&` (e.g. "p < 0.05") can't inject markup, while **bold**,
// lists and other markdown still render. When marked wraps the result in a
// single <p>, the tags are dropped so one-line fields (e.g. a checkpoint
// summary) sit flush without picking up block margins.
function renderMarkdownSafe(md) {
    const safe = escapeHtml(md || '');
    if (window.marked && typeof window.marked.parse === 'function') {
        const html = window.marked.parse(safe).trim();
        const single = html.match(/^<p>([\s\S]*)<\/p>$/);
        const unwrapped = single ? single[1] : html;
        if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
            return window.DOMPurify.sanitize(unwrapped);
        }
        return escapeHtml(unwrapped);
    }
    return `<pre class="markdown-fallback">${safe}</pre>`;
}

let truncatableIdCounter = 0;

// Checkpoint 2 was showing researchGap/conceptualFramework in full — observed
// at ~2500 words on screen in one live test run. literatureReview already had
// a hard 400-char cut with no way to read past it. This gives every long
// snippet the same collapsed-by-default treatment with an actual way out.
function renderTruncatable(text, opts = {}) {
    const charLimit = opts.charLimit || 400;
    const safeText = text || '';
    const id = `trunc-${++truncatableIdCounter}`;
    const rendered = renderMarkdownSafe(safeText);

    if (safeText.length <= charLimit) {
        return `<div class="problem-statement-text">${rendered}</div>`;
    }

    return `
        <div class="problem-statement-text truncatable" id="${id}">${rendered}</div>
        <button type="button" class="truncate-toggle" data-truncate-target="${id}">Show full text</button>
    `;
}

function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
}

// Activity & Timer Monitors
const researchTimer = {
    startTime: null,
    timerId: null,
    elapsedSeconds: 0,
    isPaused: false,
    start() {
        this.stop();
        this.startTime = Date.now() - (this.elapsedSeconds * 1000);
        this.isPaused = false;
        this.updateDisplay();
        this.timerId = setInterval(() => {
            if (!this.isPaused) {
                this.elapsedSeconds = Math.floor((Date.now() - this.startTime) / 1000);
                this.updateDisplay();
            }
        }, 1000);
    },
    stop() {
        if (this.timerId) { clearInterval(this.timerId); this.timerId = null; }
    },
    reset() {
        this.stop(); this.startTime = null; this.elapsedSeconds = 0; this.updateDisplay();
    },
    updateDisplay() {
        const timerEl = document.getElementById('research-timer');
        if (timerEl) {
            const minutes = Math.floor(this.elapsedSeconds / 60);
            const seconds = this.elapsedSeconds % 60;
            timerEl.textContent = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        }
    }
};

// Research Mode Minimal Timer
const rmTimer = {
    startTime: null,
    timerId: null,
    elapsedSeconds: 0,
    isPaused: false,
    start(initialSeconds = null) {
        this.stop();
        if (typeof initialSeconds === 'number' && initialSeconds >= 0) {
            this.elapsedSeconds = initialSeconds;
        }
        this.startTime = Date.now() - (this.elapsedSeconds * 1000);
        this.isPaused = false;
        this.timerId = setInterval(() => {
            if (!this.isPaused) {
                this.elapsedSeconds = Math.floor((Date.now() - this.startTime) / 1000);
                this.updateDisplay();
            }
        }, 1000);
        this.updateDisplay();
    },
    pause() {
        this.isPaused = true;
        if (this.timerId) {
            clearInterval(this.timerId);
            this.timerId = null;
        }
        this.updateDisplay();
    },
    resume() {
        if (!this.startTime) {
            this.start(this.elapsedSeconds);
            return;
        }
        this.stop();
        this.startTime = Date.now() - (this.elapsedSeconds * 1000);
        this.isPaused = false;
        this.timerId = setInterval(() => {
            if (!this.isPaused) {
                this.elapsedSeconds = Math.floor((Date.now() - this.startTime) / 1000);
                this.updateDisplay();
            }
        }, 1000);
        this.updateDisplay();
    },
    stop() {
        if (this.timerId) {
            clearInterval(this.timerId);
            this.timerId = null;
        }
        this.updateDisplay();
    },
    reset() {
        this.stop();
        this.startTime = null;
        this.elapsedSeconds = 0;
        this.isPaused = false;
        this.updateDisplay();
    },
    updateDisplay() {
        const timerEl = document.getElementById('rm-research-timer');
        const container = document.getElementById('rm-timer-container');
        if (timerEl) {
            const minutes = Math.floor(this.elapsedSeconds / 60);
            const seconds = this.elapsedSeconds % 60;
            timerEl.textContent = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        }
        if (container) {
            if (state.rm && state.rm.status === 'completed') {
                container.classList.remove('running');
                container.classList.add('completed');
            } else if (!this.isPaused && this.timerId) {
                container.classList.remove('completed');
                container.classList.add('running');
            } else {
                container.classList.remove('running', 'completed');
            }
        }
    }
};

// Cold-start auto-reload guard. If the user lands on a cold backend instance
// where the backend takes 30-60s to wake up, the page can sit stuck with the
// offline banner visible. A bounded one-shot reload gives the page a single
// chance to cleanly recover once the backend spins up, without looping
// indefinitely if the backend is genuinely down or interrupting an active run.
const COLD_START_RELOAD_DELAY_MS = 50000;
const COLD_START_SESSION_KEY = 'coldStartReloadDone';

function initColdStartAutoReload() {
    setTimeout(() => {
        // 1. Guard against infinite reload loops: only attempt once per session.
        if (sessionStorage.getItem(COLD_START_SESSION_KEY)) {
            return;
        }

        // 2. Banner must be actively visible (not hidden or absent).
        const banner = dom.backendOfflineBanner || document.getElementById('backend-offline-banner');
        if (!banner || banner.style.display === 'none') {
            return;
        }

        // 3. Must be completely idle in both modes — never interrupt active work.
        // Re-checked at the 50s mark so runs started during the wait are safe.
        const isDeepSearchBusy = Boolean(state.threadId);
        const isResearchModeBusy = Boolean(state.rm && state.rm.threadId);
        if (isDeepSearchBusy || isResearchModeBusy) {
            return;
        }

        // Mark done in sessionStorage before reloading so subsequent loads won't repeat.
        sessionStorage.setItem(COLD_START_SESSION_KEY, 'true');
        location.reload();
    }, COLD_START_RELOAD_DELAY_MS);
}

// Live Paper Synthesized Counter
let currentPaperTotal = 33;

function animatePaperCounter(targetVal, duration = 800) {
    const el = dom.rmTotalPapers || document.getElementById('rm-total-papers');
    if (!el) return;
    const startVal = parseInt(el.textContent.replace(/,/g, ''), 10) || currentPaperTotal;
    const endVal = Number.isFinite(targetVal) ? Math.max(targetVal, 33) : 33;
    currentPaperTotal = endVal;

    if (startVal === endVal) {
        el.textContent = endVal.toLocaleString();
        return;
    }

    const startTime = performance.now();
    const range = endVal - startVal;

    function step(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const ease = 1 - (1 - progress) * (1 - progress);
        const current = Math.round(startVal + range * ease);
        el.textContent = current.toLocaleString();

        if (progress < 1) {
            requestAnimationFrame(step);
        } else {
            el.textContent = endVal.toLocaleString();
        }
    }
    requestAnimationFrame(step);
}

async function fetchLivePaperCount() {
    try {
        const res = await fetch(`${API_BASE_URL}/research-mode/total-papers`);
        if (res.ok) {
            const data = await res.json();
            const total = typeof data.total_papers === 'number' ? Math.max(data.total_papers, 33) : 33;
            animatePaperCounter(total, 600);
        }
    } catch (e) {
        console.warn('Failed to fetch live paper count:', e);
    }
}

// DOM Cache
let dom = {};

function initMobileDisclosurePanels() {
    const mobileLayout = window.matchMedia('(max-width: 640px)');
    const indexStrip = document.getElementById('index-strip');

    const syncDisclosureState = () => {
        const shouldExpand = !mobileLayout.matches;
        if (indexStrip) indexStrip.open = shouldExpand;
        if (dom.backendOfflineBanner) dom.backendOfflineBanner.open = shouldExpand;
    };

    syncDisclosureState();
    mobileLayout.addEventListener('change', syncDisclosureState);
}

document.addEventListener('DOMContentLoaded', () => {
    cacheDomElements();
    initMobileDisclosurePanels();
    initTheme();
    updateModeTimeEstimate(state.mode);
    initHeaderOffset();
    initGitHubStarCount();
    setupEventListeners();
    checkBackendHealth();
    initColdStartAutoReload();
    checkConfigGate();
    fetchLivePaperCount();
    renderRMPipelineTracker();
    restoreRMSessionOnLoad();
    
    // Check URL parameters for explicit mode selection and a pre-filled query
    try {
        const urlParams = new URLSearchParams(window.location.search);
        const requestedMode = (urlParams.get('mode') || '').toLowerCase();
        if (requestedMode === 'deepsearch' || requestedMode === 'researchmode') {
            switchMode(requestedMode);
        }

        const qParam = urlParams.get('q');
        if (qParam) {
            const targetInput = state.mode === 'researchmode' ? dom.rmPsInput : dom.queryInput;
            if (targetInput) {
                targetInput.value = qParam;
                targetInput.dispatchEvent(new Event('input', { bubbles: true }));
            }
        }
    } catch (e) {
        console.warn('URL mode/query param error:', e);
    }

    rmPlaceholderCycle = initCyclingPlaceholder(dom.rmPsInput, RM_PLACEHOLDER_EXAMPLES);
    dsPlaceholderCycle = initCyclingPlaceholder(dom.queryInput, DS_PLACEHOLDER_EXAMPLES);
    updateNewRunVisibility();
    initCanvasText();
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(initCanvasText);
    }
    refreshIcons();
});

// A single unhandled render error used to leave the page frozen with no clue why.
window.addEventListener('error', (e) => {
    console.error('Unhandled error:', e.error || e.message);
});
window.addEventListener('unhandledrejection', (e) => {
    console.error('Unhandled promise rejection:', e.reason);
});

let rmPlaceholderCycle = null;
let dsPlaceholderCycle = null;

// Example prompts the Core Problem Statement box types out when idle and empty.
const RM_PLACEHOLDER_EXAMPLES = [
    'Efficacy of cognitive behavioral therapy interventions for healthcare worker burnout...',
    'Impact of generative AI code assistants on software developer productivity...',
    'Macroeconomic predictors of digital health platform adoption in rural systems...',
    'Comparative analysis of transformer architectures for financial time-series forecasting...'
];

// Same idea for the DeepSearch query box.
const DS_PLACEHOLDER_EXAMPLES = [
    'What would you like to research today?',
    'Latest developments in solid-state EV battery chemistry...',
    'Competitive landscape for open-weight LLM inference providers...',
    'Regulatory shifts affecting cross-border crypto payments in 2026...'
];

// Types each example into the placeholder, pauses, deletes it, moves to the next.
// Stops while the field has focus or real content so it never fights the user.
function initCyclingPlaceholder(el, examples, opts = {}) {
    if (!el || !examples.length) return;
    const typeSpeed = opts.typeSpeed || 32;
    const deleteSpeed = opts.deleteSpeed || 16;
    const holdMs = opts.holdMs || 1800;
    const gapMs = opts.gapMs || 400;

    let exampleIndex = 0;
    let timerId = null;
    let running = false;

    function step(charIndex, deleting) {
        const text = examples[exampleIndex];
        el.placeholder = text.slice(0, charIndex);

        if (!deleting && charIndex < text.length) {
            timerId = setTimeout(() => step(charIndex + 1, false), typeSpeed);
        } else if (!deleting) {
            timerId = setTimeout(() => step(charIndex, true), holdMs);
        } else if (deleting && charIndex > 0) {
            timerId = setTimeout(() => step(charIndex - 1, true), deleteSpeed);
        } else {
            exampleIndex = (exampleIndex + 1) % examples.length;
            timerId = setTimeout(() => step(0, false), gapMs);
        }
    }

    function start() {
        if (running || el.value) return;
        running = true;
        step(0, false);
    }

    function stop() {
        running = false;
        if (timerId) { clearTimeout(timerId); timerId = null; }
    }

    el.addEventListener('focus', stop);
    el.addEventListener('input', () => { if (el.value) stop(); });
    el.addEventListener('blur', () => { if (!el.value) start(); });

    start();
    return { start, stop };
}

function cacheDomElements() {
    dom = {
        // App header & settings
        themeToggleBtn: document.getElementById('theme-toggle-btn'),
        newResearchBtn: document.getElementById('new-research-btn'),
        backendOfflineBanner: document.getElementById('backend-offline-banner'),
        githubStarBtn: document.getElementById('github-star-btn'),
        githubStarCount: document.getElementById('github-star-count'),
        githubStarIcon: document.getElementById('github-star-icon'),
        
        // Mode Tabs & Time Estimate
        tabDeepSearch: document.getElementById('tab-deepsearch'),
        tabResearchMode: document.getElementById('tab-researchmode'),
        modeTimeDeepSearch: document.getElementById('mode-time-deepsearch'),
        modeTimeResearchMode: document.getElementById('mode-time-researchmode'),

        // Setup Gate Modal
        setupGateModal: document.getElementById('setup-gate-modal'),
        gateLlmBaseUrl: document.getElementById('gate-llm-base-url'),
        gateLlmApiKey: document.getElementById('gate-llm-api-key'),
        gateModelPlanner: document.getElementById('gate-llm-model-planner'),
        gateModelResearcher: document.getElementById('gate-llm-model-researcher'),
        gateModelAggregator: document.getElementById('gate-llm-model-aggregator'),
        gateTavilyKey: document.getElementById('gate-tavily-key'),
        gateOpenalexEmail: document.getElementById('gate-openalex-email'),
        gateSubmitBtn: document.getElementById('gate-submit-btn'),
        gateSaveStatus: document.getElementById('gate-save-status'),

        // Panels
        landingPanel: document.getElementById('landing-panel'),
        approvalPanel: document.getElementById('approval-panel'),
        workspacePanel: document.getElementById('workspace-panel'),
        rmInputPanel: document.getElementById('rm-input-panel'),
        rmWorkspacePanel: document.getElementById('rm-workspace-panel'),

        // DeepSearch Inputs & Elements
        queryInput: document.getElementById('query-input'),
        queryCharCounter: document.getElementById('query-char-counter'),
        planResearchBtn: document.getElementById('plan-research-btn'),
        filterChips: document.getElementById('filter-chips'),
        approvalQueryDisplay: document.getElementById('approval-query-display'),
        approvalPsText: document.getElementById('approval-ps-text'),
        approvalSubtasksContainer: document.getElementById('approval-subtasks-container'),
        feedbackInput: document.getElementById('feedback-input'),
        feedbackCharCounter: document.getElementById('feedback-char-counter'),
        submitFeedbackBtn: document.getElementById('submit-feedback-btn'),
        approvePlanBtn: document.getElementById('approve-plan-btn'),
        approvalNewResearchBtn: document.getElementById('approval-new-research-btn'),
        workersListContainer: document.getElementById('workers-list-container'),
        reportOutput: document.getElementById('report-output'),
        reportStreamingIndicator: document.getElementById('report-streaming-indicator'),
        workspaceProgressBar: document.getElementById('workspace-progress-bar'),
        workspaceSourcesSection: document.getElementById('workspace-sources-section'),
        workspaceSourcesContainer: document.getElementById('workspace-sources-container'),
        copyMdBtn: document.getElementById('copy-md-btn'),
        downloadMdBtn: document.getElementById('download-md-btn'),
        workspaceNewResearchBtn: document.getElementById('workspace-new-research-btn'),

        // Research Mode Elements
        rmPsInput: document.getElementById('rm-ps-input'),
        rmPsCharCounter: document.getElementById('rm-ps-char-counter'),
        rmExampleQueriesGrid: document.getElementById('rm-example-queries-grid'),
        rmObjsInput: document.getElementById('rm-objs-input'),
        rmRqsInput: document.getElementById('rm-rqs-input'),
        rmModelPlanner: document.getElementById('rm-model-planner'),
        rmModelResearcher: document.getElementById('rm-model-researcher'),
        rmModelAggregator: document.getElementById('rm-model-aggregator'),
        rmTotalPapers: document.getElementById('rm-total-papers'),
        rmStartBtn: document.getElementById('rm-start-btn'),
        rmPipelineStepsGrid: document.getElementById('rm-pipeline-steps-grid'),
        rmPipelineStatusTag: document.getElementById('rm-pipeline-status-tag'),
        rmHitlPanel: document.getElementById('rm-hitl-panel'),
        rmHitlTitle: document.getElementById('rm-hitl-title'),
        rmHitlBadge: document.getElementById('rm-hitl-checkpoint-badge'),
        rmHitlBody: document.getElementById('rm-hitl-body'),
        rmHitlFeedbackInput: document.getElementById('rm-hitl-feedback-input'),
        rmHitlCharCounter: document.getElementById('rm-hitl-char-counter'),
        rmHitlReviseBtn: document.getElementById('rm-hitl-revise-btn'),
        rmHitlApproveBtn: document.getElementById('rm-hitl-approve-btn'),
        rmPaperTitle: document.getElementById('rm-paper-title'),
        rmPaperOutput: document.getElementById('rm-paper-output'),
        togglePaperOutlineBtn: document.getElementById('toggle-paper-outline-btn'),
        paperOutlineRail: document.getElementById('paper-outline-rail'),
        paperOutlineList: document.getElementById('paper-outline-list'),
        paperStatsBadges: document.getElementById('paper-stats-badges'),
        paperReadTimeVal: document.getElementById('paper-read-time-val'),
        paperWordCountVal: document.getElementById('paper-word-count-val'),
        rmCopyPaperBtn: document.getElementById('rm-copy-paper-btn'),
        rmExportPdfBtn: document.getElementById('rm-export-pdf-btn'),
        rmExportDropdown: document.getElementById('rm-export-dropdown'),

        statRetrieved: document.getElementById('stat-retrieved'),
        statDedup: document.getElementById('stat-dedup'),
        statScreened: document.getElementById('stat-screened'),
        statIncluded: document.getElementById('stat-included'),
        statFulltext: document.getElementById('stat-fulltext'),
        rmCorpusStatsBar: document.getElementById('rm-corpus-stats-bar'),
        rmLogDrawer: document.getElementById('rm-log-drawer'),
        rmLogBody: document.getElementById('rm-log-body'),
        rmLogCount: document.getElementById('rm-log-count'),
        rmEvidenceCard: document.getElementById('rm-evidence-card'),
        rmEvidenceMatrixView: document.getElementById('rm-evidence-matrix-view'),
        rmSourcesPanel: document.getElementById('rm-sources-panel'),
        rmSourcesGrid: document.getElementById('rm-sources-grid'),
        rmSourcesCountTag: document.getElementById('rm-sources-count-tag'),
        sourcesFilterBar: document.getElementById('sources-filter-bar'),
        paperDetailModal: document.getElementById('paper-detail-modal'),
        modalPaperTitle: document.getElementById('modal-paper-title'),
        modalPaperBody: document.getElementById('modal-paper-body'),
        modalCloseBtn: document.getElementById('modal-close-btn'),

        toastContainer: document.getElementById('toast-container')

    };
}

// System Environment Setup Gate Check
async function checkConfigGate() {
    try {
        const res = await fetch(`${API_BASE_URL}/config/status`);
        if (!res.ok) return;
        markBackendOnline();
        const data = await res.json();
        
        if (dom.rmModelPlanner && data.llm_model_planner) dom.rmModelPlanner.placeholder = data.llm_model_planner;
        if (dom.rmModelResearcher && data.llm_model_researcher) dom.rmModelResearcher.placeholder = data.llm_model_researcher;
        if (dom.rmModelAggregator && data.llm_model_aggregator) dom.rmModelAggregator.placeholder = data.llm_model_aggregator;

        if (!data.ok || (data.missing_required && data.missing_required.length > 0)) {
            // The gate writes to the server's own .env, so it only makes sense where
            // that is allowed. On a deployment with the config API locked it could
            // not save, so it stays hidden and the operator is told to set the environment variables instead.
            if (!data.config_writable) {
                showToast(
                    `Server is missing ${(data.missing_required || []).join(', ')}. ` +
                    `Set them as environment variables on the host.`,
                    'error'
                );
                return;
            }

            // Populate form defaults if present
            if (dom.gateLlmBaseUrl) dom.gateLlmBaseUrl.value = data.llm_base_url || 'https://api.deepseek.com';
            if (dom.gateModelPlanner) dom.gateModelPlanner.value = data.llm_model_planner || 'deepseek-chat';
            if (dom.gateModelResearcher) dom.gateModelResearcher.value = data.llm_model_researcher || 'deepseek-chat';
            if (dom.gateModelAggregator) dom.gateModelAggregator.value = data.llm_model_aggregator || 'deepseek-chat';
            if (dom.gateOpenalexEmail) dom.gateOpenalexEmail.value = data.openalex_email || '';
            
            dom.setupGateModal.style.display = 'flex';
        }
    } catch (e) {
        console.warn('Config check failed:', e);
    }
}

// Remembers the panel each mode was last on. Switching tabs used to hard-reset
// to the landing/input panel, so flipping to the other mode and back during a
// live run threw away the workspace view and looked like the run had vanished.
const lastPanelByMode = { deepsearch: null, researchmode: null };

// The header is fixed, so body reserves its height as padding. That height is
// not a constant: below 640px the header stacks into two rows, and its content
// changes at runtime (the "New Run" button appears mid-session). Any hardcoded
// offset drifts out of sync and either hides content behind the header or
// leaves a gap under it, so measure the real element instead.
function syncHeaderOffset() {
    const header = document.querySelector('header');
    if (!header) return;
    const height = Math.ceil(header.getBoundingClientRect().height);
    if (height > 0) {
        document.documentElement.style.setProperty('--header-offset', `${height}px`);
    }
}

function initHeaderOffset() {
    syncHeaderOffset();
    // ResizeObserver catches content-driven height changes too, not just viewport
    // resizes, which a window resize listener alone would miss.
    if (typeof ResizeObserver !== 'undefined') {
        const header = document.querySelector('header');
        if (header) new ResizeObserver(syncHeaderOffset).observe(header);
    } else {
        window.addEventListener('resize', syncHeaderOffset);
    }
    // Web fonts land after first paint and can change the header's height.
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(syncHeaderOffset).catch(() => {});
    }
}

// Both runtimes stay on screen; switching modes only moves the emphasis, so the
// figures are always comparable rather than one replacing the other.
function updateModeTimeEstimate(mode) {
    const ds = dom.modeTimeDeepSearch || document.getElementById('mode-time-deepsearch');
    const rm = dom.modeTimeResearchMode || document.getElementById('mode-time-researchmode');
    if (ds) ds.classList.toggle('is-active', mode === 'deepsearch');
    if (rm) rm.classList.toggle('is-active', mode !== 'deepsearch');
}

// GitHub repository live star count fetcher with 1-hour cache and safe fallback
const GITHUB_REPO_API = 'https://api.github.com/repos/Aryan-Pardeshi/CiteProof-AI-MCP';
const GITHUB_STARS_CACHE_KEY = 'deepresearch_github_stars';
const GITHUB_STARS_CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

async function initGitHubStarCount() {
    const countEl = dom.githubStarCount || document.getElementById('github-star-count');
    const iconEl = dom.githubStarIcon || document.getElementById('github-star-icon');
    if (!countEl) return;

    function renderCount(num) {
        if (typeof num === 'number' && num > 0) {
            countEl.textContent = num >= 1000 ? `${(num / 1000).toFixed(1)}k` : String(num);
            countEl.style.display = 'inline';
            if (iconEl) iconEl.style.display = 'inline-block';
        }
    }

    try {
        const cached = localStorage.getItem(GITHUB_STARS_CACHE_KEY);
        if (cached) {
            const parsed = JSON.parse(cached);
            if (parsed && typeof parsed.count === 'number' && (Date.now() - (parsed.timestamp || 0)) < GITHUB_STARS_CACHE_TTL_MS) {
                renderCount(parsed.count);
                return;
            }
        }
    } catch (e) {
        // Safe fallback on localStorage errors
    }

    try {
        const res = await fetch(GITHUB_REPO_API);
        if (res.ok) {
            const data = await res.json();
            if (data && typeof data.stargazers_count === 'number' && data.stargazers_count > 0) {
                renderCount(data.stargazers_count);
                try {
                    localStorage.setItem(GITHUB_STARS_CACHE_KEY, JSON.stringify({
                        count: data.stargazers_count,
                        timestamp: Date.now()
                    }));
                } catch (e) {
                    // Safe fallback
                }
            }
        }
    } catch (e) {
        // Silently fail safely
    }
}

function switchMode(newMode) {
    const previous = state.mode;
    const currentPanel = document.querySelector('.panel.active');
    if (currentPanel && previous) lastPanelByMode[previous] = currentPanel;

    state.mode = newMode;
    if (newMode === 'deepsearch') {
        dom.tabDeepSearch.classList.add('active');
        dom.tabResearchMode.classList.remove('active');
        switchPanel(lastPanelByMode.deepsearch || dom.landingPanel);
    } else {
        dom.tabResearchMode.classList.add('active');
        dom.tabDeepSearch.classList.remove('active');
        switchPanel(lastPanelByMode.researchmode || dom.rmInputPanel);
        fetchLivePaperCount();
    }
    updateModeTimeEstimate(newMode);
    updateNewRunVisibility();
    setTimeout(initCanvasText, 60);
}

// Setup Event Listeners
function setupEventListeners() {
    // Mode tabs
    dom.tabDeepSearch?.addEventListener('click', () => switchMode('deepsearch'));
    dom.tabResearchMode?.addEventListener('click', () => switchMode('researchmode'));

    // Theme toggle
    dom.themeToggleBtn?.addEventListener('click', toggleTheme);

    // 20-Stage Pipeline DAG Matrix Toggle
    const toggleGraphBtn = document.getElementById('rm-toggle-all-stages-btn');
    const allStagesCollapse = document.getElementById('rm-all-stages-collapse');
    const graphBtnLabel = document.getElementById('rm-graph-btn-label');
    toggleGraphBtn?.addEventListener('click', () => {
        if (!allStagesCollapse) return;
        const isHidden = allStagesCollapse.style.display === 'none';
        allStagesCollapse.style.display = isHidden ? 'block' : 'none';
        toggleGraphBtn.classList.toggle('active', isHidden);
        if (graphBtnLabel) {
            graphBtnLabel.textContent = isHidden ? 'Hide Full Matrix' : 'All 20 Stages';
        }
        refreshIcons();
    });

    // Settings Modal

    // Gate Modal Submit
    dom.gateSubmitBtn?.addEventListener('click', submitSetupGate);

    // DeepSearch Filters & Actions
    dom.filterChips?.addEventListener('click', (e) => {
        if (e.target.classList.contains('chip')) {
            document.querySelectorAll('#filter-chips .chip').forEach(c => c.classList.remove('active'));
            e.target.classList.add('active');
            state.searchTopic = [e.target.dataset.topic];
        }
    });

    // Delegated so it works for truncatable blocks rendered at any point
    // after this listener is attached (checkpoint panels, library panel).
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('.truncate-toggle');
        if (!btn) return;
        const target = document.getElementById(btn.dataset.truncateTarget);
        if (!target) return;
        const expanded = target.classList.toggle('expanded');
        btn.textContent = expanded ? 'Show less' : 'Show full text';
    });

    dom.planResearchBtn?.addEventListener('click', handlePlanResearch);
    dom.feedbackInput?.addEventListener('input', () => {
        const val = dom.feedbackInput.value.trim();
        dom.submitFeedbackBtn.style.display = val ? 'flex' : 'none';
        dom.approvePlanBtn.style.display = val ? 'none' : 'flex';
    });

    dom.submitFeedbackBtn?.addEventListener('click', handleRevision);
    dom.approvePlanBtn?.addEventListener('click', submitPlanApproval);
    dom.approvalNewResearchBtn?.addEventListener('click', resetToLanding);
    dom.workspaceNewResearchBtn?.addEventListener('click', resetToLanding);
    dom.newResearchBtn?.addEventListener('click', resetToLanding);

    dom.copyMdBtn?.addEventListener('click', () => copyToClipboard(state.finalAnswer, dom.copyMdBtn));
    dom.downloadMdBtn?.addEventListener('click', downloadMarkdownReport);

    // Research Mode Actions
    dom.rmStartBtn?.addEventListener('click', handleRMStart);
    dom.rmHitlReviseBtn?.addEventListener('click', () => {
        const feedback = dom.rmHitlFeedbackInput.value.trim();
        if (!feedback) {
            showToast('Type what you want changed, then request revisions.', 'warning');
            return;
        }
        handleRMApprove(feedback);
    });
    dom.rmHitlApproveBtn?.addEventListener('click', () => handleRMApprove('approve'));
    dom.rmCopyPaperBtn?.addEventListener('click', () => copyToClipboard(getPaperMarkdown(), dom.rmCopyPaperBtn));

    // Outline rail toggle
    dom.togglePaperOutlineBtn?.addEventListener('click', () => {
        if (dom.paperOutlineRail) {
            const isHidden = dom.paperOutlineRail.style.display === 'none';
            dom.paperOutlineRail.style.display = isHidden ? 'flex' : 'none';
            if (dom.togglePaperOutlineBtn) {
                dom.togglePaperOutlineBtn.classList.toggle('active', isHidden);
            }
        }
    });

    // Sources filter bar chips
    dom.sourcesFilterBar?.addEventListener('click', (e) => {
        const chip = e.target.closest('.source-filter-chip');
        if (!chip) return;
        dom.sourcesFilterBar.querySelectorAll('.source-filter-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        state.rm.activeSourceFilter = chip.dataset.sourceFilter || 'all';
        renderRMSourcesPanel();
    });

    // Input Word & Character Gauges
    attachInputWordCounter(dom.queryInput, dom.queryCharCounter);
    attachInputWordCounter(dom.feedbackInput, dom.feedbackCharCounter);
    attachInputWordCounter(dom.rmPsInput, dom.rmPsCharCounter);
    attachInputWordCounter(dom.rmHitlFeedbackInput, dom.rmHitlCharCounter);

    // Apple-Style Example Research Topic Chips
    dom.rmExampleQueriesGrid?.addEventListener('click', (e) => {
        const chip = e.target.closest('.rm-query-chip');
        if (!chip) return;
        
        const query = chip.dataset.query;
        if (!query || !dom.rmPsInput) return;

        dom.rmExampleQueriesGrid.querySelectorAll('.rm-query-chip').forEach(c => c.classList.remove('selected'));
        chip.classList.add('selected');

        dom.rmPsInput.value = query;
        dom.rmPsInput.dispatchEvent(new Event('input', { bubbles: true }));

        dom.rmPsInput.focus();
        const card = dom.rmPsInput.closest('.rm-input-card');
        if (card) {
            card.classList.remove('input-pulse');
            // Trigger reflow to restart animation smoothly
            void card.offsetWidth;
            card.classList.add('input-pulse');
            setTimeout(() => card.classList.remove('input-pulse'), 600);
        }
    });

    // Paper inspector modal.
    dom.modalCloseBtn?.addEventListener('click', closePaperInspector);
    dom.paperDetailModal?.addEventListener('click', (e) => {
        if (e.target === dom.paperDetailModal) closePaperInspector();
    });

    // Pipeline tracker collapse toggle
    const collapseToggleBtn = document.getElementById('rm-tracker-collapse-toggle');
    const expandableContent = document.getElementById('rm-tracker-expandable-content');
    const toggleLbl = document.getElementById('tracker-toggle-lbl');
    const toggleIcon = document.getElementById('tracker-toggle-icon');

    collapseToggleBtn?.addEventListener('click', () => {
        if (!expandableContent) return;
        const isHidden = expandableContent.style.display === 'none';
        expandableContent.style.display = isHidden ? 'flex' : 'none';
        if (toggleLbl) toggleLbl.textContent = isHidden ? 'Collapse' : 'Expand';
        if (toggleIcon) {
            toggleIcon.setAttribute('data-lucide', isHidden ? 'chevron-up' : 'chevron-down');
            refreshIcons();
        }
    });

    // Global Keyboard Shortcuts (⌘/Ctrl + Enter to approve/run, Esc to close modals)
    setupGlobalKeyboardShortcuts();
}

function attachInputWordCounter(inputEl, counterEl) {
    if (!inputEl || !counterEl) return;
    function update() {
        const val = inputEl.value.trim();
        const words = val ? val.split(/\s+/).length : 0;
        const chars = inputEl.value.length;
        counterEl.textContent = `${words} ${words === 1 ? 'word' : 'words'} (${chars} chars)`;
        counterEl.classList.toggle('has-content', words > 0);
    }
    inputEl.addEventListener('input', update);
    update();
}

function setupGlobalKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Esc dismisses modals and popovers
        if (e.key === 'Escape') {
            if (dom.paperDetailModal && dom.paperDetailModal.style.display !== 'none') {
                closePaperInspector();
                return;
            }
            const exportMenu = document.getElementById('rm-export-menu');
            if (exportMenu && exportMenu.classList.contains('show')) {
                exportMenu.classList.remove('show');
                return;
            }
        }

        // Cmd/Ctrl + Enter executes main action
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            const gateModal = dom.setupGateModal || document.getElementById('setup-gate-modal');
            if (gateModal && gateModal.style.display !== 'none') {
                e.preventDefault();
                submitSetupGate();
                return;
            }

            if (state.mode === 'deepsearch') {
                const landing = dom.landingPanel || document.getElementById('landing-panel');
                const approval = dom.approvalPanel || document.getElementById('approval-panel');
                if (landing && landing.classList.contains('active')) {
                    e.preventDefault();
                    handlePlanResearch();
                } else if (approval && approval.classList.contains('active')) {
                    e.preventDefault();
                    const fb = dom.feedbackInput ? dom.feedbackInput.value.trim() : '';
                    if (fb) {
                        handleRevision();
                    } else {
                        submitPlanApproval();
                    }
                }
            } else if (state.mode === 'researchmode') {
                const rmInput = dom.rmInputPanel || document.getElementById('rm-input-panel');
                const rmHitl = dom.rmHitlPanel || document.getElementById('rm-hitl-panel');
                if (rmInput && rmInput.classList.contains('active')) {
                    e.preventDefault();
                    handleRMStart();
                } else if (rmHitl && rmHitl.style.display !== 'none') {
                    e.preventDefault();
                    const fb = dom.rmHitlFeedbackInput ? dom.rmHitlFeedbackInput.value.trim() : '';
                    if (fb) {
                        handleRMApprove(fb);
                    } else {
                        handleRMApprove('approve');
                    }
                }
            }
        }
    });
}

function closePaperInspector() {
    if (dom.paperDetailModal) dom.paperDetailModal.style.display = 'none';
}

// Admin token for the config API. The backend rejects config writes without it
// unless the deployment explicitly opted into open access for local use.
function getConfigToken() {
    return window.CONFIG_API_TOKEN || localStorage.getItem('config_api_token') || '';
}

function configHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    const token = getConfigToken();
    if (token) headers['X-Config-Token'] = token;
    return headers;
}

async function promptForConfigToken() {
    const token = window.prompt('This deployment requires an admin token to change configuration. Enter X-Config-Token:');
    if (token && token.trim()) {
        localStorage.setItem('config_api_token', token.trim());
        return true;
    }
    return false;
}

// Setup Gate Submit
async function submitSetupGate() {
    const payload = {
        LLM_BASE_URL: dom.gateLlmBaseUrl.value.trim(),
        LLM_API_KEY: dom.gateLlmApiKey.value.trim(),
        LLM_MODEL_PLANNER: dom.gateModelPlanner.value.trim(),
        LLM_MODEL_RESEARCHER: dom.gateModelResearcher.value.trim(),
        LLM_MODEL_AGGREGATOR: dom.gateModelAggregator.value.trim(),
        TAVILY_API_KEY: dom.gateTavilyKey.value.trim(),
        OPENALEX_EMAIL: dom.gateOpenalexEmail.value.trim()
    };

    dom.gateSaveStatus.textContent = 'Saving configuration to the backend...';
    try {
        let res = await fetch(`${API_BASE_URL}/config/setup`, {
            method: 'POST',
            headers: configHeaders(),
            body: JSON.stringify(payload)
        });
        if (res.status === 401 && await promptForConfigToken()) {
            res = await fetch(`${API_BASE_URL}/config/setup`, {
                method: 'POST',
                headers: configHeaders(),
                body: JSON.stringify(payload)
            });
        }
        const data = await res.json();
        if (res.status === 401 || res.status === 403) {
            localStorage.removeItem('config_api_token');
            dom.gateSaveStatus.textContent = data.detail || 'Configuration API is locked on this deployment.';
            return;
        }
        if (data.ok) {
            dom.gateSaveStatus.textContent = 'Configuration saved!';
            setTimeout(() => {
                dom.setupGateModal.style.display = 'none';
                showToast('Environment successfully configured!', 'success');
            }, 800);
        } else {
            dom.gateSaveStatus.textContent = data.detail || 'Failed to save.';
        }
    } catch (e) {
        dom.gateSaveStatus.textContent = 'Error connecting to backend.';
    }
}


// Theme handling
function initTheme() {
    const theme = localStorage.getItem('deepresearch_theme') || 'dark';
    if (theme === 'light') {
        document.documentElement.classList.add('light-mode');
    } else {
        document.documentElement.classList.remove('light-mode');
    }
    updateThemeIcon();
}

function toggleTheme() {
    document.documentElement.classList.toggle('light-mode');
    const isLight = document.documentElement.classList.contains('light-mode');
    localStorage.setItem('deepresearch_theme', isLight ? 'light' : 'dark');
    updateThemeIcon();
}

function updateThemeIcon() {
    const isLight = document.documentElement.classList.contains('light-mode');
    const btn = document.getElementById('theme-toggle-btn');
    if (btn) {
        btn.innerHTML = `<i data-lucide="${isLight ? 'sun' : 'moon'}" style="width: 18px; height: 18px;"></i>`;
        if (window.lucide) lucide.createIcons();
    }
}

function switchPanel(targetPanel) {
    if (!targetPanel) return;
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    targetPanel.classList.add('active');
    lastPanelByMode[state.mode] = targetPanel;
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Health Check. This ran exactly once at load, so a host that was still waking
// up (a cold-starting host can take ~30-60s to wake) pinned the offline banner on
// screen for the whole session even after the backend came up.
//
// The poll loop alone isn't enough: Chrome/Brave throttle setTimeout heavily in
// backgrounded tabs (exactly what happens when someone tabs away during a ~50s
// cold-start wait), so the next poll can be delayed minutes past when the
// backend actually came up. Two additional recovery paths cover that: a
// visibilitychange listener re-checks the instant the tab regains focus, and
// markBackendOnline() lets any real successful API response clear the banner
// immediately instead of waiting on the timer at all.
//
// A single failed /healthz poll isn't treated as "down" either: a small
// single worker occasionally queues or drops one request under load even while
// the app is otherwise working (a real API call can succeed seconds later), and
// that lone blip was enough to re-show the banner right after markBackendOnline()
// had just cleared it. Two consecutive failures are required before showing it;
// any success (poll or real traffic) clears the counter and hides it immediately.
// Finally, the fetch itself is bounded and the reschedule is unconditional.
// Without a timeout a cold-starting host holds the connection open instead of
// failing fast, so `await fetch(...)` never settles, the function never reaches
// its own setTimeout, and the poll loop dies permanently — leaving the banner
// stuck on screen with nothing left to ever clear it. That was the actual cause
// of the "it never goes away" reports, and it struck exactly during the cold
// start the banner exists to explain.
const HEALTH_TIMEOUT_MS = 8000;
let healthPollTimer = null;
let healthFailureStreak = 0;
let healthCheckInFlight = false;
let lastHealthFailureReason = '';

function markBackendOnline() {
    healthFailureStreak = 0;
    if (dom.backendOfflineBanner) {
        dom.backendOfflineBanner.style.display = 'none';
    }
}

// Probes one URL. Resolves {ok, reason} and never throws, so a caller can try a
// second endpoint without unwinding.
async function probeEndpoint(path) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
    try {
        const res = await fetch(`${API_BASE_URL}${path}`, {
            cache: 'no-store',
            signal: controller.signal
        });
        return { ok: res.ok, reason: res.ok ? '' : `HTTP ${res.status}` };
    } catch (e) {
        return {
            ok: false,
            reason: e.name === 'AbortError'
                ? `no response in ${HEALTH_TIMEOUT_MS / 1000}s`
                : `${e.name}: ${e.message}`
        };
    } finally {
        clearTimeout(timeoutId);
    }
}

async function checkBackendHealth() {
    // A focus-triggered check can land while the timer-driven poll is still
    // waiting; letting both run would double-count one outage toward the streak.
    if (healthCheckInFlight) return;
    healthCheckInFlight = true;

    let online = false;
    try {
        const primary = await probeEndpoint('/healthz');
        online = primary.ok;
        lastHealthFailureReason = primary.reason;

        // "Failed to fetch" means no HTTP response came back at all, which does
        // NOT prove the server is down — a content blocker or privacy extension
        // rejecting this one URL produces exactly the same error, and /healthz
        // looks enough like a telemetry beacon to attract those lists. Declaring
        // the backend unreachable on that alone pinned the banner permanently for
        // users whose app was working fine. Confirm against a second, ordinary
        // application endpoint before believing it; if that answers, we are online.
        if (!online && !primary.reason.startsWith('HTTP')) {
            const fallback = await probeEndpoint('/config/status');
            if (fallback.ok) {
                online = true;
                lastHealthFailureReason = '';
            }
        }
    } finally {
        healthCheckInFlight = false;
        // Poll fast while it is down (cold start), slowly once it is up. This
        // lives in finally so no failure path can ever leave the loop unscheduled.
        clearTimeout(healthPollTimer);
        healthPollTimer = setTimeout(checkBackendHealth, online ? 60000 : 5000);
    }

    healthFailureStreak = online ? 0 : healthFailureStreak + 1;

    if (dom.backendOfflineBanner) {
        if (online) {
            dom.backendOfflineBanner.style.display = 'none';
        } else if (healthFailureStreak >= 2) {
            dom.backendOfflineBanner.style.display = 'flex';
            const reasonEl = document.getElementById('backend-offline-reason');
            if (reasonEl && lastHealthFailureReason) {
                reasonEl.textContent = ` (${lastHealthFailureReason})`;
            }
        }
    }

    return online;
}

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        checkBackendHealth();
    }
});


/* ==========================================================================
   RESEARCH MODE PIPELINE LOGIC & SESSION PERSISTENCE
   ========================================================================== */

function saveRMSession() {
    if (!state.rm.threadId) return;
    try {
        state.rm.elapsedSeconds = rmTimer.elapsedSeconds;
        state.rm.timerPaused = rmTimer.isPaused;
        const { screenedPapers, ...persistableRmState } = state.rm;
        const sessionData = {
            threadId: state.rm.threadId,
            rmState: persistableRmState,
            lastSeq: state.rm.lastSeq || 0,
            timestamp: Date.now()
        };
        localStorage.setItem('rm_session', JSON.stringify(sessionData));
    } catch (e) {
        console.warn('Failed to save RM session:', e);
    }
}

function clearRMSession() {
    try {
        localStorage.removeItem('rm_session');
    } catch (e) {
        console.warn('Failed to clear RM session:', e);
    }
}

function inferCurrentRMCheckpoint(checkpointName) {
    if (!checkpointName) return 'checkpoint_1';
    const clean = String(checkpointName).replace(/_(approved|revising)$/, '');
    if (clean === 'checkpoint_1' || clean === 'checkpoint_2' || clean === 'checkpoint_3') {
        return clean;
    }
    return 'checkpoint_1';
}

async function restoreRMSessionOnLoad() {
    try {
        const raw = localStorage.getItem('rm_session');
        if (!raw) return;
        const session = JSON.parse(raw);
        if (!session || !session.threadId) return;

        // 1. Immediately restore state from localStorage so progress is NEVER wiped on page reload
        state.rm.threadId = session.threadId;
        setResearchBackdropActive(true);
        if (session.rmState) {
            Object.assign(state.rm, session.rmState);
        }
        if (session.lastSeq !== undefined) state.rm.lastSeq = session.lastSeq;

        // 2. Switch view to Research Mode workspace panel
        switchMode('researchmode');
        switchPanel(dom.rmWorkspacePanel);

        // 3. Render current progress from restored local state.
        const currentCp = inferCurrentRMCheckpoint(state.rm.hitlCheckpoint);
        state.rm.hitlCheckpoint = currentCp;
        const cpIdx = RM_STAGES.findIndex(s => s.id === currentCp);
        const completedStages = cpIdx > 0 ? RM_STAGES.slice(0, cpIdx).map(s => s.id) : [];
        updateRMPipelineTracker(currentCp, completedStages);
        if (state.rm.hitlCheckpointPending || (!state.rm.hitlApproved && state.rm.status !== 'completed')) {
            renderRMHitlPanel(currentCp);
        } else {
            if (dom.rmHitlPanel) dom.rmHitlPanel.style.display = 'none';
        }

        // Rehydrate elapsed timer from local state
        if (typeof state.rm.elapsedSeconds === 'number') {
            rmTimer.elapsedSeconds = state.rm.elapsedSeconds;
            if (state.rm.status === 'completed' || state.rm.hitlCheckpointPending || state.rm.timerPaused) {
                rmTimer.isPaused = true;
                rmTimer.updateDisplay();
            } else {
                rmTimer.start(state.rm.elapsedSeconds);
            }
        }

        // Rehydrate corpus stats immediately from local cache on reload
        if (state.rm.corpus_stats) {
            updateCorpusStats(state.rm.corpus_stats);
        } else if (state.rm.corpusStats) {
            updateCorpusStats(state.rm.corpusStats);
        } else if (state.rm.rawPapersCount || state.rm.screenedPapersCount) {
            updateCorpusStats({
                retrieved: state.rm.rawPapersCount || 0,
                after_dedup: state.rm.rawPapersCount || 0,
                screened: state.rm.screenedPapersCount || 0,
                included: state.rm.screenedPapersCount || 0,
                fulltext_fetched: state.rm.screenedPapersCount || 0
            });
        }

        if (state.rm.status === 'completed') {
            renderRMPaperFinal();
        } else if (typeof getPaperMarkdown === 'function' && getPaperMarkdown().trim().length > 50) {
            renderRMPaperLive(false);
        }

        showResumeBanner({ hitl_checkpoint: currentCp, is_completed: state.rm.status === 'completed' });
        updateNewRunVisibility();

        // 4. Ask the backend for ground truth and reconcile. values["hitl_checkpoint"]
        // and a non-empty state.next are both true for the entire rest of the run
        // after a checkpoint is passed, not just while paused there — so this now
        // relies on the backend's is_checkpoint, which is only true when the graph
        // is genuinely blocked inside interrupt() (see research_mode.py). Three
        // outcomes: finished, genuinely paused, or still executing.
        try {
            const res = await fetch(`${API_BASE_URL}/research-mode/result/${session.threadId}`);
            if (res.ok) {
                markBackendOnline();
                const data = await res.json();
                // This endpoint returns the raw graph state in snake_case. Copying it
                // straight onto state.rm (which is camelCase) meant a rehydrated
                // session rendered an empty paper and empty checkpoint panels.
                applyRMStatePayload(data.values || {});
                renderRMSourcesPanel();
                if (data.status) state.rm.status = data.status;

                // Sync corpus stats from backend result
                if (data.values && data.values.corpus_stats) {
                    updateCorpusStats(data.values.corpus_stats);
                } else if (state.rm.corpus_stats) {
                    updateCorpusStats(state.rm.corpus_stats);
                }

                if (data.report_rejected) {
                    // Finished, but the report guard rejected the paper.
                    processRMSEEvent({ event: 'error', message: data.message });
                } else if (data.is_completed) {
                    rmTimer.stop();
                    state.rm.hitlCheckpoint = 'title';
                    updateRMPipelineTracker('title', RM_STAGES.map(s => s.id));
                    if (dom.rmHitlPanel) dom.rmHitlPanel.style.display = 'none';
                    renderRMPaperFinal();
                } else if (data.is_checkpoint) {
                    rmTimer.pause();
                    const resolvedCp = inferCurrentRMCheckpoint(data.hitl_checkpoint);
                    state.rm.hitlCheckpoint = resolvedCp;
                    state.rm.hitlCheckpointPending = true;
                    state.rm.hitlApproved = false;
                    const idx = RM_STAGES.findIndex(s => s.id === resolvedCp);
                    const doneStages = idx > 0 ? RM_STAGES.slice(0, idx).map(s => s.id) : [];
                    updateRMPipelineTracker(resolvedCp, doneStages);
                    renderRMHitlPanel(resolvedCp);
                    if (typeof getPaperMarkdown === 'function' && getPaperMarkdown().trim().length > 50) {
                        renderRMPaperLive(false);
                    }
                } else {
                    // Not finished, not paused: a node is actively running on the
                    // backend right now. Show live progress and reopen the event
                    // stream so it keeps updating instead of sitting frozen.
                    rmTimer.resume();
                    if (dom.rmHitlPanel) dom.rmHitlPanel.style.display = 'none';
                    state.rm.hitlApproved = true;
                    state.rm.hitlCheckpointPending = false;
                    if (typeof getPaperMarkdown === 'function' && getPaperMarkdown().trim().length > 50) {
                        renderRMPaperLive(true);
                    }
                    reconnectRMStream();
                }
                saveRMSession();
            }
        } catch (syncErr) {
            console.warn('Backend session sync skipped (using local cache):', syncErr);
            if (state.rm.hitlCheckpointPending || (!state.rm.hitlApproved && state.rm.status !== 'completed')) {
                renderRMHitlPanel(currentCp);
            }
        }

    } catch (e) {
        console.warn('Error restoring session on load:', e);
    }
}

function showResumeBanner(data) {
    let banner = document.getElementById('rm-resume-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'rm-resume-banner';
        banner.className = 'original-query-banner';
        banner.style.display = 'flex';
        banner.style.justifyContent = 'space-between';
        banner.style.alignItems = 'center';
        banner.style.marginBottom = '1.25rem';

        const workspace = document.getElementById('rm-workspace-panel');
        if (workspace) {
            workspace.prepend(banner);
        }
    }

    const shortId = state.rm.threadId ? state.rm.threadId.slice(0, 8) : '';
    const statusText = data.is_completed
        ? 'Completed Academic Paper'
        : (data.hitl_checkpoint ? `Checkpoint (${data.hitl_checkpoint})` : 'In Progress');

    banner.innerHTML = `
        <div style="display: flex; align-items: center; gap: 0.6rem;">
            <i data-lucide="rotate-ccw" style="width: 18px; height: 18px; color: var(--academic-blue);"></i>
            <span><strong>Session Resumed:</strong> Rehydrated session <code>${shortId}</code> — Status: <em>${escapeHtml(statusText)}</em></span>
        </div>
        <button id="rm-banner-reset-btn" class="btn-secondary" style="padding: 0.3rem 0.75rem; font-size: 0.8rem; display: flex; align-items: center; gap: 0.35rem;">
            <i data-lucide="plus" style="width: 14px; height: 14px;"></i>
            <span>New Research</span>
        </button>
    `;

    if (window.lucide) lucide.createIcons();

    document.getElementById('rm-banner-reset-btn')?.addEventListener('click', resetResearchModeForm);
}

function resetResearchModeForm() {
    clearRMSession();
    setResearchBackdropActive(false);
    state.rm.threadId = null;
    state.rm.status = 'idle';
    state.rm.hitlCheckpoint = null;
    state.rm.problemStatement = '';
    state.rm.researchObjectives = [];
    state.rm.researchQuestions = [];
    state.rm.keywords = [];
    state.rm.searchProtocol = null;
    state.rm.rawPapersCount = 0;
    state.rm.screenedPapersCount = 0;
    state.rm.evidenceRecordsCount = 0;
    state.rm.evidenceRecords = [];
    state.rm.claims = [];
    state.rm.prismaTracker = null;
    state.rm.taxonomy = null;
    state.rm.validationReport = null;
    state.rm.literatureReview = '';
    state.rm.researchGap = '';
    state.rm.conceptualFramework = '';
    state.rm.hypotheses = [];
    state.rm.researchDesign = '';
    state.rm.dataCollectionPlan = '';
    state.rm.dataAnalysisPlan = '';
    state.rm.results = '';
    state.rm.discussion = '';
    state.rm.implications = '';
    state.rm.limitations = '';
    state.rm.conclusion = '';
    state.rm.futureScope = [];
    state.rm.references = [];
    state.rm.appendices = '';
    state.rm.introduction = '';
    state.rm.abstract = '';
    state.rm.title = '';
    state.rm.lastSeq = 0;
    state.rm.completedStages = [];
    state.rm.screenedPapers = [];
    state.rm.hitlApproved = false;
    state.rm.hitlCheckpointPending = false;
    state.rm.corpus_stats = null;

    rmTimer.reset();
    hideRMCheckpointTransitionLoader();
    if (dom.rmCorpusStatsBar) dom.rmCorpusStatsBar.style.display = 'none';
    if (dom.rmHitlPanel) dom.rmHitlPanel.style.display = 'none';
    if (dom.rmCopyPaperBtn) dom.rmCopyPaperBtn.style.display = 'none';
    if (dom.rmExportDropdown) dom.rmExportDropdown.style.display = 'none';
    renderRMSourcesPanel();

    if (dom.rmPsInput) { dom.rmPsInput.value = ''; rmPlaceholderCycle?.start(); }
    if (dom.rmObjsInput) dom.rmObjsInput.value = '';
    if (dom.rmRqsInput) dom.rmRqsInput.value = '';

    const banner = document.getElementById('rm-resume-banner');
    if (banner) banner.remove();

    if (dom.rmPaperTitle) dom.rmPaperTitle.textContent = 'Academic Paper Workspace';
    if (dom.rmPaperOutput) dom.rmPaperOutput.innerHTML = `
        <div class="paper-placeholder-state" id="rm-paper-placeholder">
            <div class="paper-idle-icon" style="margin-bottom: 0.5rem;">
                <i data-lucide="file-clock" style="width: 36px; height: 36px; color: var(--academic-blue); opacity: 0.7;"></i>
            </div>
            <p style="color: var(--text-secondary); font-weight: 600; font-size: 0.95rem;">
                Ready to Launch
            </p>
            <p style="margin-top: 0.35rem; color: var(--text-muted); font-size: 0.82rem; max-width: 440px; line-height: 1.5; text-align: center;">
                Fill in your research topic and click <strong style="color: var(--text-secondary);">Launch Autonomous Academic Pipeline</strong> to begin.
            </p>
        </div>
    `;
    if (window.lucide) lucide.createIcons();

    renderRMPipelineTracker();
    switchPanel(dom.rmInputPanel);
    showToast('Research session reset.', 'info');
}

function renderRMPipelineTracker() {
    updateRMPipelineTracker(state.rm.activeStage || 'scope_definition');
}

function updateCorpusStats(stats) {
    if (!stats) return;
    state.rm.corpus_stats = stats;

    const retrievedEl = document.getElementById('stat-retrieved');
    const dedupEl = document.getElementById('stat-dedup');
    const screenedEl = document.getElementById('stat-screened');
    const includedEl = document.getElementById('stat-included');
    const fulltextEl = document.getElementById('stat-fulltext');

    if (retrievedEl) retrievedEl.textContent = stats.retrieved != null ? stats.retrieved : 0;
    if (dedupEl) dedupEl.textContent = stats.after_dedup != null ? stats.after_dedup : (stats.deduplicated != null ? stats.deduplicated : 0);
    if (screenedEl) screenedEl.textContent = stats.screened != null ? stats.screened : 0;
    if (includedEl) includedEl.textContent = stats.included != null ? stats.included : 0;
    if (fulltextEl) fulltextEl.textContent = stats.fulltext_fetched != null ? stats.fulltext_fetched : (stats.fulltext != null ? stats.fulltext : 0);

    const statsBar = document.getElementById('rm-corpus-stats-bar');
    if (statsBar) statsBar.style.display = 'grid';
}

function updateRMPipelineTracker(activeStageId, completedStages) {
    const hidden = RM_HIDDEN_STAGES[activeStageId];
    const anchoredStageId = hidden ? hidden.anchor : activeStageId;

    // Track completed stages
    const done = new Set(state.rm.completedStages || []);
    if (Array.isArray(completedStages)) {
        completedStages.forEach(id => done.add(id));
    }
    state.rm.completedStages = Array.from(done);

    // Determine active phase index (0 to 3)
    let activePhaseIdx = 0;
    RM_PHASES.forEach((phase, idx) => {
        if (phase.stages.includes(anchoredStageId)) {
            activePhaseIdx = idx;
        }
    });

    const isFinished = activeStageId === 'title' || state.rm.status === 'completed';

    // Update active task label
    const current = RM_STAGES.find(s => s.id === anchoredStageId);
    const activeLabelEl = document.getElementById('rm-active-task-label');
    if (activeLabelEl) {
        if (isFinished) {
            activeLabelEl.textContent = 'Academic Paper Synthesis Complete';
        } else if (!hidden && current && current.hitl) {
            activeLabelEl.textContent = `Review Checkpoint: ${current.name} (Waiting for your review)`;
        } else {
            const label = hidden ? hidden.label : (current ? current.name : 'Running');
            activeLabelEl.textContent = `Active Task: ${label}`;
        }
    }

    if (dom.rmPipelineStatusTag) {
        if (isFinished) {
            dom.rmPipelineStatusTag.textContent = 'Synthesis Complete';
            dom.rmPipelineStatusTag.className = 'stepper-status-pill completed';
        } else if (!hidden && current && current.hitl) {
            dom.rmPipelineStatusTag.textContent = `Waiting for your review — ${current.name}`;
            dom.rmPipelineStatusTag.className = 'stepper-status-pill review';
        } else {
            const label = hidden ? hidden.label : (current ? current.name : null);
            dom.rmPipelineStatusTag.textContent = label ? `Active: ${label}` : 'Pipeline Running';
            dom.rmPipelineStatusTag.className = 'stepper-status-pill';
        }
    }

    // Render the 4-Phase Connected Real-Time Pipeline Matrix
    const matrixContainer = document.getElementById('rm-stages-matrix');
    if (matrixContainer) {
        let matrixHtml = '';
        RM_PHASES.forEach((phase, pIdx) => {
            const isPhaseActive = pIdx === activePhaseIdx;
            const isPhaseDone = pIdx < activePhaseIdx || isFinished;
            matrixHtml += `
                <div class="pipeline-phase-group ${isPhaseActive ? 'phase-active' : ''} ${isPhaseDone ? 'phase-done' : ''}">
                    <div class="phase-group-header">
                        <span class="phase-num-pill">${phase.badge || `Phase ${pIdx + 1}`}</span>
                        <span class="phase-group-title">${escapeHtml(phase.name)}</span>
                    </div>
                    <div class="phase-stages-chips">
            `;

            phase.stages.forEach(stageId => {
                const stageObj = RM_STAGES.find(s => s.id === stageId);
                if (!stageObj) return;

                const isDone = isFinished || done.has(stageId);
                const isActive = !isFinished && (stageId === anchoredStageId);
                const isHitl = !!stageObj.hitl;

                let chipClass = 'pending';
                let iconHtml = '<i data-lucide="circle" style="width: 8px; height: 8px; opacity: 0.5;"></i>';

                if (isDone) {
                    chipClass = 'completed';
                    iconHtml = '<i data-lucide="check" style="width: 9px; height: 9px; color: var(--emerald-verified); stroke-width: 3;"></i>';
                } else if (isActive) {
                    chipClass = isHitl ? 'review active' : 'active';
                    iconHtml = isHitl 
                        ? '<i data-lucide="shield-alert" style="width: 10px; height: 10px; color: #f59e0b;"></i>' 
                        : '<span class="micro-spin-dot"></span>';
                } else if (isHitl) {
                    chipClass = 'hitl-pending';
                    iconHtml = '<i data-lucide="shield" style="width: 9px; height: 9px; color: rgba(251, 191, 36, 0.7);"></i>';
                }

                matrixHtml += `
                    <div class="stage-flow-chip ${chipClass} ${isHitl ? 'is-hitl-stage' : ''}" title="${escapeHtml(stageObj.name)}">
                        <span class="stage-chip-icon">${iconHtml}</span>
                        <span class="stage-chip-name">${escapeHtml(stageObj.name)}</span>
                        ${isActive ? '<span class="stage-chip-live-dot"></span>' : ''}
                    </div>
                `;
            });

            matrixHtml += `
                    </div>
                </div>
            `;
        });
        matrixContainer.innerHTML = matrixHtml;
    }

    refreshIcons();
}

// Human-readable name for a raw graph node id, for the event log.
function rmStageLabel(nodeId) {
    if (!nodeId) return 'unknown';
    const hidden = RM_HIDDEN_STAGES[nodeId];
    if (hidden) return hidden.label;
    const stage = RM_STAGES.find(s => s.id === nodeId);
    return stage ? stage.name : nodeId;
}

// Some nodes stream for minutes with no node_update in between. Without this the
// tracker sat on a static label and the run looked hung.
let rmTokenActivityTimer = null;
function noteRMTokenActivity(nodeId) {
    if (!dom.rmPipelineStatusTag) return;
    dom.rmPipelineStatusTag.textContent = `Writing: ${rmStageLabel(nodeId)}…`;
    dom.rmPipelineStatusTag.classList.add('streaming');
    clearTimeout(rmTokenActivityTimer);
    rmTokenActivityTimer = setTimeout(() => {
        dom.rmPipelineStatusTag?.classList.remove('streaming');
    }, 2500);
}

async function handleRMStart() {
    const ps = dom.rmPsInput.value.trim();
    if (!ps) {
        showToast('Please enter a core Problem Statement to start Research Mode.', 'warning');
        return;
    }

    const objs = dom.rmObjsInput.value.split('\n').map(s => s.trim()).filter(Boolean);
    const rqs = dom.rmRqsInput.value.split('\n').map(s => s.trim()).filter(Boolean);

    const plannerModel = dom.rmModelPlanner?.value.trim();
    const researcherModel = dom.rmModelResearcher?.value.trim();
    const aggregatorModel = dom.rmModelAggregator?.value.trim();
    const models = {};
    if (plannerModel) models.planner = plannerModel;
    if (researcherModel) models.researcher = researcherModel;
    if (aggregatorModel) models.aggregator = aggregatorModel;

    dom.rmStartBtn.disabled = true;
    currentPaperTotal = Math.max(currentPaperTotal + 1, 34);
    animatePaperCounter(currentPaperTotal, 400);
    dom.rmStartBtn.innerHTML = `
        <div class="btn-loader-wrap">
            <span class="apple-spin-ring"></span>
            <span class="btn-loader-text">Defining Research Scope<span class="loading-dots"><span>.</span><span>.</span><span>.</span></span></span>
        </div>
    `;

    // Show an Apple radar-style scope loader in the workspace body
    const scopeLoader = document.createElement('div');
    scopeLoader.className = 'rm-scope-loader card';
    scopeLoader.id = 'rm-scope-loader';
    scopeLoader.innerHTML = `
        <div class="rm-scope-loader-inner">
            <div class="apple-radar-spinner">
                <div class="radar-core"></div>
                <div class="radar-wave radar-wave-1"></div>
                <div class="radar-wave radar-wave-2"></div>
                <div class="radar-wave radar-wave-3"></div>
            </div>
            <div class="scope-text-wrap">
                <div class="scope-title-row">
                    <h4 class="scope-title">Initializing Research Scope…</h4>
                    <span class="scope-live-badge"><span class="badge-live-dot"></span> Active</span>
                </div>
                <p class="scope-sub">Drafting problem statement, formulating objectives &amp; questions, extracting academic keywords</p>
                <div class="scope-progress-bar-track">
                    <div class="scope-progress-bar-fill"></div>
                </div>
            </div>
        </div>
    `;

    try {
        const res = await fetch(`${API_BASE_URL}/research-mode/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ problem_statement: ps, research_objectives: objs, research_questions: rqs, models })
        });

        const contentType = res.headers.get('content-type') || '';
        if (!res.ok || !contentType.includes('application/json')) {
            throw new Error(`Server returned ${res.status} (${res.statusText || 'Non-JSON response'}). Make sure the backend server is running on ${API_BASE_URL}.`);
        }
        markBackendOnline();

        const data = await res.json();
        if (data.error || data.status === 'error') {
            showToast(data.error || 'Failed to start Research Mode.', 'error');
            scopeLoader.remove();
            dom.rmStartBtn.disabled = false;
            dom.rmStartBtn.innerHTML = '<i data-lucide="atom" style="width: 18px; height: 18px;"></i><span>Launch Autonomous Academic Pipeline</span>';
            if (window.lucide) lucide.createIcons();
            return;
        }

        state.rm.threadId = data.thread_id;
        setResearchBackdropActive(true);
        if (typeof data.papers_total === 'number') {
            animatePaperCounter(data.papers_total, 400);
        }
        state.rm.problemStatement = data.problem_statement;
        state.rm.researchObjectives = data.research_objectives || [];
        state.rm.researchQuestions = data.research_questions || [];
        state.rm.keywords = data.keywords || [];
        state.rm.hitlCheckpoint = data.hitl_checkpoint;
        rmTimer.reset();
        rmTimer.pause();
        saveRMSession();

        state.rm.completedStages = [];
        switchPanel(dom.rmWorkspacePanel);
        const wsBody = dom.rmWorkspacePanel?.querySelector('.rm-workspace-body');
        wsBody?.prepend(scopeLoader);
        updateRMPipelineTracker('checkpoint_1', ['scope_definition', 'keyword_extractor']);
        renderRMHitlPanel('checkpoint_1');
        updateNewRunVisibility();

    } catch (e) {
        showToast('Error connecting to Research Mode service: ' + e.message, 'error');
        scopeLoader.remove();
    } finally {
        dom.rmStartBtn.disabled = false;
        dom.rmStartBtn.innerHTML = '<i data-lucide="atom" style="width: 18px; height: 18px;"></i><span>Launch Autonomous Academic Pipeline</span>';
        if (window.lucide) lucide.createIcons();
    }
}
const RM_CHECKPOINT_TRANSITIONS = {
    checkpoint_1: {
        phaseBadge: 'Phase 2 of 5: Retrieval & Screening',
        title: 'Retrieving Multi-Source Literature & Citation Graph…',
        subtitle: 'Querying OpenAlex, Semantic Scholar, ArXiv, Crossref, and PubMed concurrently to build the comprehensive academic candidate pool.',
        subtasks: [
            { icon: 'globe', label: 'Executing concurrent multi-source API queries' },
            { icon: 'git-merge', label: 'Resolving OpenCitations forward/backward references graph' },
            { icon: 'check-square', label: 'Deterministic deduplication & metadata normalization' },
            { icon: 'filter', label: 'Title & abstract eligibility screening' }
        ]
    },
    checkpoint_2: {
        phaseBadge: 'Phase 4 of 5: Theoretical Framing',
        title: 'Structuring Evidence Taxonomy & Research Gaps…',
        subtitle: 'Synthesizing extracted quantitative metrics and qualitative findings into hierarchical thematic clusters and identifying research gaps.',
        subtasks: [
            { icon: 'folder-tree', label: 'Clustering evidence by taxonomy and methodology' },
            { icon: 'search', label: 'Evaluating research gaps and theoretical conflicts' },
            { icon: 'layout', label: 'Constructing conceptual framework architecture' }
        ]
    },
    checkpoint_3: {
        phaseBadge: 'Phase 5 of 5: Methodology & Full Paper Synthesis',
        title: 'Formulating Hypotheses & Synthesizing Academic Paper…',
        subtitle: 'Drafting empirical methodology, results, in-depth discussion, deterministic citation validation, and complete paper sections.',
        subtasks: [
            { icon: 'sparkles', label: 'Formulating testable directional hypotheses' },
            { icon: 'layout', label: 'Specifying proposed research design and analysis plans' },
            { icon: 'book-open', label: 'Synthesizing evidence-grounded literature review & results' },
            { icon: 'shield-check', label: 'Running citation resolution and number checks' },
            { icon: 'file-check', label: 'Rendering APA references, PRISMA figures & appendices' }
        ]
    }
};

function showRMCheckpointTransitionLoader(currentCp, isRevision = false) {
    hideRMCheckpointTransitionLoader();

    const transitionMeta = RM_CHECKPOINT_TRANSITIONS[currentCp] || {
        phaseBadge: 'Pipeline Progressing',
        title: 'Autonomous Academic Pipeline Resuming…',
        subtitle: 'Executing next phase agents, processing data and synthesizing findings.',
        subtasks: [
            { icon: 'cpu', label: 'Autonomous agents actively processing workflow' },
            { icon: 'sparkles', label: 'Synthesizing evidence and academic artifacts' }
        ]
    };

    const loaderCard = document.createElement('div');
    loaderCard.className = 'rm-checkpoint-loader card';
    loaderCard.id = 'rm-checkpoint-loader';

    const isRev = isRevision;
    const badgeText = isRev ? `Revising: ${transitionMeta.phaseBadge}` : transitionMeta.phaseBadge;
    const titleText = isRev ? `Applying Checkpoint Revisions…` : transitionMeta.title;
    const subtitleText = isRev ? `Refining scope and updating parameters based on your feedback before resuming pipeline execution.` : transitionMeta.subtitle;

    loaderCard.innerHTML = `
        <div class="rm-loader-header">
            <div class="rm-loader-badge-group">
                <span class="rm-loader-badge">
                    <span class="rm-loader-badge-dot"></span>
                    ${escapeHtml(badgeText)}
                </span>
                <span class="rm-loader-live-tag">
                    <i data-lucide="activity" style="width: 12px; height: 12px;"></i>
                    Active Execution
                </span>
            </div>
        </div>

        <div class="rm-loader-body">
            <div class="rm-loader-hero">
                <div class="rm-loader-spinner-wrapper">
                    <div class="orbital-ring"></div>
                    <div class="rm-loader-core-icon">
                        <i data-lucide="${isRev ? 'refresh-cw' : 'atom'}" style="width: 22px; height: 22px; color: var(--academic-blue);"></i>
                    </div>
                </div>
                <div class="rm-loader-hero-text">
                    <h3 class="rm-loader-title">${escapeHtml(titleText)}</h3>
                    <p class="rm-loader-subtitle">${escapeHtml(subtitleText)}</p>
                </div>
            </div>

            <div class="rm-loader-subtasks-container">
                <span class="rm-loader-subtasks-heading">Phase Pipeline Tasks</span>
                <div class="rm-loader-subtasks-list">
                    ${transitionMeta.subtasks.map((task, idx) => `
                        <div class="rm-loader-subtask-item ${idx === 0 ? 'in-progress' : 'pending'}">
                            <span class="rm-loader-subtask-status">
                                ${idx === 0 ? '<span class="pulse-dot"></span>' : '<span class="wait-dot"></span>'}
                            </span>
                            <span class="rm-loader-subtask-icon">
                                <i data-lucide="${escapeHtml(task.icon)}" style="width: 14px; height: 14px;"></i>
                            </span>
                            <span class="rm-loader-subtask-label">${escapeHtml(task.label)}</span>
                        </div>
                    `).join('')}
                </div>
            </div>

            <div class="rm-loader-shimmer-preview">
                <div class="skeleton-line" style="width: 90%;"></div>
                <div class="skeleton-line" style="width: 75%;"></div>
                <div class="skeleton-line" style="width: 60%;"></div>
            </div>
        </div>

        <div class="rm-loader-footer-bar">
            <div class="rm-loader-footer-inner">
                <span class="ai-wave-container">
                    <span class="ai-wave-bar"></span>
                    <span class="ai-wave-bar"></span>
                    <span class="ai-wave-bar"></span>
                    <span class="ai-wave-bar"></span>
                </span>
                <span class="rm-loader-footer-note">Streaming live agent deliberations &amp; synthesizing academic sections</span>
            </div>
        </div>
    `;

    try {
        if (dom.rmHitlPanel && dom.rmHitlPanel.parentNode) {
            dom.rmHitlPanel.parentNode.insertBefore(loaderCard, dom.rmHitlPanel);
        } else {
            const targetContainer = dom.rmWorkspacePanel?.querySelector('.rm-main-column')
                || dom.rmWorkspacePanel?.querySelector('.rm-workspace-body')
                || dom.rmWorkspacePanel;
            if (targetContainer) {
                targetContainer.prepend(loaderCard);
            }
        }
        refreshIcons();
    } catch (err) {
        console.warn('Failed to mount RM transition loader card:', err);
    }
}

function hideRMCheckpointTransitionLoader() {
    document.getElementById('rm-checkpoint-loader')?.remove();
    document.getElementById('rm-scope-loader')?.remove();
}

function updateRMTransitionLoaderNode(nodeId) {
    const loader = document.getElementById('rm-checkpoint-loader');
    if (!loader) return;
    const label = rmStageLabel(nodeId);
    const noteEl = loader.querySelector('.rm-loader-footer-note');
    if (noteEl) {
        noteEl.textContent = `Active Stage: ${label} — synthesizing findings…`;
    }
}

function renderRMHitlPanel(checkpoint) {
    hideRMCheckpointTransitionLoader();
    dom.rmHitlPanel.style.display = 'block';
    dom.rmHitlBody.innerHTML = '';
    dom.rmHitlFeedbackInput.value = '';
    dom.rmHitlFeedbackInput.placeholder = 'Specify any edits or revisions for this phase...';

    if (checkpoint === 'checkpoint_1') {
        dom.rmHitlTitle.textContent = 'Gate 1: Planning & Protocol Review';
        dom.rmHitlBadge.textContent = 'Gate 1 of 3';
        dom.rmHitlFeedbackInput.placeholder = 'e.g. Include population constraints, add boolean keyword for transformer models...';

        const objectives = state.rm.researchObjectives || [];
        const questions = state.rm.researchQuestions || [];
        const protocol = state.rm.searchProtocol || {};

        dom.rmHitlBody.innerHTML = `
            <div class="form-group">
                <label class="form-label">Problem Statement</label>
                <div class="problem-statement-text">${renderMarkdownSafe(state.rm.problemStatement)}</div>
            </div>
            ${protocol && (protocol.population || protocol.inclusion_criteria) ? `
            <div class="form-group">
                <label class="form-label">Systematic Search Protocol (PICOC)</label>
                <div class="protocol-card" style="background: var(--surface-bg-subtle, rgba(255,255,255,0.03)); border: 1px solid var(--border-subtle); border-radius: 8px; padding: 0.85rem; font-size: 0.85rem;">
                    <p style="margin-bottom: 0.35rem;"><strong>Domain / Population:</strong> ${escapeHtml(protocol.population || 'General')}</p>
                    <p style="margin-bottom: 0.35rem;"><strong>Inclusion Criteria:</strong> ${escapeHtml((protocol.inclusion_criteria || []).join('; ') || 'Standard peer-reviewed literature')}</p>
                    <p style="margin-bottom: 0;"><strong>Exclusion Criteria:</strong> ${escapeHtml((protocol.exclusion_criteria || []).join('; ') || 'Non-English or non-empirical preprints')}</p>
                </div>
            </div>
            ` : ''}
            <div class="form-group">
                <label class="form-label">Research Objectives <span class="label-tag">auto-defined</span></label>
                <div class="subtasks-list">
                    ${objectives.map((o, i) => `
                        <div class="subtask-item">
                            <span class="subtask-number">O${i + 1}</span>
                            <span class="subtask-content">${renderMarkdownSafe(o)}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">Research Questions <span class="label-tag">auto-defined</span></label>
                <div class="subtasks-list">
                    ${questions.map((q, i) => `
                        <div class="subtask-item">
                            <span class="subtask-number">RQ${i + 1}</span>
                            <span class="subtask-content">${renderMarkdownSafe(q)}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">Extracted Academic Keywords (6-10)</label>
                <div class="chips-container">
                    ${(state.rm.keywords || []).map(kw => `<span class="chip active">${escapeHtml(kw)}</span>`).join('')}
                </div>
            </div>
        `;
    } else if (checkpoint === 'checkpoint_2') {
        dom.rmHitlTitle.textContent = 'Gate 2: Evidence & Corpus Review';
        dom.rmHitlBadge.textContent = 'Gate 2 of 3';
        dom.rmHitlFeedbackInput.placeholder = 'e.g. Filter out non-benchmark papers, prioritize higher sample studies...';

        const topPapers = getScreenedPapers().slice(0, 8);
        const evidenceRecords = (state.rm.evidenceRecords || []).slice(0, 6);

        const evidenceCards = evidenceRecords.map(e => {
            const sectionLabel = e.section || e.source_section || 'unknown';
            const pageLabel = e.page != null ? `p. ${e.page}` : 'page n/a';
            const confidencePct = e.confidence != null ? Math.round(e.confidence * 100) : null;
            // 'resolved' = quote located and chain complete; 'verified' is the legacy value from runs saved before the rename.
            const resolved = ['resolved', 'verified'].includes(e.verification_status);
            const confColor = resolved ? 'rgba(34,197,94,0.18)' : 'rgba(234,179,8,0.18)';
            const confTextColor = resolved ? '#16a34a' : '#ca8a04';
            let sourceHtml = '';
            if (e.doi) {
                sourceHtml = `<a href="https://doi.org/${encodeURIComponent(e.doi)}" target="_blank" rel="noopener noreferrer" style="font-size: 0.72rem; color: var(--academic-blue); text-decoration: none;">Source &rarr;</a>`;
            } else if (e.source_url) {
                const safeSource = safeHttpUrl(e.source_url);
                if (safeSource) {
                    sourceHtml = `<a href="${escapeHtml(safeSource)}" target="_blank" rel="noopener noreferrer" style="font-size: 0.72rem; color: var(--academic-blue); text-decoration: none;">Source &rarr;</a>`;
                } else {
                    sourceHtml = `<span style="font-size: 0.72rem; color: var(--text-secondary);">${escapeHtml(e.source_url)}</span>`;
                }
            }
            return `
            <div class="evidence-extract-card" style="background: var(--surface-bg-subtle, rgba(255,255,255,0.02)); border: 1px solid var(--border-subtle); border-radius: 6px; padding: 0.65rem; margin-bottom: 0.5rem; font-size: 0.82rem;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.25rem;">
                    <span style="font-family: monospace; font-size: 0.75rem; color: var(--academic-blue);">${escapeHtml(e.evidence_id || '')}</span>
                    <span style="display: flex; gap: 0.35rem; align-items: center;">
                        ${confidencePct != null ? `<span title="${resolved ? 'Verbatim quote anchored to source' : 'Paraphrase or incomplete traceability chain'}" style="font-size: 0.72rem; padding: 0.1rem 0.4rem; border-radius: 4px; background: ${confColor}; color: ${confTextColor}; font-weight: 600;">${resolved ? '&#10003;' : '&#9888;'} ${confidencePct}%</span>` : ''}
                        <span style="font-size: 0.72rem; padding: 0.1rem 0.4rem; border-radius: 4px; background: rgba(59,130,246,0.15); color: var(--academic-blue); font-weight: 600;">${escapeHtml(sectionLabel)}</span>
                        <span style="font-size: 0.72rem; padding: 0.1rem 0.4rem; border-radius: 4px; background: rgba(148,163,184,0.15); color: var(--text-secondary);">${escapeHtml(pageLabel)}</span>
                    </span>
                </div>
                <p style="margin: 0.25rem 0; font-weight: 500;">${escapeHtml(e.claim_summary || '')}</p>
                ${e.exact_quote ? `<p style="margin: 0.25rem 0; font-size: 0.75rem; color: var(--text-secondary); font-style: italic;">&ldquo;${escapeHtml(String(e.exact_quote).slice(0, 180))}${String(e.exact_quote).length > 180 ? '&hellip;' : ''}&rdquo;</p>` : ''}
                ${sourceHtml ? `<div style="margin-top: 0.25rem;">${sourceHtml}</div>` : ''}
                ${e.reported_value != null ? `<div style="font-size: 0.75rem; color: var(--text-secondary);">Metric: <code>${escapeHtml(e.metric_name || '')} = ${escapeHtml(String(e.reported_value))}</code></div>` : ''}
            </div>
        `;
        }).join('');

        dom.rmHitlBody.innerHTML = `
            <div class="form-group">
                <label class="form-label">Screened Literature Corpus <span class="label-tag">${state.rm.screenedPapersCount || topPapers.length} studies included</span></label>
                <div class="evidence-list">
                    ${topPapers.map((p, i) => `
                        <div class="evidence-row" data-paper-index="${i}">
                            ${sourceBadgeHtml(p.retrieval_source || p.source)}
                            <span class="evidence-title">${escapeHtml(p.title || 'Untitled')}</span>
                            <span class="evidence-year">${escapeHtml(String(p.year || ''))}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
            ${evidenceRecords.length ? `
            <div class="form-group">
                <label class="form-label">Extracted Structured Evidence Records <span class="label-tag">${state.rm.evidenceRecordsCount || evidenceRecords.length} records</span></label>
                <div>${evidenceCards}</div>
            </div>
            ` : ''}
        `;

        dom.rmHitlBody.querySelectorAll('.evidence-row').forEach((row) => {
            row.addEventListener('click', () => {
                const idx = parseInt(row.dataset.paperIndex, 10);
                openPaperInspector(topPapers[idx]);
            });
        });
    } else if (checkpoint === 'checkpoint_3') {
        dom.rmHitlTitle.textContent = 'Gate 3: Hypotheses & Theoretical Framework Review';
        dom.rmHitlBadge.textContent = 'Gate 3 of 3';
        dom.rmHitlFeedbackInput.placeholder = 'e.g. Refine H2 to specify causal mechanism, adjust conceptual framework boundary...';

        dom.rmHitlBody.innerHTML = `
            <div class="form-group">
                <label class="form-label">Formulated Research Hypotheses</label>
                <div class="subtasks-list">
                    ${(state.rm.hypotheses || []).map((h, i) => `
                        <div class="subtask-item">
                            <span class="subtask-number">H${i+1}</span>
                            <span class="subtask-content">${renderMarkdownSafe(h)}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
            ${state.rm.conceptualFramework ? `
            <div class="form-group">
                <label class="form-label">Proposed Conceptual Framework</label>
                ${renderTruncatable(state.rm.conceptualFramework)}
            </div>
            ` : ''}
            ${state.rm.researchGap ? `
            <div class="form-group">
                <label class="form-label">Identified Research Gaps</label>
                ${renderTruncatable(state.rm.researchGap)}
            </div>
            ` : ''}
        `;
    } else {
        dom.rmHitlTitle.textContent = 'Quality Gate Review Required';
        dom.rmHitlBadge.textContent = 'Review';
        dom.rmHitlBody.innerHTML = `
            <p class="rm-hint">The pipeline is paused at <code>${escapeHtml(checkpoint)}</code>.
            Approve to continue, or specify feedback adjustments.</p>
        `;
    }

    refreshIcons();
}

async function handleRMApprove(feedback) {
    if (!state.rm.threadId) return;

    state.rm.hitlApproved = true;
    state.rm.hitlCheckpointPending = false;
    dom.rmHitlPanel.style.display = 'none';

    const currentCp = inferCurrentRMCheckpoint(state.rm.hitlCheckpoint);
    state.rm.hitlCheckpoint = currentCp;
    const cpIdx = RM_STAGES.findIndex(s => s.id === currentCp);
    const completedStages = cpIdx >= 0 ? RM_STAGES.slice(0, cpIdx + 1).map(s => s.id) : ['scope_definition', 'keyword_extractor', 'checkpoint_1'];
    const nextStage = cpIdx >= 0 && cpIdx + 1 < RM_STAGES.length ? RM_STAGES[cpIdx + 1].id : 'paper_fetcher';

    try {
        updateRMPipelineTracker(nextStage, completedStages);
        rmTimer.resume();
        showRMCheckpointTransitionLoader(currentCp, feedback !== 'approve');
        appendLogLine(`Checkpoint '${currentCp}' approved. Resuming pipeline...`, 'info');
        saveRMSession();

        if (dom.rmPaperTitle) dom.rmPaperTitle.textContent = state.rm.title || 'Synthesizing Paper...';
        renderRMPaperLive(true);
        renderRMSourcesPanel();
    } catch (err) {
        console.warn('UI update failed before stream connection, proceeding with API call:', err);
    }

    await openRMEventStream(feedback);
}

// Opens (or reconnects to) the Research Mode SSE stream. Shared by handleRMApprove
// (after the user acts on a checkpoint) and reconnectRMStream (page reload while
// the pipeline is still running server-side) so both get the same retry and
// event-parsing behavior instead of two copies drifting apart.
async function openRMEventStream(message) {
    if (activeRMController) activeRMController.abort();
    activeRMController = new AbortController();

    let attempt = 0;
    const maxRetries = 999;
    let isTerminal = false;
    let receivedEventsCount = 0;

    while (attempt <= maxRetries && !isTerminal) {
        try {
            const reqMessage = attempt === 0 ? (message || '') : '';
            const reqPayload = { thread_id: state.rm.threadId, message: reqMessage };
            if (state.rm.lastSeq !== undefined && state.rm.lastSeq !== null) {
                reqPayload.from_seq = state.rm.lastSeq;
            }
            const reqHeaders = { 'Content-Type': 'application/json' };
            if (state.rm.lastSeq !== undefined && state.rm.lastSeq !== null) {
                reqHeaders['Last-Event-ID'] = String(state.rm.lastSeq);
            }

            const response = await fetch(`${API_BASE_URL}/research-mode/approve`, {
                method: 'POST',
                headers: reqHeaders,
                body: JSON.stringify(reqPayload),
                signal: activeRMController.signal
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                const events = buffer.split('\n\n');
                buffer = events.pop();

                for (const rawEvent of events) {
                    for (const line of rawEvent.split('\n')) {
                        if (!line.startsWith('data: ')) continue;
                        try {
                            const data = JSON.parse(line.slice(6));
                            receivedEventsCount++;
                            const evt = processRMSEEvent(data);
                            if (evt === 'checkpoint' || evt === 'completed' || evt === 'error') {
                                isTerminal = true;
                            }
                            attempt = 0;
                        } catch (e) {
                            console.warn('RM SSE parse error:', e);
                        }
                    }
                }
            }

            if (isTerminal) break;

            // PROXY ERROR HANDLING:
            // Stream ended without a terminal event (checkpoint/completed/error).
            // This happens when the host's reverse proxy terminates idle SSE connections
            // or when network drops occur between client and server during heavy LLM tasks.
            // We check the pipeline-status endpoint to verify if the server is still executing.
            if (!isTerminal && state.rm.threadId) {
                try {
                    const statusRes = await fetch(
                        `${API_BASE_URL}/research-mode/pipeline-status/${state.rm.threadId}`
                    );
                    if (statusRes.ok) {
                        const statusData = await statusRes.json();
                        if (statusData.running) {
                            // PROXY ERROR HANDLING:
                            // Backend is still executing — reconnect to pick up buffered
                            // events that arrived while disconnected.
                            appendLogLine('Connection dropped mid-run — reconnecting to resume stream…', 'warn');
                            if (dom.rmPipelineStatusTag) {
                                dom.rmPipelineStatusTag.textContent = 'Reconnecting to pipeline…';
                            }
                            attempt++;
                            await new Promise(res => setTimeout(res, 3000));
                            continue; // retry the SSE connection
                        } else if (statusData.completed) {
                            // Backend finished while we were disconnected — sync final state
                            try {
                                const syncRes = await fetch(`${API_BASE_URL}/research-mode/result/${state.rm.threadId}`);
                                if (syncRes.ok) {
                                    const syncData = await syncRes.json();
                                    if (syncData.values) applyRMStatePayload(syncData.values);
                                    if (syncData.report_rejected) {
                                        processRMSEEvent({ event: 'error', message: syncData.message });
                                    } else if (syncData.is_checkpoint) {
                                        const cp = (syncData.hitl_checkpoint || 'checkpoint_1').replace(/_(approved|revising)$/, '');
                                        renderRMHitlPanel(cp);
                                    } else if (!syncData.next || syncData.next.length === 0) {
                                        renderRMPaperFinal();
                                    }
                                }
                            } catch (e) {
                                console.warn('RM result sync failed:', e);
                            }
                            break;
                        }
                    }
                } catch (e) {
                    console.warn('Pipeline status check failed:', e);
                }
            }

            // Fallback sync if stream ended cleanly without a terminal event
            // (regardless of whether we received some events before the disconnect)
            if (!isTerminal && state.rm.threadId) {
                try {
                    const syncRes = await fetch(`${API_BASE_URL}/research-mode/result/${state.rm.threadId}`);
                    if (syncRes.ok) {
                        const syncData = await syncRes.json();
                        if (syncData.values) applyRMStatePayload(syncData.values);
                        if (syncData.report_rejected) {
                            processRMSEEvent({ event: 'error', message: syncData.message });
                        } else if (syncData.is_checkpoint) {
                            const cp = (syncData.hitl_checkpoint || 'checkpoint_1').replace(/_(approved|revising)$/, '');
                            renderRMHitlPanel(cp);
                        } else if (!syncData.next || syncData.next.length === 0) {
                            renderRMPaperFinal();
                        }
                    }
                } catch (e) {
                    console.warn('RM result sync failed:', e);
                }
            }
            break;

        } catch (e) {
            if (e.name === 'AbortError') {
                console.log('RM SSE stream aborted by user.');
                break;
            }
            attempt++;
            if (attempt > maxRetries) {
                showToast('Connection lost. Auto-reconnect failed: ' + e.message, 'error');
                break;
            }
            showToast(`Connection dropped. Auto-reconnecting (attempt ${attempt})...`, 'warning');
            await new Promise(res => setTimeout(res, Math.min(1000 * Math.pow(1.5, attempt - 1), 10000)));
        }
    }
}


// Re-attaches to an already-running pipeline after a page reload. The backend
// keeps executing in its own asyncio task regardless of whether any browser is
// listening, so without this the frontend was permanently frozen on whatever it
// last saw before the reload — new node_update/checkpoint/completed events had
// nowhere to land until the user manually clicked something. from_seq (read from
// state.rm.lastSeq, restored from localStorage) makes the backend replay only
// what was missed instead of restreaming from the start.
async function reconnectRMStream() {
    if (!state.rm.threadId) return;
    appendLogLine('Reconnecting to live pipeline…', 'info');
    await openRMEventStream('');
}

function appendLogLine(msg, level = 'info') {
    if (!dom.rmLogBody) return;
    const timeStr = new Date().toLocaleTimeString();
    const div = document.createElement('div');
    div.className = `rm-log-line ${level}`;
    div.style.color = level === 'warn' ? '#fbbf24' : (level === 'success' ? '#34d399' : (level === 'error' ? '#f87171' : '#38bdf8'));
    div.textContent = `[${timeStr}] ${msg}`;
    dom.rmLogBody.appendChild(div);
    dom.rmLogBody.scrollTop = dom.rmLogBody.scrollHeight;

    const count = dom.rmLogBody.children.length;
    if (dom.rmLogCount) dom.rmLogCount.textContent = `${count} events`;
}

function updateCorpusStats(stats) {
    if (!stats || typeof stats !== 'object') return;
    state.rm.corpus_stats = stats;

    const retrieved = stats.retrieved != null ? stats.retrieved : (stats.raw_papers_count != null ? stats.raw_papers_count : 0);
    const dedup = stats.after_dedup != null ? stats.after_dedup : (stats.dedup != null ? stats.dedup : retrieved);
    const screened = stats.screened != null ? stats.screened : (stats.screened_papers_count != null ? stats.screened_papers_count : 0);
    const included = stats.included != null ? stats.included : (stats.screened_papers_count != null ? stats.screened_papers_count : 0);
    const fulltext = stats.fulltext_fetched != null ? stats.fulltext_fetched : (stats.fulltext != null ? stats.fulltext : 0);

    const statRetrievedEl = dom.statRetrieved || document.getElementById('stat-retrieved');
    const statDedupEl = dom.statDedup || document.getElementById('stat-dedup');
    const statScreenedEl = dom.statScreened || document.getElementById('stat-screened');
    const statIncludedEl = dom.statIncluded || document.getElementById('stat-included');
    const statFulltextEl = dom.statFulltext || document.getElementById('stat-fulltext');
    const corpusBarEl = dom.rmCorpusStatsBar || document.getElementById('rm-corpus-stats-bar');

    if (statRetrievedEl) statRetrievedEl.textContent = retrieved;
    if (statDedupEl) statDedupEl.textContent = dedup;
    if (statScreenedEl) statScreenedEl.textContent = screened;
    if (statIncludedEl) statIncludedEl.textContent = included;
    if (statFulltextEl) statFulltextEl.textContent = fulltext;

    if (corpusBarEl) {
        // Clear the inline display:none from resetRMWorkspace() rather than
        // hardcoding a value - the element's real layout (display: grid, with
        // responsive column counts per breakpoint) lives in the .rm-corpus-stats
        // CSS class. Hardcoding 'flex' here was silently overriding that class
        // on every stats update, which is why the grid's mobile column rules
        // never actually applied and the cards overflowed into each other.
        corpusBarEl.style.display = '';
    }
}

function openPaperInspector(paper) {
    if (!paper || !dom.paperDetailModal) return;
    const title = escapeHtml(paper.title || 'Paper Details');
    if (dom.modalPaperTitle) dom.modalPaperTitle.textContent = paper.title || 'Paper Details';
    if (dom.modalPaperBody) {
        const authors = Array.isArray(paper.authors) ? paper.authors.map(escapeHtml).join(', ') : escapeHtml(paper.authors || 'N/A');
        const venue = escapeHtml(paper.venue || paper.journal || 'Academic Index');
        const year = escapeHtml(String(paper.year || 'N/A'));
        const doi = paper.doi ? `<a href="https://doi.org/${encodeURIComponent(paper.doi)}" target="_blank" rel="noopener noreferrer" style="color: var(--academic-blue);">${escapeHtml(paper.doi)}</a>` : 'N/A';
        
        let pdfLink = '<span style="color: var(--text-muted);">No PDF Direct Link</span>';
        if (typeof paper.pdf_url === 'string' && paper.pdf_url.trim()) {
            try {
                const parsedUrl = new URL(safeHttpUrl(paper.pdf_url) || 'invalid:');
                if (parsedUrl.protocol === 'http:' || parsedUrl.protocol === 'https:') {
                    const safeHref = escapeHtml(parsedUrl.href);
                    pdfLink = `<a href="${safeHref}" target="_blank" rel="noopener noreferrer" style="color: var(--emerald-accent);">[Open Access PDF Link]</a>`;
                }
            } catch (e) {
                // Invalid URL, retains no-link fallback
            }
        }

        const score = escapeHtml(String(paper.relevance_score ?? 'N/A'));
        const excerpt = escapeHtml(paper.fulltext_excerpt || paper.abstract || 'No full-text excerpt extracted for this paper.');

        dom.modalPaperBody.innerHTML = `
            <div style="margin-bottom: 1rem;">
                <h4 style="margin: 0 0 0.5rem 0; color: var(--academic-blue); font-size: 1.05rem;">${title}</h4>
                <p style="margin: 0.25rem 0; color: var(--text-muted);"><strong>Authors:</strong> ${authors}</p>
                <p style="margin: 0.25rem 0; color: var(--text-muted);"><strong>Venue / Year:</strong> ${venue} (${year})</p>
                <p style="margin: 0.25rem 0;"><strong>DOI:</strong> ${doi}</p>
                <p style="margin: 0.25rem 0;"><strong>PDF Source:</strong> ${pdfLink}</p>
            </div>
            <div style="background: var(--bg-surface); padding: 0.75rem; border-radius: 8px; border: 1px solid var(--border-color); margin-bottom: 1rem;">
                <p style="margin: 0 0 0.25rem 0; font-weight: 600;">Relevance Score: <span style="color: var(--academic-blue);">${score}/10</span></p>
                <div style="margin-top: 1rem;">
                    <h5 style="margin: 0 0 0.5rem 0; font-size: 0.95rem; font-weight: 600;">Extracted Full-Text Excerpt</h5>
                    <pre style="white-space: pre-wrap; font-family: monospace; font-size: 0.82rem; background: #0f172a; color: #f8fafc; padding: 0.85rem; border-radius: 8px; max-height: 250px; overflow-y: auto;">${excerpt}</pre>
                </div>
            </div>
        `;
    }
    dom.paperDetailModal.style.display = 'flex';
}

function processRMSEEvent(data) {
    const trackerContainer = document.querySelector('.pipeline-tracker-container');
    const paperCard = document.getElementById('rm-paper-card');

    // Every buffered event carries a seq. Only some of them used to update
    // lastSeq, so a reconnect asked the server to replay from a stale point and
    // the tracker jumped backwards through nodes it had already passed.
    if (data.seq !== undefined && data.seq !== null) state.rm.lastSeq = data.seq;

    if (data.event === 'node_start') {
        rmTimer.resume();
        updateRMPipelineTracker(data.node);
        updateRMTransitionLoaderNode(data.node);
        appendLogLine(`Node started: ${rmStageLabel(data.node)}`, 'info');
        if (trackerContainer) trackerContainer.classList.add('active-execution');
        if (paperCard) paperCard.classList.add('active-execution');
        saveRMSession();
    } else if (data.event === 'token_stream') {
        // Not buffered server-side and not part of the paper state; it is the only
        // signal that a long node is still alive, so it drives the status tag.
        noteRMTokenActivity(data.node);
    } else if (data.event === 'resume') {
        rmTimer.resume();
        appendLogLine('Pipeline resumed.', 'info');
    } else if (data.event === 'node_update') {
        applyRMStatePayload(data.data || {});
        renderRMSourcesPanel();
        appendLogLine(`Node updated: ${rmStageLabel(data.node)}`, 'success');
        if (data.data && data.data.corpus_stats) updateCorpusStats(data.data.corpus_stats);
        renderRMPaperLive();
        saveRMSession();
    } else if (data.event === 'checkpoint') {
        rmTimer.pause();
        applyRMStatePayload(data.state || {});
        const cp = inferCurrentRMCheckpoint(data.hitl_checkpoint);
        state.rm.hitlCheckpoint = cp;
        state.rm.hitlCheckpointPending = true;
        state.rm.hitlApproved = false;
        appendLogLine(`Checkpoint reached — your review is needed (Step ${cp.replace('checkpoint_', '')} of 4)`, 'warn');
        if (data.state && data.state.corpus_stats) updateCorpusStats(data.state.corpus_stats);
        updateRMPipelineTracker(cp);
        if (trackerContainer) trackerContainer.classList.remove('active-execution');
        if (paperCard) paperCard.classList.remove('active-execution');
        renderRMHitlPanel(cp);
        // The pipeline blocks here until the user acts, and the panel can be far
        // below the fold on a long paper, so it has to announce itself.
        showToast('Checkpoint reached — your review is required to continue.', 'warning');
        dom.rmHitlPanel?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        saveRMSession();
    } else if (data.event === 'completed') {
        state.rm.status = 'completed';
        state.rm.hitlCheckpointPending = false;
        state.rm.hitlApproved = true;
        applyRMStatePayload(data.state || {});
        rmTimer.stop();
        appendLogLine(`Pipeline execution completed!`, 'success');
        if (data.state && data.state.corpus_stats) updateCorpusStats(data.state.corpus_stats);
        updateRMPipelineTracker('title', RM_STAGES.map(s => s.id));
        if (trackerContainer) trackerContainer.classList.remove('active-execution');
        if (paperCard) paperCard.classList.remove('active-execution');
        renderRMPaperFinal();
        saveRMSession();
    } else if (data.event === 'error') {
        rmTimer.stop();
        hideRMCheckpointTransitionLoader();
        appendLogLine(`Pipeline Error: ${data.message}`, 'error');
        if (trackerContainer) trackerContainer.classList.remove('active-execution');
        if (paperCard) paperCard.classList.remove('active-execution');
        if (dom.rmPipelineStatusTag) dom.rmPipelineStatusTag.textContent = 'Pipeline stopped — error';
        // A failed or rejected paper must not keep the completed-paper export
        // controls (a restored session may have rendered them before the check).
        [dom.rmCopyPaperBtn, dom.rmExportDropdown, dom.rmExportPdfBtn].forEach(el => {
            if (el) el.style.display = 'none';
        });
        // The run is dead, so the "synthesizing" spinner must not keep spinning
        // forever. Show whatever was produced before the failure instead.
        state.rm.status = 'error';
        renderRMPaperLive(false);
        showToast(data.message || 'Pipeline error occurred.', 'error');
        saveRMSession();
    }
    return data.event;
}

const SOURCE_BADGE_MAP = {
    openalex: { src: 'assets/openalex.png', label: 'OpenAlex', cls: 'badge-openalex' },
    semantic_scholar: { src: 'assets/semantic-scholar.png', label: 'Semantic Scholar', cls: 'badge-semantic' },
    semantic: { src: 'assets/semantic-scholar.png', label: 'Semantic Scholar', cls: 'badge-semantic' },
    arxiv: { src: 'assets/arxiv.png', label: 'arXiv', cls: 'badge-arxiv' },
    crossref: { src: 'assets/crossref.svg', label: 'Crossref', cls: 'badge-crossref' },
    pubmed: { src: 'assets/pubmed.svg', label: 'PubMed', cls: 'badge-pubmed' },
    opencitations: { src: 'assets/opencitations.svg', label: 'OpenCitations', cls: 'badge-opencitations' },
    europe_pmc: { src: 'assets/europe-pmc.png', label: 'Europe PMC', cls: 'badge-europepmc' },
    europepmc: { src: 'assets/europe-pmc.png', label: 'Europe PMC', cls: 'badge-europepmc' },
    doaj: { src: 'assets/doaj.svg', label: 'DOAJ', cls: 'badge-doaj' },
    datacite: { src: 'assets/datacite.png', label: 'DataCite', cls: 'badge-datacite' },
    orcid: { src: 'assets/orcid.svg', label: 'ORCID', cls: 'badge-orcid' },
    tavily_web_fallback: { src: 'assets/openalex.png', label: 'Web Fallback', cls: 'badge-generic' }
};

function getPaperSource(p) {
    if (!p) return 'unknown';
    const src = String(p.retrieval_source || p.retrievalSource || p.source || '').toLowerCase().trim();
    if (src.includes('openalex')) return 'openalex';
    if (src.includes('semantic') || src === 's2') return 'semantic_scholar';
    if (src.includes('arxiv')) return 'arxiv';
    // europe_pmc must be tested BEFORE pubmed: 'europe_pmc' contains the
    // substring 'pmc', which would otherwise be swallowed by the PubMed match.
    if (src.includes('europe_pmc') || src.includes('europepmc')) return 'europe_pmc';
    if (src.includes('crossref')) return 'crossref';
    if (src.includes('pubmed') || src.includes('ncbi') || src === 'pmc') return 'pubmed';
    if (src.includes('opencitations') || src.includes('coci')) return 'opencitations';
    if (src.includes('doaj')) return 'doaj';
    if (src.includes('datacite')) return 'datacite';
    if (src.includes('orcid')) return 'orcid';
    if (src.includes('tavily')) return 'tavily_web_fallback';

    // Inference fallback if retrieval_source field was omitted
    if (p.arxiv_id || (p.doi && p.doi.toLowerCase().includes('arxiv'))) return 'arxiv';
    if (p.pmid || (p.doi && p.doi.toLowerCase().includes('pubmed'))) return 'pubmed';
    return 'unknown';
}

// Unknown sources get a neutral "Academic" pill rather than impersonating OpenAlex.
function sourceBadgeHtml(input) {
    const srcKey = typeof input === 'object' ? getPaperSource(input) : getPaperSource({ retrieval_source: input });
    const entry = SOURCE_BADGE_MAP[srcKey];
    if (!entry) {
        return `<span class="source-badge-pill badge-generic"><span>Academic</span></span>`;
    }
    return `<span class="source-badge-pill ${entry.cls}"><img src="${entry.src}" alt="${entry.label}" width="12" height="12" loading="lazy"><span>${entry.label}</span></span>`;
}

function renderRMSourcesPanel() {
    const panel = dom.rmSourcesPanel || document.getElementById('rm-sources-panel');
    const grid = dom.rmSourcesGrid || document.getElementById('rm-sources-grid');
    const countTag = dom.rmSourcesCountTag || document.getElementById('rm-sources-count-tag');
    if (!panel || !grid) return;

    const papers = getScreenedPapers();
    if (papers.length === 0) {
        panel.style.display = 'none';
        return;
    }

    panel.style.display = 'flex';
    const filter = (state.rm.activeSourceFilter || 'all').toLowerCase();

    const filtered = filter === 'all'
        ? papers
        : papers.filter(p => {
            const src = getPaperSource(p);
            if (filter === 'semantic' || filter === 'semantic_scholar') {
                return src === 'semantic_scholar' || src === 'semantic';
            }
            return src.includes(filter);
        });

    if (countTag) {
        countTag.textContent = `${filtered.length} paper${filtered.length === 1 ? '' : 's'}`;
    }

    if (filtered.length === 0) {
        grid.innerHTML = `
            <div class="sources-empty-state">
                <p style="color: var(--text-muted); font-size: 0.8rem; text-align: center;">No papers from ${escapeHtml(filter)} in current corpus.</p>
            </div>
        `;
        return;
    }

    grid.innerHTML = filtered.map((p) => {
        const title = escapeHtml(p.title || 'Untitled Paper');
        const rawAuthors = p.authors || [];
        const authors = Array.isArray(rawAuthors)
            ? rawAuthors.slice(0, 2).map(escapeHtml).join(', ') + (rawAuthors.length > 2 ? ' et al.' : '')
            : escapeHtml(String(rawAuthors || 'Unknown'));
        const year = escapeHtml(String(p.year || 'n.d.'));
        const badge = sourceBadgeHtml(p);
        const doi = p.doi ? `DOI: ${escapeHtml(p.doi)}` : (p.pmid ? `PMID: ${escapeHtml(p.pmid)}` : '');
        const realIdx = papers.indexOf(p);

        return `
            <div class="rm-source-item" data-paper-index="${realIdx}">
                <div class="source-item-top">
                    ${badge}
                    <span class="source-item-year">${year}</span>
                </div>
                <h4 class="source-item-title">${title}</h4>
                <div class="source-item-meta">
                    <span class="source-item-authors">${authors}</span>
                    ${doi ? `<span class="source-item-doi">${doi}</span>` : ''}
                </div>
            </div>
        `;
    }).join('');

    grid.querySelectorAll('.rm-source-item').forEach(item => {
        item.addEventListener('click', () => {
            const idx = parseInt(item.dataset.paperIndex, 10);
            if (papers[idx]) openPaperInspector(papers[idx]);
        });
    });
}

// Best-effort only: matches "(Lastname, YYYY)" and "(Lastname et al., YYYY)"
// against screened papers' first-author last name + year. Model-generated
// citation text won't always map cleanly to a specific screened paper
// (paraphrased names, multi-author collisions, references outside the
// screened set) — unmatched citations are left as plain text, unchanged
// from today's behavior.
function linkCitations(html) {
    const papers = getScreenedPapers();
    if (papers.length === 0) return html;

    const byLastNameYear = new Map();
    papers.forEach((p, idx) => {
        const firstAuthor = Array.isArray(p.authors) ? p.authors[0] : p.authors;
        if (!firstAuthor || !p.year) return;
        const lastName = String(firstAuthor).trim().split(/\s+/).pop();
        if (!lastName) return;
        const key = `${lastName.toLowerCase()}|${p.year}`;
        if (!byLastNameYear.has(key)) byLastNameYear.set(key, idx);
    });

    if (byLastNameYear.size === 0) return html;

    // Work on text nodes of the sanitised DOM, never on the HTML string: a
    // regex over markup can match inside an attribute value and break out of it.
    const template = document.createElement('template');
    template.innerHTML = html;
    const root = template.content;
    const skipTags = new Set(['A', 'CODE', 'PRE', 'SCRIPT', 'STYLE']);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
            for (let parent = node.parentNode; parent && parent !== root; parent = parent.parentNode) {
                if (skipTags.has(parent.nodeName)) return NodeFilter.FILTER_REJECT;
            }
            return NodeFilter.FILTER_ACCEPT;
        },
    });
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);

    const citationRegex = /\(([A-Z][a-zA-Z'-]+)(?:\s+et al\.)?,\s*(\d{4})\)/;
    textNodes.forEach((node) => {
        let rest = node;
        while (rest && rest.nodeValue) {
            const match = citationRegex.exec(rest.nodeValue);
            if (!match) break;
            const end = match.index + match[0].length;
            const paperIdx = byLastNameYear.get(`${match[1].toLowerCase()}|${match[2]}`);
            if (paperIdx === undefined) {
                rest = end < rest.nodeValue.length ? rest.splitText(end) : null;
                continue;
            }
            const matchNode = match.index > 0 ? rest.splitText(match.index) : rest;
            const after = matchNode.nodeValue.length > match[0].length
                ? matchNode.splitText(match[0].length)
                : null;
            const link = document.createElement('a');
            link.href = '#';
            link.className = 'citation-link';
            link.dataset.paperIndex = String(paperIdx);
            link.textContent = match[0];
            matchNode.parentNode.replaceChild(link, matchNode);
            rest = after;
        }
    });
    return template.innerHTML;
}

function updatePaperStats(markdownText) {
    const text = markdownText || '';
    const words = text.trim() ? text.trim().split(/\s+/).length : 0;
    const readMinutes = Math.max(1, Math.ceil(words / 220));

    if (dom.paperStatsBadges) {
        if (words > 20) {
            dom.paperStatsBadges.style.display = 'flex';
            if (dom.paperWordCountVal) dom.paperWordCountVal.textContent = `${words.toLocaleString()} words`;
            if (dom.paperReadTimeVal) dom.paperReadTimeVal.textContent = `~${readMinutes} min read`;
            if (dom.togglePaperOutlineBtn) dom.togglePaperOutlineBtn.style.display = 'inline-flex';
        } else {
            dom.paperStatsBadges.style.display = 'none';
            if (dom.togglePaperOutlineBtn) dom.togglePaperOutlineBtn.style.display = 'none';
        }
    }
}

let activePaperOutlineObserver = null;

function updatePaperTableOfContents(scrollerEl) {
    if (!dom.paperOutlineList || !dom.paperOutlineRail) return;
    const headings = scrollerEl.querySelectorAll('h1, h2, h3');
    if (headings.length <= 1) {
        dom.paperOutlineRail.style.display = 'none';
        return;
    }

    // Keep display state if user manually toggled, else default visible on wide screens
    if (window.innerWidth > 960 && dom.paperOutlineRail.style.display === 'none' && !dom.togglePaperOutlineBtn?.classList.contains('manually-hidden')) {
        dom.paperOutlineRail.style.display = 'flex';
    }

    dom.paperOutlineList.innerHTML = '';

    headings.forEach((h, idx) => {
        const id = h.id || `paper-sec-${idx}`;
        h.id = id;
        const text = h.textContent.replace(/^#+\s*/, '').trim();
        const level = h.tagName.toLowerCase();

        const li = document.createElement('li');
        li.className = 'outline-item';
        li.innerHTML = `
            <a href="#${id}" class="outline-link ${level === 'h3' ? 'level-3' : ''}" title="${escapeHtml(text)}">
                ${escapeHtml(text)}
            </a>
        `;
        li.querySelector('a').addEventListener('click', (e) => {
            e.preventDefault();
            h.scrollIntoView({ behavior: 'smooth', block: 'start' });
            dom.paperOutlineList.querySelectorAll('.outline-link').forEach(l => l.classList.remove('active'));
            e.currentTarget.classList.add('active');
        });
        dom.paperOutlineList.appendChild(li);
    });

    if (activePaperOutlineObserver) {
        activePaperOutlineObserver.disconnect();
    }

    if (window.IntersectionObserver) {
        activePaperOutlineObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const id = entry.target.id;
                    dom.paperOutlineList.querySelectorAll('.outline-link').forEach(link => {
                        link.classList.toggle('active', link.getAttribute('href') === `#${id}`);
                    });
                }
            });
        }, { root: scrollerEl, rootMargin: '0px 0px -70% 0px', threshold: 0 });

        headings.forEach(h => activePaperOutlineObserver.observe(h));
    }
}

function renderRMPaperLive(isStreaming = true) {
    if (dom.rmPaperTitle) dom.rmPaperTitle.textContent = state.rm.title || 'Synthesizing Academic Paper...';
    if (dom.rmPaperOutput) {
        const scroller = dom.rmPaperOutput;
        const prevScroll = scroller.scrollTop;
        const wasAtBottom = scroller.scrollHeight - scroller.clientHeight - prevScroll < 40;

        const rawMd = getPaperMarkdown();
        updatePaperStats(rawMd);

        let content = linkCitations(renderMarkdown(rawMd));
        if (isStreaming) {
            content += '<span class="typing-cursor"></span>';
        }
        scroller.innerHTML = content;

        scroller.querySelectorAll('.citation-link').forEach((link) => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const idx = parseInt(link.dataset.paperIndex, 10);
                const papers = getScreenedPapers();
                if (papers[idx]) openPaperInspector(papers[idx]);
            });
        });

        updatePaperTableOfContents(scroller);

        scroller.scrollTop = wasAtBottom ? scroller.scrollHeight : prevScroll;
    }
}

function renderRMPaperFinal() {
    hideRMCheckpointTransitionLoader();
    renderRMPaperLive(false);
    if (dom.rmCopyPaperBtn) dom.rmCopyPaperBtn.style.display = 'inline-flex';
    if (dom.rmExportDropdown) dom.rmExportDropdown.style.display = 'inline-flex';
    if (dom.rmExportPdfBtn) dom.rmExportPdfBtn.style.display = 'inline-flex';
    showToast('Academic Paper Synthesis Completed!', 'success');
}

function getPaperMarkdown() {
    const s = state.rm;
    let md = `# ${s.title || 'Academic Research Report'}\n\n`;
    if (s.abstract) md += `## Abstract\n${s.abstract}\n\n`;
    if (s.introduction) md += `## 1. Introduction\n${s.introduction}\n\n`;
    if (s.literatureReview) md += `## 2. Literature Review\n${s.literatureReview}\n\n`;
    if (s.researchGap) md += `## 3. Research Gap\n${s.researchGap}\n\n`;
    if (s.researchObjectives && s.researchObjectives.length) md += `## 4. Research Objectives\n${s.researchObjectives.map((o, i) => `${i+1}. ${o}`).join('\n')}\n\n`;
    if (s.researchQuestions && s.researchQuestions.length) md += `## 5. Research Questions\n${s.researchQuestions.map((q, i) => `**RQ${i+1}**: ${q}`).join('\n\n')}\n\n`;
    if (s.conceptualFramework) md += `## 6. Conceptual Framework\n${s.conceptualFramework}\n\n`;
    if (s.hypotheses && s.hypotheses.length) md += `## 7. Hypotheses\n${s.hypotheses.map((h, i) => `- **H${i+1}**: ${h}`).join('\n')}\n\n`;
    if (s.researchDesign) md += `## 8. Methodology\n### 8.1 Research Design\n${s.researchDesign}\n\n### 8.2 Data Collection\n${s.dataCollectionPlan}\n\n### 8.3 Data Analysis\n${s.dataAnalysisPlan}\n\n`;
    if (s.results) md += `## 9. Results\n${s.results}\n\n`;
    if (s.discussion) md += `## 10. Discussion\n${s.discussion}\n\n${s.implications ? `### 10.1 Implications\n${s.implications}\n\n` : ''}`;
    if (s.limitations) md += `## 11. Limitations\n${s.limitations}\n\n`;
    if (s.conclusion) md += `## 12. Conclusion\n${s.conclusion}\n\n`;
    if (s.futureScope && s.futureScope.length) md += `## 13. Future Scope\n${Array.isArray(s.futureScope) ? s.futureScope.map(f => `- ${f}`).join('\n') : s.futureScope}\n\n`;
    if (s.references && s.references.length) md += `## 14. References\n${s.references.map(r => `- ${r}`).join('\n')}\n\n`;
    if (s.appendices) md += `## 15. Appendices\n${s.appendices}\n\n`;
    return md;
}


function toggleExportMenu(event) {
    if (event) event.stopPropagation();
    const menu = document.getElementById("rm-export-menu");
    if (menu) {
        menu.classList.toggle("show");
    }
}

// Bound here rather than with onclick attributes so the CSP needs no 'unsafe-inline'.
document.getElementById("rm-export-main-btn")?.addEventListener("click", toggleExportMenu);
document.querySelectorAll("#rm-export-menu [data-export-format]").forEach((item) => {
    item.addEventListener("click", () => exportReport(item.dataset.exportFormat));
});

document.addEventListener("click", () => {
    const menu = document.getElementById("rm-export-menu");
    if (menu && menu.classList.contains("show")) {
        menu.classList.remove("show");
    }
});

async function exportReport(format = 'pdf') {
    if (!state.rm.threadId) return;
    const menu = document.getElementById("rm-export-menu");
    if (menu) menu.classList.remove("show");

    const endpoint = format === 'docx' 
        ? `${API_BASE_URL}/research-mode/export/docx/${state.rm.threadId}` 
        : `${API_BASE_URL}/research-mode/export/${state.rm.threadId}`;
    const ext = format === 'docx' ? 'docx' : 'pdf';

    try {
        const res = await fetch(endpoint, { method: 'POST' });
        if (res.ok) {
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `academic_paper_${state.rm.threadId.slice(0, 8)}.${ext}`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            showToast(`Exported ${ext.toUpperCase()} successfully!`, 'success');
        } else {
            showToast(`Failed to export ${ext.toUpperCase()}.`, 'error');
        }
    } catch (e) {
        showToast(`Error exporting ${ext.toUpperCase()}: ` + e.message, 'error');
    }
}



/* ==========================================================================
   DEEPSEARCH MODE HANDLERS & HELPERS
   ========================================================================== */

// Shimmering placeholders while the planner works. The skeleton styles already
// existed but nothing ever rendered them, so the panel just showed bare text.
function renderPlanSkeleton() {
    if (dom.approvalPsText) {
        dom.approvalPsText.innerHTML =
            '<span class="skeleton skeleton-ps" style="width:92%"></span>' +
            '<span class="skeleton skeleton-ps" style="width:85%"></span>' +
            '<span class="skeleton skeleton-ps"></span>';
    }
    if (dom.approvalSubtasksContainer) {
        dom.approvalSubtasksContainer.innerHTML =
            '<span class="skeleton skeleton-task" style="width:100%"></span>'.repeat(4);
    }
}

async function handlePlanResearch() {
    const query = dom.queryInput.value.trim();
    if (!query) {
        showToast('Please enter a research query.', 'warning');
        return;
    }

    state.query = query;
    dom.approvalQueryDisplay.textContent = `"${query}"`;
    switchPanel(dom.approvalPanel);

    setDeepSearchBusy(true);
    renderPlanSkeleton();

    try {
        const res = await fetch(`${API_BASE_URL}/research/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: state.query, search_topic: state.searchTopic })
        });
        const contentType = res.headers.get('content-type') || '';
        if (!res.ok || !contentType.includes('application/json')) {
            throw new Error(`Server returned ${res.status} (${res.statusText || 'Non-JSON response'}). Make sure the backend server is running on ${API_BASE_URL}.`);
        }
        markBackendOnline();
        const data = await res.json();
        if (data.status === 'error') {
            showToast(data.error || 'Failed to create plan.', 'error');
            return;
        }

        state.threadId = data.thread_id;
        state.ps = data.ps;
        state.plan = data.plan || [];
        renderApprovalPanel();
        updateNewRunVisibility();

    } catch (e) {
        showToast('Error planning research: ' + e.message, 'error');
        // The panel already switched and is showing shimmer placeholders that
        // will never resolve, so send the user back to the query box.
        switchPanel(dom.landingPanel);
    } finally {
        setDeepSearchBusy(false);
    }
}

function renderApprovalPanel() {
    dom.approvalPsText.innerHTML = '';
    dom.approvalPsText.innerHTML = renderMarkdownSafe(state.ps);
    dom.approvalSubtasksContainer.innerHTML = '';
    state.plan.forEach((task, idx) => {
        const item = document.createElement('div');
        item.className = 'subtask-item';
        item.innerHTML = `
            <div class="subtask-number">${idx + 1}</div>
            <div class="subtask-content">${renderMarkdownSafe(task)}</div>
        `;
        dom.approvalSubtasksContainer.appendChild(item);
    });
}

async function handleRevision() {
    const feedback = dom.feedbackInput.value.trim();
    if (!feedback) return;

    renderPlanSkeleton();
    // A revision keeps the user on the approval panel: the graph loops back to the
    // planner and interrupts again. Sending them to the execution workspace (as
    // this used to) left them staring at an empty report that never filled in.
    await submitPlanApprovalWithMessage(feedback, { isRevision: true });
}

async function submitPlanApproval() {
    await submitPlanApprovalWithMessage('approve', { isRevision: false });
}

async function submitPlanApprovalWithMessage(message, { isRevision = false } = {}) {
    if (!state.threadId) return;

    setDeepSearchBusy(true);

    if (!isRevision) {
        state.finalAnswer = '';
        state.workers = {};
        renderWorkers();
        if (dom.reportOutput) dom.reportOutput.innerHTML = '';
        switchPanel(dom.workspacePanel);
        researchTimer.start();
    }

    try {
        const response = await fetch(`${API_BASE_URL}/research/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ thread_id: state.threadId, message: message })
        });

        // Without this a 500 from the backend produced an unreadable body, no
        // events, and a workspace that sat blank with the timer still running.
        if (!response.ok || !response.body) {
            throw new Error(`Server returned ${response.status} ${response.statusText || ''}`.trim());
        }
        setResearchBackdropActive(true);

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            const events = buffer.split('\n\n');
            buffer = events.pop();

            for (const rawEvent of events) {
                for (const line of rawEvent.split('\n')) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        processDeepSearchSSEEvent(JSON.parse(line.slice(6)));
                    } catch (e) {
                        console.warn('DeepSearch SSE parse error:', e);
                    }
                }
            }
        }
    } catch (e) {
        researchTimer.stop();
        if (dom.reportStreamingIndicator) dom.reportStreamingIndicator.style.display = 'none';
        showToast('Research stream failed: ' + e.message, 'error');
        // Nothing arrived, so put the user back where they can retry instead of
        // stranding them on an empty workspace.
        if (isRevision || !state.finalAnswer) switchPanel(dom.approvalPanel);
    } finally {
        setDeepSearchBusy(false);
    }
}

// Disables the approve/revise controls while a stream is open so a double click
// cannot start two concurrent runs against the same thread.
function setDeepSearchBusy(busy) {
    [dom.approvePlanBtn, dom.submitFeedbackBtn, dom.planResearchBtn].forEach(btn => {
        if (btn) btn.disabled = busy;
    });
}

function processDeepSearchSSEEvent(data) {
    if (data.event === 'node_start') {
        if (data.node && data.node.startsWith('researcher')) {
            const existing = state.workers[data.node];
            state.workers[data.node] = {
                task: data.task,
                status: 'running',
                logs: existing ? existing.logs : []
            };
            renderWorkers();
        }
    } else if (data.event === 'node_update') {
        // The backend sends node_update for every finished node. Nothing consumed
        // it, so researcher cards stayed pinned at "running" for the whole run.
        if (data.node && data.node.startsWith('researcher') && state.workers[data.node]) {
            state.workers[data.node].status = 'completed';
            renderWorkers();
        }
        if (data.node === 'aggregator' && dom.reportStreamingIndicator) {
            dom.reportStreamingIndicator.style.display = 'none';
        }
    } else if (data.event === 'researcher_search') {
        // data.task is the researcher's query string, which is what node_start
        // stored on the worker record.
        const wKey = Object.keys(state.workers).find(k => state.workers[k].task === data.task);
        if (wKey && data.query) {
            state.workers[wKey].logs.push(data.query);
            renderWorkers();
        }
    } else if (data.event === 'aggregator_token') {
        if (dom.reportStreamingIndicator) dom.reportStreamingIndicator.style.display = 'flex';
        state.finalAnswer += data.token;
        dom.reportOutput.innerHTML = renderMarkdown(state.finalAnswer) + '<span class="streaming-cursor">|</span>';
    } else if (data.event === 'awaiting_approval') {
        // The planner revised the plan and interrupted again. This event had no
        // handler at all, so "Request Revision" simply hung forever.
        researchTimer.stop();
        state.ps = data.ps || state.ps;
        state.plan = data.plan || state.plan;
        renderApprovalPanel();
        switchPanel(dom.approvalPanel);
        if (dom.feedbackInput) dom.feedbackInput.value = '';
        if (dom.submitFeedbackBtn) dom.submitFeedbackBtn.style.display = 'none';
        if (dom.approvePlanBtn) dom.approvePlanBtn.style.display = 'flex';
        showToast('Plan revised. Review it and approve when ready.', 'info');
    } else if (data.event === 'completed') {
        researchTimer.stop();
        if (dom.reportStreamingIndicator) dom.reportStreamingIndicator.style.display = 'none';
        state.finalAnswer = data.final_answer || state.finalAnswer;
        dom.reportOutput.innerHTML = renderMarkdown(state.finalAnswer);
        // Any researcher still marked running finished with the graph.
        Object.values(state.workers).forEach(w => { if (w.status === 'running') w.status = 'completed'; });
        renderWorkers();
        dom.copyMdBtn.style.display = 'inline-flex';
        dom.downloadMdBtn.style.display = 'inline-flex';
        dom.workspaceNewResearchBtn.style.display = 'inline-flex';
        if (data.citations) renderCitations(data.citations);
    } else if (data.event === 'error') {
        researchTimer.stop();
        if (dom.reportStreamingIndicator) dom.reportStreamingIndicator.style.display = 'none';
        showToast(data.message || 'Research pipeline error.', 'error');
    }
}

function renderWorkers() {
    if (!dom.workersListContainer) return;
    dom.workersListContainer.innerHTML = '';
    Object.entries(state.workers).forEach(([id, w]) => {
        const card = document.createElement('div');
        card.className = 'worker-card';
        const searches = (w.logs || []).slice(-3);
        card.innerHTML = `
            <div class="worker-header">
                <strong>${escapeHtml(id.replace('_', ' '))}</strong>
                <span class="worker-status ${escapeHtml(w.status)}">${escapeHtml(w.status)}</span>
            </div>
            <div class="worker-task">${escapeHtml(w.task)}</div>
            ${searches.length ? `<div class="worker-queries">${
                searches.map(q => `<span class="worker-query">${escapeHtml(q)}</span>`).join('')
            }</div>` : ''}
        `;
        dom.workersListContainer.appendChild(card);
    });
}

function renderCitations(citations) {
    if (!dom.workspaceSourcesContainer) return;
    dom.workspaceSourcesContainer.innerHTML = '';
    citations.forEach(url => {
        const safeUrl = safeHttpUrl(url);
        const card = document.createElement(safeUrl ? 'a' : 'div');
        card.className = 'source-card';
        if (safeUrl) card.href = safeUrl;
        if (safeUrl) card.target = '_blank';
        if (safeUrl) card.rel = 'noopener noreferrer';
        card.innerHTML = `<i data-lucide="link"></i><span>${escapeHtml(url)}</span>`;
        dom.workspaceSourcesContainer.appendChild(card);
    });
    dom.workspaceSourcesSection.style.display = 'block';
    refreshIcons();
}

// "New Run" is shared by both modes. It used to always wipe the Research Mode
// session and drop the user on the DeepSearch landing page, so pressing it from
// a live Research Mode run destroyed that run.
function resetToLanding() {
    if (state.mode === 'researchmode') {
        resetResearchModeForm();
        updateNewRunVisibility();
        return;
    }
    researchTimer.reset();
    setResearchBackdropActive(false);
    state.threadId = null;
    state.ps = '';
    state.plan = [];
    state.finalAnswer = '';
    state.workers = {};
    renderWorkers();
    if (dom.reportOutput) dom.reportOutput.innerHTML = '';
    if (dom.workspaceSourcesSection) dom.workspaceSourcesSection.style.display = 'none';
    [dom.copyMdBtn, dom.downloadMdBtn, dom.workspaceNewResearchBtn].forEach(b => {
        if (b) b.style.display = 'none';
    });
    dom.queryInput.value = '';
    dsPlaceholderCycle?.start();
    switchPanel(dom.landingPanel);
    updateNewRunVisibility();
}

// The header button is markup-hidden and nothing ever revealed it, so there was
// no way out of a run except a reload.
function updateNewRunVisibility() {
    if (!dom.newResearchBtn) return;
    const active = state.mode === 'researchmode' ? !!state.rm.threadId : !!state.threadId;
    dom.newResearchBtn.style.display = active ? 'flex' : 'none';
}

async function copyToClipboard(text, btnEl) {
    const orig = btnEl.innerHTML;
    try {
        // navigator.clipboard is undefined on insecure origins; this threw and
        // left the button showing "Copied!" for a copy that never happened.
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
        } else {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
        }
        btnEl.innerHTML = '<span>Copied!</span>';
    } catch (e) {
        btnEl.innerHTML = '<span>Copy failed</span>';
        showToast('Could not copy to clipboard: ' + e.message, 'error');
    }
    setTimeout(() => { btnEl.innerHTML = orig; refreshIcons(); }, 2000);
}

function downloadMarkdownReport() {
    const blob = new Blob([state.finalAnswer], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `research_report_${state.threadId ? state.threadId.slice(0,8) : 'export'}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
}

function showToast(msg, type = 'info') {
    // showToast is called from error paths that can fire before the DOM cache is
    // built. Throwing here used to mask the original error.
    const container = dom.toastContainer || document.getElementById('toast-container');
    if (!container) {
        console.warn(`[toast:${type}] ${msg}`);
        return;
    }
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

/* ==========================================================================
   CANVAS TEXT ANIMATED WAVY GRADIENT ENGINE (Research & Search Modes)
   ========================================================================== */

function initCanvasText() {
    const targets = document.querySelectorAll('.canvas-text-target');
    targets.forEach(el => {
        const text = el.getAttribute('data-text') || el.textContent.trim();
        if (!text) return;

        // Clean any existing canvas if reinitializing
        el.innerHTML = '';
        el.style.position = 'relative';
        el.style.display = 'inline-block';
        el.style.verticalAlign = 'top';
        el.style.background = 'none';
        el.style.webkitTextFillColor = 'initial';

        // Sizing span to maintain exact geometric dimensions and screen-reader accessibility
        const sizer = document.createElement('span');
        sizer.className = 'canvas-text-sizer';
        sizer.textContent = text;
        sizer.style.visibility = 'hidden';
        sizer.style.display = 'inline-block';
        sizer.style.whiteSpace = 'pre-wrap';
        sizer.style.userSelect = 'none';
        sizer.setAttribute('aria-hidden', 'true');
        el.appendChild(sizer);

        // Render Canvas
        const canvas = document.createElement('canvas');
        canvas.className = 'canvas-text-canvas';
        canvas.style.position = 'absolute';
        canvas.style.top = '0';
        canvas.style.left = '0';
        canvas.style.pointerEvents = 'none';
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', text);
        el.appendChild(canvas);

        const colors = [
            'rgba(255, 255, 255, 0.75)',  // luminous shimmer
            'rgba(192, 132, 252, 0.9)',   // bright lavender purple
            'rgba(96, 165, 250, 0.9)',    // bright electric blue
            'rgba(216, 180, 254, 0.85)',  // soft violet
            'rgba(129, 140, 248, 0.9)',   // indigo
            'rgba(56, 189, 248, 0.85)',   // sky blue shimmer
            'rgba(168, 85, 247, 0.9)',    // deep vibrant purple
            'rgba(37, 99, 235, 0.9)'      // sapphire blue
        ];

        const animationDuration = 6; // seconds
        const lineWidth = 2.2;
        const lineGap = 4;
        const curveIntensity = 30;

        let startTime = performance.now();

        function renderFrame(currentTime) {
            // Check if element is still connected
            if (!el.isConnected) return;

            const rect = sizer.getBoundingClientRect();
            const width = Math.ceil(rect.width);
            const height = Math.ceil(rect.height);

            if (width === 0 || height === 0) {
                requestAnimationFrame(renderFrame);
                return;
            }

            const dpr = window.devicePixelRatio || 1;
            if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
                canvas.width = width * dpr;
                canvas.height = height * dpr;
                canvas.style.width = width + 'px';
                canvas.style.height = height + 'px';
            }

            const ctx = canvas.getContext('2d', { alpha: true });
            if (!ctx) return;

            const computed = window.getComputedStyle(el);
            const font = `${computed.fontWeight} ${computed.fontSize} ${computed.fontFamily}`;

            const elapsed = (currentTime - startTime) / 1000;
            const phase = (elapsed / animationDuration) * Math.PI * 2;

            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, width, height);

            // 1. Draw text mask
            ctx.globalCompositeOperation = 'source-over';
            ctx.font = font;
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillStyle = '#000';
            ctx.fillText(text, 0, height / 2);

            // 2. Fill background with luminous electric blue & purple gradient base
            ctx.globalCompositeOperation = 'source-in';
            const baseGrad = ctx.createLinearGradient(0, 0, width, height);
            baseGrad.addColorStop(0, '#a855f7');   // vibrant purple
            baseGrad.addColorStop(0.5, '#6366f1'); // indigo
            baseGrad.addColorStop(1, '#3b82f6');   // electric blue
            ctx.fillStyle = baseGrad;
            ctx.fillRect(0, 0, width, height);

            // 3. Draw moving wavy curves clipped inside text
            ctx.globalCompositeOperation = 'source-atop';
            const numLines = Math.floor(height / lineGap) + 12;

            for (let i = 0; i < numLines; i++) {
                const y = i * lineGap;
                const curve1 = Math.sin(phase + (i * 0.1)) * curveIntensity;
                const curve2 = Math.sin(phase + 0.6 + (i * 0.1)) * curveIntensity * 0.7;

                const colorIndex = i % colors.length;
                ctx.strokeStyle = colors[colorIndex];
                ctx.lineWidth = lineWidth;

                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.bezierCurveTo(
                    width * 0.33, y + curve1,
                    width * 0.66, y + curve2,
                    width, y
                );
                ctx.stroke();
            }

            requestAnimationFrame(renderFrame);
        }

        requestAnimationFrame(renderFrame);
    });
}
