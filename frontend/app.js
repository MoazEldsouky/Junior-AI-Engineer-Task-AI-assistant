const API_BASE_URL = 'http://127.0.0.1:8000';
let currentSessionId = null;
let isProcessing = false;

// DOM Elements
const chatMessages = document.getElementById('chat-messages');
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const sessionIdDisplay = document.getElementById('session-id-display');
const newChatBtn = document.getElementById('new-chat-btn');
const exampleItems = document.querySelectorAll('.example-item');

const latencyIndicator = document.getElementById('latency-indicator');
const latencyText = document.getElementById('latency-text');

// ─── Initialize session on load ───
document.addEventListener('DOMContentLoaded', async () => {
    const savedSessionId = sessionStorage.getItem('sessionId');
    if (savedSessionId) {
        currentSessionId = savedSessionId;
        updateSessionId(savedSessionId);
        await loadHistory(savedSessionId);
    }
});

// ─── Utilities ───
function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function updateSessionId(id) {
    currentSessionId = id;
    sessionIdDisplay.textContent = id ? id.substring(0, 8) + '...' : 'None';
    if (id) {
        sessionStorage.setItem('sessionId', id);
    } else {
        sessionStorage.removeItem('sessionId');
    }
}

// ─── Latency indicator (static bottom-left) ───
let latencyTimer = null;

function showLatencyProcessing() {
    latencyIndicator.classList.remove('hidden');
    latencyIndicator.classList.add('processing');
    const startTime = Date.now();
    latencyText.textContent = 'processing | 0.0s';

    if (latencyTimer) clearInterval(latencyTimer);
    latencyTimer = setInterval(() => {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        latencyText.textContent = `processing | ${elapsed}s`;
    }, 100);
}

function showLatencyTTFT(ttftMs) {
    if (latencyTimer) { clearInterval(latencyTimer); latencyTimer = null; }
    latencyIndicator.classList.remove('processing');
    latencyIndicator.classList.remove('hidden');
    const seconds = (ttftMs / 1000).toFixed(2);
    latencyText.textContent = `TTFT: ${seconds}s`;
}

function hideLatency() {
    if (latencyTimer) { clearInterval(latencyTimer); latencyTimer = null; }
    // Keep visible showing last TTFT — only hide on new chat
}

// ─── Simple Markdown → HTML renderer ───
function renderMarkdown(text) {
    if (!text) return '';
    let html = text;

    // Escape HTML entities
    html = html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

    // Code blocks (``` ... ```)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        return `<pre><code>${code.trim()}</code></pre>`;
    });

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

    // Bold + Italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic
    html = html.replace(/(?<!\*)\*([^*]+?)\*(?!\*)/g, '<em>$1</em>');

    // Horizontal rule
    html = html.replace(/^---$/gm, '<hr>');

    // ── Markdown tables ──
    // Detect blocks of consecutive pipe-delimited lines
    html = html.replace(
        /((?:^\|.+\|[ \t]*$\n?){2,})/gm,
        (tableBlock) => {
            const rows = tableBlock.trim().split('\n').filter(r => r.trim());
            if (rows.length < 2) return tableBlock;

            // Check if row 2 is a separator (e.g. |---|---|)
            const isSeparator = /^\|[\s\-:|]+\|$/.test(rows[1].trim());
            if (!isSeparator) return tableBlock;

            // Parse header
            const headerCells = rows[0].split('|').filter(c => c.trim() !== '');
            let tableHtml = '<table><thead><tr>';
            headerCells.forEach(cell => {
                tableHtml += `<th>${cell.trim()}</th>`;
            });
            tableHtml += '</tr></thead><tbody>';

            // Parse body rows (skip row 0 = header, row 1 = separator)
            for (let i = 2; i < rows.length; i++) {
                const cells = rows[i].split('|').filter(c => c.trim() !== '');
                tableHtml += '<tr>';
                cells.forEach(cell => {
                    tableHtml += `<td>${cell.trim()}</td>`;
                });
                tableHtml += '</tr>';
            }
            tableHtml += '</tbody></table>';
            return `<div class="table-scroll-wrapper">${tableHtml}</div>`;
        }
    );

    // Unordered lists (lines starting with - )
    html = html.replace(/^(\s*)[-*] (.+)$/gm, (match, indent, content) => {
        return `<li>${content}</li>`;
    });
    // Wrap consecutive <li> tags in <ul>
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');

    // Ordered lists (lines starting with number.)
    html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');

    // Paragraphs: wrap remaining loose lines
    html = html.replace(/^(?!<[hluopt]|<\/|<li|<hr|<pre|<code|<table|<thead|<tbody|<tr|<td|<th)(.+)$/gm, '<p>$1</p>');

    // Clean up extra newlines
    html = html.replace(/\n{2,}/g, '\n');
    html = html.replace(/\n/g, '');

    return html;
}

// ─── Create a thinking block element ───
function createThinkingBlock() {
    const wrapper = document.createElement('div');
    wrapper.className = 'message assistant-message';

    const block = document.createElement('div');
    block.className = 'thinking-block';

    const header = document.createElement('div');
    header.className = 'thinking-header';
    header.innerHTML = `
        <div class="thinking-spinner"></div>
        <span class="thinking-label">Thinking...</span>
    `;

    const body = document.createElement('div');
    body.className = 'thinking-body expanded';

    const content = document.createElement('div');
    content.className = 'thinking-content';

    body.appendChild(content);
    block.appendChild(header);
    block.appendChild(body);
    wrapper.appendChild(block);

    // Toggle expand/collapse
    header.addEventListener('click', () => {
        body.classList.toggle('expanded');
        const icon = header.querySelector('.thinking-icon');
        if (icon) icon.classList.toggle('open');
    });

    return { wrapper, block, header, body, content };
}

// ─── Add a thinking step to the thinking block ───
function addThinkingStep(contentEl, stepData) {
    const stepDiv = document.createElement('div');
    stepDiv.className = 'thinking-step';

    if (stepData.thought) {
        const label = document.createElement('div');
        label.className = 'thinking-step-label';
        label.textContent = 'Thought';
        const text = document.createElement('div');
        text.className = 'thinking-step-text';
        text.textContent = stepData.thought;
        stepDiv.appendChild(label);
        stepDiv.appendChild(text);
    }

    if (stepData.action) {
        const label = document.createElement('div');
        label.className = 'thinking-step-label';
        label.textContent = 'Action';
        const text = document.createElement('div');
        text.className = 'thinking-step-text';
        text.textContent = stepData.action;
        stepDiv.appendChild(label);
        stepDiv.appendChild(text);
    }

    if (stepData.observation) {
        const label = document.createElement('div');
        label.className = 'thinking-step-label';
        label.textContent = 'Observation';
        const text = document.createElement('div');
        text.className = 'thinking-step-text';
        const obs = stepData.observation.length > 300
            ? stepData.observation.substring(0, 300) + '...'
            : stepData.observation;
        text.textContent = obs;
        stepDiv.appendChild(label);
        stepDiv.appendChild(text);
    }

    contentEl.appendChild(stepDiv);
}

// ─── Finalize thinking block (replace spinner with chevron) ───
function finalizeThinkingBlock(thinkingElements, stepCount) {
    const { header, body } = thinkingElements;
    body.classList.remove('expanded');

    const chevronSvg = `<svg class="thinking-icon" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none"><polyline points="9 18 15 12 9 6"></polyline></svg>`;
    header.innerHTML = `
        ${chevronSvg}
        <span class="thinking-label">Thought for ${stepCount} step${stepCount !== 1 ? 's' : ''}</span>
    `;
}

// ─── Append a simple message (for user messages and history) ───
function appendMessage(role, content) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}-message`;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    if (role === 'assistant') {
        contentDiv.innerHTML = renderMarkdown(content);
    } else {
        let escaped = content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        escaped = escaped.replace(/\n/g, '<br>');
        contentDiv.innerHTML = escaped;
    }

    msgDiv.appendChild(contentDiv);
    chatMessages.appendChild(msgDiv);
    scrollToBottom();
    return msgDiv;
}

// ─── Create the assistant message container for streaming ───
function createStreamingAssistantMessage() {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant-message';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    msgDiv.appendChild(contentDiv);
    chatMessages.appendChild(msgDiv);
    scrollToBottom();

    return { msgDiv, contentDiv };
}


// ─── Load history from API ───
async function loadHistory(sessionId) {
    try {
        const res = await fetch(`${API_BASE_URL}/sessions/${sessionId}/history`);
        if (!res.ok) throw new Error('Failed to load history');
        const data = await res.json();

        chatMessages.innerHTML = '';

        if (data.history && data.history.length > 0) {
            data.history.forEach(msg => {
                appendMessage(msg.role, msg.content);
            });
        }
    } catch (error) {
        console.error('Error loading history:', error);
        chatMessages.innerHTML = '';
    }
}

// ─── Parse SSE events from a text buffer ───
function parseSSEBuffer(buffer) {
    const events = [];
    const blocks = buffer.split('\n\n');
    const remaining = blocks.pop() || '';

    for (const block of blocks) {
        if (!block.trim()) continue;
        const lines = block.split('\n');
        let eventType = null;
        let dataStr = null;

        for (const line of lines) {
            if (line.startsWith('event: ')) {
                eventType = line.substring(7).trim();
            } else if (line.startsWith('data: ')) {
                dataStr = line.substring(6);
            }
        }

        if (eventType && dataStr !== null) {
            try {
                events.push({ type: eventType, data: JSON.parse(dataStr) });
            } catch (e) {
                console.warn('Failed to parse SSE data:', dataStr, e);
            }
        }
    }

    return { events, remaining };
}

// ─── Send message via SSE streaming endpoint ───
async function sendMessage(text) {
    if (!text.trim() || isProcessing) return;
    isProcessing = true;

    appendMessage('user', text);
    chatInput.value = '';
    chatInput.style.height = 'auto';  // Reset textarea height after send
    showLatencyProcessing();

    let thinkingElements = null;
    let streamingMsg = null;
    let accumulatedText = '';
    let stepCount = 0;
    let ttftRecorded = false;
    const requestStartTime = Date.now();

    try {
        const payload = { message: text };
        if (currentSessionId) {
            payload.session_id = currentSessionId;
        }

        const res = await fetch(`${API_BASE_URL}/chat/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!res.ok) throw new Error(`API request failed with status ${res.status}`);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const parsed = parseSSEBuffer(buffer);
            buffer = parsed.remaining;

            for (const evt of parsed.events) {
                if (evt.type === 'session_id') {
                    updateSessionId(evt.data.session_id);
                }

                else if (evt.type === 'thinking') {
                    if (!thinkingElements) {
                        thinkingElements = createThinkingBlock();
                        chatMessages.appendChild(thinkingElements.wrapper);
                        scrollToBottom();
                    }
                    stepCount++;
                    addThinkingStep(thinkingElements.content, evt.data);
                    scrollToBottom();
                }

                else if (evt.type === 'thinking_end') {
                    if (thinkingElements && stepCount > 0) {
                        finalizeThinkingBlock(thinkingElements, stepCount);
                    }
                }

                else if (evt.type === 'token') {
                    // Record TTFT on the very first token
                    if (!ttftRecorded) {
                        const ttftMs = Date.now() - requestStartTime;
                        showLatencyTTFT(ttftMs);
                        ttftRecorded = true;
                    }

                    if (!streamingMsg) {
                        streamingMsg = createStreamingAssistantMessage();
                    }
                    accumulatedText += evt.data.token;
                    streamingMsg.contentDiv.innerHTML = renderMarkdown(accumulatedText);
                    scrollToBottom();
                }

                else if (evt.type === 'done') {
                    if (streamingMsg) {
                        streamingMsg.contentDiv.innerHTML = renderMarkdown(accumulatedText);
                    }

                    // If no tokens were received (e.g. error path), record TTFT now
                    if (!ttftRecorded) {
                        const ttftMs = Date.now() - requestStartTime;
                        showLatencyTTFT(ttftMs);
                    }

                    scrollToBottom();
                }

                else if (evt.type === 'error') {
                    if (!ttftRecorded) {
                        const ttftMs = Date.now() - requestStartTime;
                        showLatencyTTFT(ttftMs);
                    }
                    appendMessage('assistant', `Error: ${evt.data.message}`);
                }
            }
        }

        // Process any remaining buffer
        if (buffer.trim()) {
            parseSSEBuffer(buffer + '\n\n');
        }
    } catch (error) {
        showLatencyTTFT(Date.now() - requestStartTime);
        appendMessage('assistant', `Error: Could not connect to API (${error.message}). Is the server running?`);
    }

    isProcessing = false;
}

// ─── Textarea auto-resize ───
function autoResizeTextarea() {
    chatInput.style.height = 'auto';               // Shrink first to measure
    chatInput.style.height = chatInput.scrollHeight + 'px';  // Expand to content
}

chatInput.addEventListener('input', autoResizeTextarea);

// ─── Keyboard: Enter sends, Shift+Enter inserts newline ───
chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        chatForm.dispatchEvent(new Event('submit', { cancelable: true }));
    }
});

// ─── Event Listeners ───
chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    sendMessage(chatInput.value);
});

exampleItems.forEach(item => {
    item.addEventListener('click', () => {
        sendMessage(item.textContent);
    });
});

newChatBtn.addEventListener('click', () => {
    currentSessionId = null;
    updateSessionId(null);
    chatMessages.innerHTML = '';
    latencyIndicator.classList.add('hidden');
});
