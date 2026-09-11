const chatContainer = document.getElementById('chatContainer');
const chatForm = document.getElementById('chatForm');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');
const newChatBtn = document.getElementById('newChatBtn');

// API endpoint — standalone Azure Functions proxy that holds Foundry auth
// server-side (client-credentials) and forwards to the hosted agent. It lives
// off the SWA (own origin) so long multi-agent replies aren't capped by the
// 45s Static Web Apps managed-functions gateway limit.
const API_ENDPOINT = 'https://millennial-mum-api-flex.azurewebsites.net/api/chat';

// Running transcript so the agent has multi-turn context. The client owns the
// history and sends it on every turn; persisted so closing/reopening the
// installed PWA keeps the conversation.
const HISTORY_KEY = 'mm-history-v1';
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
    setProcessing(true);

    const typingEl = showTypingIndicator();

    try {
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
    } catch (error) {
        removeTypingIndicator(typingEl);
        appendMessage('assistant', '⚠️ Sorry, something went wrong. Please try again.');
        console.error('Chat error:', error);
    } finally {
        setProcessing(false);
        userInput.focus();
    }
});

if (newChatBtn) {
    newChatBtn.addEventListener('click', () => {
        if (isProcessing) return;
        history = [];
        saveHistory();
        chatContainer.innerHTML = '';
        appendMessage(
            'assistant',
            "👋 Fresh start! What do you need help with — meals, the schedule, an email, or a health worry?"
        );
        userInput.focus();
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
    chatContainer.scrollTop = chatContainer.scrollHeight;
    return messageDiv;
}

// Re-render an existing bubble's content (used while streaming a reply in).
function updateMessage(messageDiv, content) {
    const contentDiv = messageDiv.querySelector('.message-content');
    if (contentDiv) contentDiv.innerHTML = formatContent(content);
    chatContainer.scrollTop = chatContainer.scrollHeight;
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
    el.innerHTML = '<span></span><span></span><span></span>';
    chatContainer.appendChild(el);
    chatContainer.scrollTop = chatContainer.scrollHeight;
    return el;
}

function removeTypingIndicator(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
}

function setProcessing(state) {
    isProcessing = state;
    sendBtn.disabled = state;
    userInput.disabled = state;
}

// Register the service worker so the app is installable + works offline (PWA).
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch((err) => {
            console.warn('Service worker registration failed:', err);
        });
    });
}

restoreHistory();
