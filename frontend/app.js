const chatContainer = document.getElementById('chatContainer');
const chatForm = document.getElementById('chatForm');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');

// API proxy endpoint — SWA managed function handles Foundry auth
const API_ENDPOINT = '/api/chat';

let isProcessing = false;

chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = userInput.value.trim();
    if (!message || isProcessing) return;

    appendMessage('user', message);
    userInput.value = '';
    setProcessing(true);

    const typingEl = showTypingIndicator();

    try {
        const response = await fetch(API_ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message }),
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.status}`);
        }

        const data = await response.json();
        removeTypingIndicator(typingEl);

        appendMessage('assistant', data.reply || 'No response received.');
    } catch (error) {
        removeTypingIndicator(typingEl);
        appendMessage('assistant', '⚠️ Sorry, something went wrong. Please try again.');
        console.error('Chat error:', error);
    } finally {
        setProcessing(false);
        userInput.focus();
    }
});

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
}

function formatContent(text) {
    // Convert markdown-like formatting to HTML
    let html = text
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
