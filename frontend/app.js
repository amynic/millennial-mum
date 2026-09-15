const chatContainer = document.getElementById('chatContainer');
const chatForm = document.getElementById('chatForm');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');
const newChatBtn = document.getElementById('newChatBtn');
const offlineBanner = document.getElementById('offlineBanner');
const srStatus = document.getElementById('srStatus');
const appEl = document.querySelector('.app');

// API endpoint — standalone Azure Functions proxy that holds Foundry auth
// server-side (client-credentials) and forwards to the hosted agent. It lives
// off the SWA (own origin) so long multi-agent replies aren't capped by the
// 45s Static Web Apps managed-functions gateway limit.
const API_ENDPOINT = 'https://millennial-mum-api-flex.azurewebsites.net/api/chat';

// Running transcript so the agent has multi-turn context. The client owns the
// history and sends it on every turn; persisted so closing/reopening the
// installed PWA keeps the conversation.
const HISTORY_KEY = 'mm-history-v1';
// The composer draft survives the app being backgrounded mid-sentence, which
// on a phone happens constantly.
const DRAFT_KEY = 'mm-draft-v1';
const MAX_TURNS = 24;
let history = loadHistory();

let isProcessing = false;

function loadHistory() {
    try {
        const raw = localStorage.getItem(HISTORY_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

function saveHistory() {
    try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-MAX_TURNS)));
    } catch {
        /* storage full / unavailable — non-fatal */
    }
}

// Re-render any persisted conversation on load (keep the welcome bubble only
// when there's no history yet).
function restoreHistory() {
    if (!history.length) return;
    const welcome = chatContainer.querySelector('.message.assistant');
    if (welcome) welcome.remove();
    for (const turn of history) {
        appendMessage(turn.role === 'assistant' ? 'assistant' : 'user', turn.content);
    }
}

chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = userInput.value.trim();
    if (!message || isProcessing) return;

    appendMessage('user', message);
    history.push({ role: 'user', content: message });
    saveHistory();
    userInput.value = '';
    clearDraft();
    autoGrow();

    await requestReply();
});

// Sends the current history and streams the reply in. Split out from the submit
// handler so a failed turn can be retried without re-typing or duplicating the
// user's message (it's already in `history`).
async function requestReply() {
    // The textarea is disabled while we wait, which drops focus to <body>, so
    // remember whether the keyboard should come back afterwards.
    const keepFocus = document.activeElement === userInput || document.activeElement === sendBtn;
    setProcessing(true);
    announce('Sending…');

    const typingEl = showTypingIndicator();

    try {
        if (!navigator.onLine) {
            throw new Error('offline');
        }

        const response = await fetch(API_ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: history.slice(-MAX_TURNS) }),
        });

        if (!response.ok || !response.body) {
            throw new Error(`Server error: ${response.status}`);
        }

        // Stream the reply: append text to a single assistant bubble as chunks
        // arrive so the answer renders progressively instead of after the full
        // 28-80s wait.
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let reply = '';
        let messageEl = null;

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, { stream: true });
            if (!chunk) continue;
            if (!messageEl) {
                removeTypingIndicator(typingEl);
                messageEl = appendMessage('assistant', '');
            }
            reply += chunk;
            updateMessage(messageEl, reply);
        }

        removeTypingIndicator(typingEl);
        reply = reply.trim();
        if (!reply) {
            if (!messageEl) messageEl = appendMessage('assistant', '');
            reply = 'No response received.';
            updateMessage(messageEl, reply);
        }
        history.push({ role: 'assistant', content: reply });
        saveHistory();
        announce(reply);
    } catch (error) {
        removeTypingIndicator(typingEl);
        const offline = !navigator.onLine || error.message === 'offline';
        // A fetch that rejects with TypeError never reached the server: DNS,
        // dropped connection, or a blocked CORS preflight. That's a different
        // problem from the server answering with an error, so say so instead of
        // collapsing both into one vague message.
        const unreachable = !offline && error instanceof TypeError;
        let text;
        if (offline) {
            text = "📡 You're offline, so I couldn't send that. Reconnect and tap Retry.";
        } else if (unreachable) {
            text = "🔌 I couldn't reach the server. Check your connection and tap Retry.";
        } else {
            text = '⚠️ Sorry, something went wrong. Tap Retry to try again.';
        }
        appendError(text);
        announce(text);
        console.error('Chat error:', error);
    } finally {
        setProcessing(false);
        refocusComposer(keepFocus);
    }
}

if (newChatBtn) {
    newChatBtn.addEventListener('click', () => {
        if (isProcessing) return;
        history = [];
        saveHistory();
        clearDraft();
        userInput.value = '';
        autoGrow();
        chatContainer.innerHTML = '';
        appendMessage(
            'assistant',
            "👋 Fresh start! What do you need help with — meals, the schedule, an email, or a health worry?"
        );
        refocusComposer();
    });
}

function extractReply(data) {
    // Try structured output first
    const output = data.output || [];
    for (const item of output) {
        if (item.type === 'message' && item.role === 'assistant') {
            const content = item.content || [];
            for (const block of content) {
                if (block.type === 'output_text') return block.text;
            }
        }
    }
    // Fallback to output_text
    if (typeof data.output_text === 'string') return data.output_text;
    return "Sorry, I couldn't generate a response.";
}

function appendMessage(role, content) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = formatContent(content);

    messageDiv.appendChild(contentDiv);
    chatContainer.appendChild(messageDiv);
    scrollToLatest(true);
    return messageDiv;
}

// Error bubble with a retry affordance — on a phone the usual cause is a dead
// signal, not a real failure, so the user shouldn't have to retype anything.
function appendError(text) {
    const messageDiv = appendMessage('assistant', text);
    const contentDiv = messageDiv.querySelector('.message-content');
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'retry-btn';
    retry.textContent = 'Retry';
    retry.addEventListener('click', () => {
        if (isProcessing) return;
        messageDiv.remove();
        requestReply();
    });
    contentDiv.appendChild(retry);
    scrollToLatest(true);
    return messageDiv;
}

// Re-render an existing bubble's content (used while streaming a reply in).
function updateMessage(messageDiv, content) {
    const contentDiv = messageDiv.querySelector('.message-content');
    if (contentDiv) contentDiv.innerHTML = formatContent(content);
    // Don't yank the view back down if the user has scrolled up to re-read
    // something while the reply streams in.
    scrollToLatest(false);
}

function isNearBottom() {
    const { scrollTop, scrollHeight, clientHeight } = chatContainer;
    return scrollHeight - scrollTop - clientHeight < 80;
}

function scrollToLatest(force) {
    if (!force && !isNearBottom()) return;
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

// Streamed text would spam a screen reader chunk by chunk, so announce
// discrete status changes and the finished reply instead.
function announce(text) {
    if (srStatus) srStatus.textContent = text;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatContent(text) {
    // Escape HTML entities first to prevent XSS
    let html = escapeHtml(text)
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        .replace(/`(.*?)`/g, '<code>$1</code>');

    // Convert bullet points
    const lines = html.split('\n');
    let result = '';
    let inList = false;

    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('- ') || trimmed.startsWith('• ')) {
            if (!inList) { result += '<ul>'; inList = true; }
            result += `<li>${trimmed.slice(2)}</li>`;
        } else {
            if (inList) { result += '</ul>'; inList = false; }
            if (trimmed.startsWith('## ')) {
                result += `<h3>${trimmed.slice(3)}</h3>`;
            } else if (trimmed.startsWith('# ')) {
                result += `<h2>${trimmed.slice(2)}</h2>`;
            } else if (trimmed) {
                result += `<p>${trimmed}</p>`;
            }
        }
    }
    if (inList) result += '</ul>';

    return result || `<p>${text}</p>`;
}

function showTypingIndicator() {
    const el = document.createElement('div');
    el.className = 'typing-indicator';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '<span></span><span></span><span></span>';
    chatContainer.appendChild(el);
    scrollToLatest(true);
    return el;
}

function removeTypingIndicator(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
}

function setProcessing(state) {
    isProcessing = state;
    sendBtn.disabled = state;
    userInput.disabled = state;
    if (newChatBtn) newChatBtn.disabled = state;
}

/* ---------------------------------------------------------------------------
 * Mobile ergonomics
 * ------------------------------------------------------------------------- */

// Keep the app box matched to the *visual* viewport so the composer stays above
// the on-screen keyboard rather than being pushed off-screen behind it.
function setupViewportFit() {
    const vv = window.visualViewport;
    if (!vv || !appEl) return;

    appEl.classList.add('kb-aware');

    const apply = () => {
        document.documentElement.style.setProperty('--app-height', `${vv.height}px`);
        // iOS offsets the visual viewport when the keyboard opens; reset the
        // layout scroll so the pinned body stays aligned.
        window.scrollTo(0, 0);
        scrollToLatest(false);
    };

    apply();
    vv.addEventListener('resize', apply);
    vv.addEventListener('scroll', apply);
}

// Grow the composer with the message (to a capped height) instead of forcing
// long requests through a one-line box.
function autoGrow() {
    userInput.style.height = 'auto';
    userInput.style.height = `${userInput.scrollHeight}px`;
}

function loadDraft() {
    try {
        return localStorage.getItem(DRAFT_KEY) || '';
    } catch {
        return '';
    }
}

function saveDraft(value) {
    try {
        if (value) localStorage.setItem(DRAFT_KEY, value);
        else localStorage.removeItem(DRAFT_KEY);
    } catch {
        /* storage full / unavailable — non-fatal */
    }
}

function clearDraft() {
    saveDraft('');
}

// Only pull the keyboard back up if the composer already had focus; never steal
// focus on first load, which would cover the welcome message.
function refocusComposer(force) {
    if (!force && document.activeElement === document.body) return;
    userInput.focus();
}

function updateOnlineState() {
    if (!offlineBanner) return;
    offlineBanner.hidden = navigator.onLine;
}

userInput.addEventListener('input', () => {
    autoGrow();
    saveDraft(userInput.value);
});

// Enter sends; Shift+Enter (and mobile "return" on a wrapped line) inserts a
// newline. Matches `enterkeyhint="send"` on the textarea.
userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        chatForm.requestSubmit();
    }
});

// Tapping the composer while the keyboard animates in can leave the newest
// message hidden; nudge it back into view once things settle.
userInput.addEventListener('focus', () => {
    setTimeout(() => scrollToLatest(true), 250);
});

window.addEventListener('online', () => {
    updateOnlineState();
    announce('Back online.');
});
window.addEventListener('offline', () => {
    updateOnlineState();
    announce("You're offline.");
});

// Register the service worker so the app is installable + works offline (PWA).
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch((err) => {
            console.warn('Service worker registration failed:', err);
        });
    });
}

// Manifest shortcuts launch with query params; honour them then strip them so a
// later refresh doesn't repeat the action.
function applyLaunchParams() {
    let params;
    try {
        params = new URL(window.location.href).searchParams;
    } catch {
        return null;
    }

    const startNew = params.get('new') === '1';
    const prefill = params.get('q');
    if (!startNew && !prefill) return null;

    if (startNew) {
        history = [];
        saveHistory();
        clearDraft();
    }

    if (window.history.replaceState) {
        window.history.replaceState({}, '', window.location.pathname);
    }
    return prefill;
}

setupViewportFit();
updateOnlineState();
const prefill = applyLaunchParams();
userInput.value = prefill || loadDraft();
autoGrow();
restoreHistory();
scrollToLatest(true);
