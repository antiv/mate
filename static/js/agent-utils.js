/**
 * Agent Utilities Module
 * Monaco editor integration, hierarchy management, filtering, and modal controls
 */

// ============================================================================
// Monaco Editor Integration
// ============================================================================

// Monaco Editor instances
let monacoEditors = {};

/**
 * Initialize Monaco Editor
 */
function initMonacoEditor(containerId, initialValue = '') {
    return new Promise((resolve) => {
        require.config({ paths: { 'vs': 'https://cdn.jsdelivr.net/npm/monaco-editor@0.44.0/min/vs' }});
        require(['vs/editor/editor.main'], function () {
            const editor = monaco.editor.create(document.getElementById(containerId), {
                value: initialValue,
                language: 'json',
                theme: document.documentElement.classList.contains('dark') ? 'vs-dark' : 'vs',
                automaticLayout: true,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                wordWrap: 'on',
                formatOnPaste: true,
                formatOnType: true,
                suggestOnTriggerCharacters: true,
                acceptSuggestionOnEnter: 'on',
                tabCompletion: 'on',
                wordBasedSuggestions: 'off',
                folding: true,
                lineNumbers: 'on',
                renderLineHighlight: 'line',
                selectOnLineNumbers: true,
                roundedSelection: false,
                readOnly: false,
                cursorStyle: 'line',
                fontSize: 14,
                lineHeight: 20
            });
            
            // Add JSON validation
            monaco.languages.json.jsonDefaults.setDiagnosticsOptions({
                validate: true,
                allowComments: false,
                schemas: []
            });
            
            resolve(editor);
        });
    });
}

/**
 * Get JSON from Monaco editor
 */
function getJsonFromEditor(editor) {
    if (!editor) return '';
    return editor.getValue();
}

/**
 * Set JSON in Monaco editor
 */
function setJsonInEditor(editor, value) {
    if (!editor) return;
    editor.setValue(value);
}

/**
 * Toggle between textarea and Monaco editor
 */
function toggleJsonEditor(textareaId, editorId) {
    const textarea = document.getElementById(textareaId);
    const editorContainer = document.getElementById(editorId);
    
    if (editorContainer.style.display === 'none') {
        // Show Monaco editor
        textarea.style.display = 'none';
        editorContainer.style.display = 'block';
        
        if (!monacoEditors[editorId]) {
            initMonacoEditor(editorId, textarea.value).then(editor => {
                monacoEditors[editorId] = editor;
            });
        }
    } else {
        // Show textarea
        editorContainer.style.display = 'none';
        textarea.style.display = 'block';
        
        if (monacoEditors[editorId]) {
            textarea.value = getJsonFromEditor(monacoEditors[editorId]);
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }
}

// ============================================================================
// Modal Controls
// ============================================================================

function showCreateAgentModal() {
    if (typeof window.selectedProjectId === 'undefined' || window.selectedProjectId === null) {
        if (typeof showNotification === 'function') {
            showNotification('Select a project before creating agents.', 'warning');
        } else {
            alert('Select a project before creating agents.');
        }
        return;
    }
    
    const projectSelect = document.getElementById('agentProject');
    if (projectSelect) {
        projectSelect.value = String(window.selectedProjectId);
    }
    
    document.getElementById('createAgentModal').classList.remove('hidden');
}

function hideCreateAgentModal() {
    document.getElementById('createAgentModal').classList.add('hidden');
    document.getElementById('createAgentForm').reset();
    if (typeof updateConfigSummaries === 'function') {
        updateConfigSummaries('agent');
    }
}

function showEditAgentModal() {
    document.getElementById('editAgentModal').classList.remove('hidden');
}

function hideEditAgentModal() {
    console.log('hideEditAgentModal called');
    console.log('editingFromGraphEditor:', window.editingFromGraphEditor);
    console.log('isSubmittingEditForm:', window.isSubmittingEditForm);
    
    document.getElementById('editAgentModal').classList.add('hidden');
    document.getElementById('editAgentForm').reset();
    if (typeof updateConfigSummaries === 'function') {
        updateConfigSummaries('editAgent');
    }
    
    // Clear graph editor flags if modal is closed without saving
    // But only if we're not in the middle of a form submission
    if (window.editingFromGraphEditor && !window.isSubmittingEditForm) {
        console.log('Edit modal closed, clearing graph editor flags');
        window.editingFromGraphEditor = false;
        window.graphEditorInstance = null;
    } else if (window.editingFromGraphEditor && window.isSubmittingEditForm) {
        console.log('Edit modal closed during form submission, keeping graph editor flags');
    }
}


function showCopyAgentModal() {
    document.getElementById('copyAgentModal').classList.remove('hidden');
}

function hideCopyAgentModal() {
    document.getElementById('copyAgentModal').classList.add('hidden');
    document.getElementById('copyAgentForm').reset();
    if (typeof updateConfigSummaries === 'function') {
        updateConfigSummaries('copyAgent');
    }
}

const CHAT_USER_STORAGE_KEY = 'agentChatSelectedUserId';
let chatUsersCache = null;
let chatUserFetchPromise = null;
let pendingChatAgentName = null;
let currentChatAgentName = null;
let currentChatUserId = '';
let chatUserModalSelectionId = '';

const chatUserAutocompleteState = {
    filteredUsers: [],
    activeIndex: -1
};

const chatHeaderAutocompleteState = {
    filteredUsers: [],
    activeIndex: -1
};

function getStoredChatUserId() {
    try {
        return localStorage.getItem(CHAT_USER_STORAGE_KEY) || '';
    } catch (error) {
        return '';
    }
}

function setStoredChatUserId(userId) {
    try {
        if (userId) {
            localStorage.setItem(CHAT_USER_STORAGE_KEY, userId);
        } else {
            localStorage.removeItem(CHAT_USER_STORAGE_KEY);
        }
    } catch (error) {
        // Ignore storage errors
    }
}

function buildChatUrl(agentName, userId) {
    const baseUrl = window.location.origin || '';
    let chatUrl = `${baseUrl}/dev-ui/?app=${encodeURIComponent(agentName)}`;
    if (userId) {
        chatUrl += `&userId=${encodeURIComponent(userId)}`;
    }
    return chatUrl;
}

async function loadChatUsers() {
    if (Array.isArray(chatUsersCache) && chatUsersCache.length > 0) {
        return chatUsersCache;
    }

    if (chatUserFetchPromise) {
        return chatUserFetchPromise;
    }

    chatUserFetchPromise = fetch('/dashboard/api/users', { credentials: 'same-origin' })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Failed to fetch users: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            const users = Array.isArray(data?.users) ? data.users : [];
            chatUsersCache = users;
            return users;
        })
        .catch(error => {
            chatUsersCache = null;
            throw error;
        })
        .finally(() => {
            chatUserFetchPromise = null;
        });

    return chatUserFetchPromise;
}

function updateChatIframeDestination(iframe, newTabBtn, chatUrl) {
    if (iframe) {
        iframe.src = chatUrl;
    }

    if (newTabBtn) {
        newTabBtn.onclick = (event) => {
            event?.preventDefault();
            const openedWindow = window.open(chatUrl, '_blank', 'noopener,noreferrer');
            if (openedWindow && typeof openedWindow.focus === 'function') {
                openedWindow.focus();
            }
            hideAgentChatPanel();
        };
        newTabBtn.disabled = false;
        newTabBtn.classList.remove('opacity-50', 'cursor-not-allowed');
    }
}

function hideChatUserSuggestions() {
    const suggestions = document.getElementById('chatUserSelectSuggestions');
    if (suggestions) {
        suggestions.classList.add('hidden');
        suggestions.innerHTML = '';
    }
    chatUserAutocompleteState.filteredUsers = [];
    chatUserAutocompleteState.activeIndex = -1;
}

function updateChatUserSuggestionActiveState() {
    const suggestions = document.getElementById('chatUserSelectSuggestions');
    if (!suggestions) {
        return;
    }

    Array.from(suggestions.querySelectorAll('[data-user-id]')).forEach((element, index) => {
        if (index === chatUserAutocompleteState.activeIndex) {
            element.classList.add('bg-blue-100', 'dark:bg-blue-900/60', 'text-blue-900', 'dark:text-blue-100');
        } else {
            element.classList.remove('bg-blue-100', 'dark:bg-blue-900/60', 'text-blue-900', 'dark:text-blue-100');
        }
    });
}

function renderChatUserSuggestions(query = '', forceOpen = false) {
    const suggestions = document.getElementById('chatUserSelectSuggestions');
    if (!suggestions) {
        return;
    }

    const trimmed = (query || '').trim().toLowerCase();
    const users = Array.isArray(chatUsersCache) ? chatUsersCache : [];

    let matches = users;
    if (trimmed) {
        matches = users.filter(user => user?.user_id?.toLowerCase().includes(trimmed));
    }

    chatUserAutocompleteState.filteredUsers = matches.slice(0, 25);
    chatUserAutocompleteState.activeIndex = chatUserAutocompleteState.filteredUsers.length > 0 ? 0 : -1;

    suggestions.innerHTML = '';

    if (chatUserAutocompleteState.filteredUsers.length === 0) {
        if (forceOpen) {
            const emptyItem = document.createElement('div');
            emptyItem.className = 'px-3 py-2 text-xs text-gray-500 dark:text-gray-400';
            emptyItem.textContent = trimmed ? 'No matching users found.' : 'No users available.';
            suggestions.appendChild(emptyItem);
            suggestions.classList.remove('hidden');
        } else {
            suggestions.classList.add('hidden');
        }
        return;
    }

    chatUserAutocompleteState.filteredUsers.forEach((user, index) => {
        if (!user?.user_id) {
            return;
        }
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.userId = user.user_id;
        button.className = 'w-full text-left px-3 py-2 text-xs text-gray-700 dark:text-gray-200 hover:bg-blue-50 dark:hover:bg-blue-900/40 focus:outline-none';
        button.textContent = user.user_id;
        button.addEventListener('mousedown', event => {
            event.preventDefault();
        });
        button.addEventListener('click', () => {
            selectChatUserFromSuggestion(user.user_id);
        });
        button.addEventListener('mouseenter', () => {
            chatUserAutocompleteState.activeIndex = index;
            updateChatUserSuggestionActiveState();
        });
        suggestions.appendChild(button);
    });

    suggestions.classList.remove('hidden');
    updateChatUserSuggestionActiveState();
}

function moveChatUserSuggestion(direction) {
    const total = chatUserAutocompleteState.filteredUsers.length;
    if (total === 0) {
        return;
    }

    let nextIndex = chatUserAutocompleteState.activeIndex + direction;
    if (nextIndex < 0) {
        nextIndex = total - 1;
    } else if (nextIndex >= total) {
        nextIndex = 0;
    }
    chatUserAutocompleteState.activeIndex = nextIndex;
    updateChatUserSuggestionActiveState();
}

function setChatUserModalSelection(userId, options = {}) {
    const input = document.getElementById('chatUserSelectInput');
    chatUserModalSelectionId = userId || '';

    if (input && options.updateInput !== false) {
        input.value = chatUserModalSelectionId;
    }

    updateChatUserConfirmState();
    syncChatUserInputValidation();
}

function selectChatUserFromSuggestion(userId) {
    setChatUserModalSelection(userId);
    hideChatUserSuggestions();
}

function syncChatUserInputValidation() {
    const error = document.getElementById('chatUserSelectError');
    const input = document.getElementById('chatUserSelectInput');

    if (!error || !input) {
        return;
    }

    const hasValue = !!input.value.trim();
    if (!hasValue) {
        error.textContent = '';
        error.classList.add('hidden');
        return;
    }

    if (chatUserModalSelectionId) {
        error.textContent = '';
        error.classList.add('hidden');
    } else {
        error.textContent = 'Select a user from the suggestions list.';
        error.classList.remove('hidden');
    }
}

function resetChatUserModalState({ clearInput = true } = {}) {
    if (clearInput) {
        const input = document.getElementById('chatUserSelectInput');
        if (input) {
            input.value = '';
        }
    }
    chatUserModalSelectionId = '';
    chatUserAutocompleteState.filteredUsers = [];
    chatUserAutocompleteState.activeIndex = -1;
    hideChatUserSuggestions();
    updateChatUserConfirmState();
    syncChatUserInputValidation();
}

function resetChatHeaderAutocompleteState({ clearInput = false } = {}) {
    if (clearInput) {
        const input = document.getElementById('agentChatUserInput');
        if (input) {
            input.value = '';
        }
    }
    chatHeaderAutocompleteState.filteredUsers = [];
    chatHeaderAutocompleteState.activeIndex = -1;
    hideChatHeaderSuggestions();
}

function hideChatHeaderSuggestions() {
    const suggestions = document.getElementById('agentChatUserSuggestions');
    if (suggestions) {
        suggestions.classList.add('hidden');
        suggestions.innerHTML = '';
    }
}

function updateChatHeaderSuggestionActiveState() {
    const suggestions = document.getElementById('agentChatUserSuggestions');
    if (!suggestions) {
        return;
    }

    Array.from(suggestions.querySelectorAll('[data-user-id]')).forEach((element, index) => {
        if (index === chatHeaderAutocompleteState.activeIndex) {
            element.classList.add('bg-blue-100', 'dark:bg-blue-900/60', 'text-blue-900', 'dark:text-blue-100');
        } else {
            element.classList.remove('bg-blue-100', 'dark:bg-blue-900/60', 'text-blue-900', 'dark:text-blue-100');
        }
    });
}

function renderChatHeaderSuggestions(query = '', forceOpen = false) {
    const suggestions = document.getElementById('agentChatUserSuggestions');
    if (!suggestions) {
        return;
    }

    const trimmed = (query || '').trim().toLowerCase();
    const users = Array.isArray(chatUsersCache) ? chatUsersCache : [];

    let matches = users;
    if (trimmed) {
        matches = users.filter(user => user?.user_id?.toLowerCase().includes(trimmed));
    }

    chatHeaderAutocompleteState.filteredUsers = matches.slice(0, 25);
    chatHeaderAutocompleteState.activeIndex = chatHeaderAutocompleteState.filteredUsers.length > 0 ? 0 : -1;

    suggestions.innerHTML = '';

    if (chatHeaderAutocompleteState.filteredUsers.length === 0) {
        if (forceOpen) {
            const emptyItem = document.createElement('div');
            emptyItem.className = 'px-2 py-1.5 text-[11px] text-gray-500 dark:text-gray-400';
            emptyItem.textContent = trimmed ? 'No matching users found.' : 'No users available.';
            suggestions.appendChild(emptyItem);
            suggestions.classList.remove('hidden');
        } else {
            suggestions.classList.add('hidden');
        }
        return;
    }

    chatHeaderAutocompleteState.filteredUsers.forEach((user, index) => {
        if (!user?.user_id) {
            return;
        }
        const option = document.createElement('button');
        option.type = 'button';
        option.dataset.userId = user.user_id;
        option.className = 'w-full text-left px-2 py-1.5 text-[11px] text-gray-700 dark:text-gray-200 hover:bg-blue-50 dark:hover:bg-blue-900/40 focus:outline-none';
        option.textContent = user.user_id;
        option.addEventListener('mousedown', event => event.preventDefault());
        option.addEventListener('click', () => selectChatHeaderUser(user.user_id));
        option.addEventListener('mouseenter', () => {
            chatHeaderAutocompleteState.activeIndex = index;
            updateChatHeaderSuggestionActiveState();
        });
        suggestions.appendChild(option);
    });

    suggestions.classList.remove('hidden');
    updateChatHeaderSuggestionActiveState();
}

function moveChatHeaderSuggestion(direction) {
    const total = chatHeaderAutocompleteState.filteredUsers.length;
    if (total === 0) {
        return;
    }

    let nextIndex = chatHeaderAutocompleteState.activeIndex + direction;
    if (nextIndex < 0) {
        nextIndex = total - 1;
    } else if (nextIndex >= total) {
        nextIndex = 0;
    }
    chatHeaderAutocompleteState.activeIndex = nextIndex;
    updateChatHeaderSuggestionActiveState();
}

function selectChatHeaderUser(userId) {
    const input = document.getElementById('agentChatUserInput');
    if (input) {
        input.value = userId || '';
    }

    hideChatHeaderSuggestions();
    chatHeaderAutocompleteState.filteredUsers = [];
    chatHeaderAutocompleteState.activeIndex = -1;

    if (userId) {
        switchChatUser(userId);
    }
}

function syncChatHeaderUserSelector(selectedUserId) {
    const wrapper = document.getElementById('agentChatUserSelectorWrapper');
    const input = document.getElementById('agentChatUserInput');

    if (!wrapper || !input) {
        return;
    }

    if (!Array.isArray(chatUsersCache) || chatUsersCache.length === 0) {
        wrapper.classList.add('hidden');
        input.value = '';
        hideChatHeaderSuggestions();
        chatHeaderAutocompleteState.filteredUsers = [];
        chatHeaderAutocompleteState.activeIndex = -1;
        return;
    }

    wrapper.classList.remove('hidden');
    input.value = selectedUserId || '';
    hideChatHeaderSuggestions();
    chatHeaderAutocompleteState.filteredUsers = [];
    chatHeaderAutocompleteState.activeIndex = -1;
}

function showAgentChatPanel(agentName, chatUrl) {
    const panel = document.getElementById('agentChatPanel');
    const iframe = document.getElementById('agentChatIframe');
    const title = document.getElementById('agentChatTitle');
    const newTabBtn = document.getElementById('agentChatNewTabBtn');

    if (!panel || !iframe) {
        window.open(chatUrl, '_blank', 'noopener,noreferrer');
        return;
    }

    attachChatIframeHandlers(iframe);

    if (title) {
        title.textContent = `Agent Chat · ${agentName}`;
    }

    updateChatIframeDestination(iframe, newTabBtn, chatUrl);
    panel.classList.remove('hidden');
    document.body.classList.add('chat-panel-open');
}

function hideAgentChatPanel() {
    const panel = document.getElementById('agentChatPanel');
    const iframe = document.getElementById('agentChatIframe');
    const newTabBtn = document.getElementById('agentChatNewTabBtn');

    if (panel) {
        panel.classList.add('hidden');
    }

    document.body.classList.remove('chat-panel-open');

    if (iframe) {
        if (iframe._chatSidebarObserver) {
            iframe._chatSidebarObserver.disconnect();
            iframe._chatSidebarObserver = null;
        }
        iframe.src = '';
    }

    if (newTabBtn) {
        newTabBtn.onclick = null;
        newTabBtn.disabled = true;
        newTabBtn.classList.add('opacity-50', 'cursor-not-allowed');
    }

    const headerWrapper = document.getElementById('agentChatUserSelectorWrapper');
    const headerInput = document.getElementById('agentChatUserInput');
    if (headerWrapper) {
        headerWrapper.classList.add('hidden');
    }
    if (headerInput) {
        headerInput.value = '';
    }
    hideChatHeaderSuggestions();
    chatHeaderAutocompleteState.filteredUsers = [];
    chatHeaderAutocompleteState.activeIndex = -1;

    currentChatAgentName = null;
    currentChatUserId = '';
}

function hideChatUserModal() {
    const modal = document.getElementById('chatUserSelectModal');
    if (modal) {
        modal.classList.add('hidden');
    }
    resetChatUserModalState({ clearInput: true });
}

function cancelChatUserSelection() {
    hideChatUserModal();
    pendingChatAgentName = null;
}

function updateChatUserConfirmState() {
    const confirmBtn = document.getElementById('chatUserSelectConfirmBtn');

    if (!confirmBtn) {
        return;
    }

    confirmBtn.disabled = !chatUserModalSelectionId;
}

function syncModalUserSelectionState(users) {
    const field = document.getElementById('chatUserSelectField');
    const loading = document.getElementById('chatUserSelectLoading');
    const error = document.getElementById('chatUserSelectError');
    const input = document.getElementById('chatUserSelectInput');

    if (loading) {
        loading.classList.add('hidden');
    }

    if (Array.isArray(users) && users.length > 0) {
        if (field) {
            field.classList.remove('hidden');
        }
        if (error) {
            error.textContent = '';
            error.classList.add('hidden');
        }

        const storedUserId = getStoredChatUserId();
        const hasStored = storedUserId && users.some(user => user?.user_id === storedUserId);

        if (input) {
            input.value = hasStored ? storedUserId : '';
        }
        setChatUserModalSelection(hasStored ? storedUserId : '', { updateInput: false });
        hideChatUserSuggestions();

        if (input && typeof input.focus === 'function') {
            setTimeout(() => {
                input.focus();
                const cursorPosition = input.value.length;
                input.setSelectionRange(cursorPosition, cursorPosition);
            }, 0);
        }

        updateChatUserConfirmState();
        syncChatUserInputValidation();
    } else {
        if (field) {
            field.classList.add('hidden');
        }
        if (error) {
            error.textContent = 'No users found. Add users in the Users tab and try again.';
            error.classList.remove('hidden');
        }
        updateChatUserConfirmState();
        syncChatUserInputValidation();
    }
}

function presentChatUserSelectionModal() {
    const modal = document.getElementById('chatUserSelectModal');
    const loading = document.getElementById('chatUserSelectLoading');
    const error = document.getElementById('chatUserSelectError');
    const field = document.getElementById('chatUserSelectField');

    if (!modal) {
        const storedUserId = getStoredChatUserId();
        const agentName = pendingChatAgentName;
        pendingChatAgentName = null;
        launchAgentChat(agentName, storedUserId);
        return;
    }

    resetChatUserModalState({ clearInput: true });

    if (error) {
        error.textContent = '';
        error.classList.add('hidden');
    }
    if (field) {
        field.classList.add('hidden');
    }
    if (loading) {
        loading.classList.remove('hidden');
    }

    modal.classList.remove('hidden');

    loadChatUsers()
        .then(users => {
            syncModalUserSelectionState(users);
        })
        .catch(fetchError => {
            console.error('Failed to load users for chat', fetchError);
            if (loading) {
                loading.classList.add('hidden');
            }
            if (field) {
                field.classList.add('hidden');
            }
            if (error) {
                error.textContent = 'Failed to load users. Please try again.';
                error.classList.remove('hidden');
            }
            updateChatUserConfirmState();
            syncChatUserInputValidation();
        });
}

function launchAgentChat(agentName, userId) {
    if (!agentName) {
        return;
    }

    const resolvedUserId = userId || '';
    currentChatAgentName = agentName;
    currentChatUserId = resolvedUserId;
    setStoredChatUserId(resolvedUserId);

    const chatUrl = buildChatUrl(agentName, resolvedUserId);
    showAgentChatPanel(agentName, chatUrl);
    syncChatHeaderUserSelector(resolvedUserId);

}

function confirmChatUserSelection() {
    if (!chatUserModalSelectionId || !pendingChatAgentName) {
        syncChatUserInputValidation();
        return;
    }

    const agentName = pendingChatAgentName;
    const userId = chatUserModalSelectionId;
    pendingChatAgentName = null;

    hideChatUserModal();
    launchAgentChat(agentName, userId);
}

function initializeChatUserSelectionHandlers() {
    const modal = document.getElementById('chatUserSelectModal');
    if (modal && !modal.dataset.chatUserHandlersAttached) {
        const input = document.getElementById('chatUserSelectInput');
        const suggestions = document.getElementById('chatUserSelectSuggestions');
        const confirmBtn = document.getElementById('chatUserSelectConfirmBtn');
        const cancelBtn = document.getElementById('chatUserSelectCancelBtn');
        const closeBtn = document.getElementById('chatUserSelectCloseBtn');
        const backdrop = document.getElementById('chatUserSelectBackdrop');

        if (input) {
            input.addEventListener('input', event => {
                const value = event.target.value;
                const matchedUser = (chatUsersCache || []).find(user => user?.user_id === value);
                setChatUserModalSelection(matchedUser ? matchedUser.user_id : '', { updateInput: false });
                renderChatUserSuggestions(value, true);
            });
            input.addEventListener('focus', event => {
                const value = event.target.value;
                renderChatUserSuggestions(value, true);
            });
            input.addEventListener('keydown', event => {
                if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    if (suggestions?.classList.contains('hidden')) {
                        renderChatUserSuggestions(event.target.value, true);
                    } else {
                        moveChatUserSuggestion(1);
                    }
                } else if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    if (!suggestions?.classList.contains('hidden')) {
                        moveChatUserSuggestion(-1);
                    }
                } else if (event.key === 'Enter') {
                    if (!suggestions?.classList.contains('hidden') && chatUserAutocompleteState.activeIndex >= 0) {
                        event.preventDefault();
                        const activeUser = chatUserAutocompleteState.filteredUsers[chatUserAutocompleteState.activeIndex];
                        if (activeUser?.user_id) {
                            selectChatUserFromSuggestion(activeUser.user_id);
                        }
                    } else if (!chatUserModalSelectionId) {
                        event.preventDefault();
                        syncChatUserInputValidation();
                    }
                } else if (event.key === 'Escape') {
                    if (!suggestions?.classList.contains('hidden')) {
                        hideChatUserSuggestions();
                        event.preventDefault();
                    }
                }
            });
            input.addEventListener('blur', () => {
                setTimeout(() => {
                    if (!modal.classList.contains('hidden')) {
                        hideChatUserSuggestions();
                        syncChatUserInputValidation();
                    }
                }, 150);
            });
        }
        if (confirmBtn) {
            confirmBtn.addEventListener('click', confirmChatUserSelection);
        }
        if (cancelBtn) {
            cancelBtn.addEventListener('click', cancelChatUserSelection);
        }
        if (closeBtn) {
            closeBtn.addEventListener('click', cancelChatUserSelection);
        }
        if (backdrop) {
            backdrop.addEventListener('click', cancelChatUserSelection);
        }

        modal.dataset.chatUserHandlersAttached = 'true';
    }

    const headerInput = document.getElementById('agentChatUserInput');
    const headerSuggestions = document.getElementById('agentChatUserSuggestions');
    if (headerInput && !headerInput.dataset.listenerAttached) {
        headerInput.addEventListener('input', event => {
            const value = event.target.value;
            if (!value) {
                resetChatHeaderAutocompleteState();
                return;
            }
            renderChatHeaderSuggestions(value, true);
        });
        headerInput.addEventListener('focus', event => {
            renderChatHeaderSuggestions(event.target.value, true);
        });
        headerInput.addEventListener('keydown', event => {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                if (headerSuggestions?.classList.contains('hidden')) {
                    renderChatHeaderSuggestions(event.target.value, true);
                } else {
                    moveChatHeaderSuggestion(1);
                }
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                if (!headerSuggestions?.classList.contains('hidden')) {
                    moveChatHeaderSuggestion(-1);
                }
            } else if (event.key === 'Enter') {
                const value = event.target.value.trim();
                if (!headerSuggestions?.classList.contains('hidden') && chatHeaderAutocompleteState.activeIndex >= 0) {
                    event.preventDefault();
                    const active = chatHeaderAutocompleteState.filteredUsers[chatHeaderAutocompleteState.activeIndex];
                    if (active?.user_id) {
                        selectChatHeaderUser(active.user_id);
                    }
                } else if (value) {
                    const matched = (chatUsersCache || []).find(user => user?.user_id === value);
                    if (matched?.user_id) {
                        event.preventDefault();
                        selectChatHeaderUser(matched.user_id);
                    }
                }
            } else if (event.key === 'Escape') {
                if (!headerSuggestions?.classList.contains('hidden')) {
                    hideChatHeaderSuggestions();
                    chatHeaderAutocompleteState.filteredUsers = [];
                    chatHeaderAutocompleteState.activeIndex = -1;
                    event.preventDefault();
                }
            }
        });
        headerInput.addEventListener('blur', () => {
            setTimeout(() => {
                hideChatHeaderSuggestions();
            }, 150);
        });
        headerInput.dataset.listenerAttached = 'true';
    }
}

function switchChatUser(userId) {
    const resolvedUserId = userId || '';
    currentChatUserId = resolvedUserId;
    setStoredChatUserId(resolvedUserId);

    if (!currentChatAgentName) {
        return;
    }

    const iframe = document.getElementById('agentChatIframe');
    const newTabBtn = document.getElementById('agentChatNewTabBtn');
    const chatUrl = buildChatUrl(currentChatAgentName, resolvedUserId);
    updateChatIframeDestination(iframe, newTabBtn, chatUrl);
    syncChatHeaderUserSelector(resolvedUserId);
}

// ============================================================================
// Instruction Modal Handling
// ============================================================================

let instructionModalTargetId = '';

function initializeInstructionFieldModal() {
    initializeInstructionModalHandlers();

    const editInstructionField = document.getElementById('editAgentInstruction');
    if (editInstructionField && !editInstructionField.dataset.instructionModalAttached) {
        editInstructionField.readOnly = true;
        editInstructionField.style.cursor = 'pointer';
        editInstructionField.title = 'Click to edit instruction';
        editInstructionField.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            openInstructionModal(editInstructionField.id);
        });
        editInstructionField.dataset.instructionModalAttached = 'true';
    }
}

function initializeInstructionModalHandlers() {
    const modal = document.getElementById('editInstructionModal');
    if (!modal || modal.dataset.handlersAttached) {
        return;
    }

    const textarea = document.getElementById('editInstructionModalTextarea');
    const saveBtn = document.getElementById('editInstructionModalSaveBtn');
    const cancelBtn = document.getElementById('editInstructionModalCancelBtn');
    const closeBtn = document.getElementById('editInstructionModalCloseBtn');
    const backdrop = document.getElementById('editInstructionModalBackdrop');

    if (saveBtn) {
        saveBtn.addEventListener('click', () => closeInstructionModal(true));
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => closeInstructionModal(false));
    }
    if (closeBtn) {
        closeBtn.addEventListener('click', () => closeInstructionModal(false));
    }
    if (backdrop) {
        backdrop.addEventListener('click', () => closeInstructionModal(false));
    }
    if (textarea) {
        textarea.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeInstructionModal(false);
            }
        });
    }

    modal.dataset.handlersAttached = 'true';
}

function openInstructionModal(fieldId) {
    const field = document.getElementById(fieldId);
    const modal = document.getElementById('editInstructionModal');
    const textarea = document.getElementById('editInstructionModalTextarea');

    if (!field || !modal || !textarea) {
        return;
    }

    instructionModalTargetId = fieldId;
    textarea.value = field.value || '';
    modal.classList.remove('hidden');

    setTimeout(() => {
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    }, 0);
}

function closeInstructionModal(saveChanges) {
    const modal = document.getElementById('editInstructionModal');
    const textarea = document.getElementById('editInstructionModalTextarea');
    const targetField = document.getElementById(instructionModalTargetId);

    if (!modal || !textarea) {
        return;
    }

    if (saveChanges && targetField) {
        const newValue = textarea.value || '';
        if (targetField.value !== newValue) {
            targetField.value = newValue;
            targetField.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }

    modal.classList.add('hidden');
    instructionModalTargetId = '';

    if (targetField) {
        setTimeout(() => {
            targetField.focus({ preventScroll: true });
        }, 0);
    }
}

function attachChatIframeHandlers(iframe) {
    if (!iframe) {
        return;
    }

    const injectSidebarHider = (doc) => {
        if (!doc) {
            return;
        }

        if (!doc.getElementById('mateAgentChatStyle')) {
            const style = doc.createElement('style');
            style.id = 'mateAgentChatStyle';
            style.textContent = `
                app-chat .side-drawer,
                app-chat .drawer-container .side-drawer {
                    display: none !important;
                }

                app-chat .drawer-container {
                    grid-template-columns: minmax(0, 1fr) !important;
                }

                app-chat .material-symbols-outlined[mattooltip] {
                    display: none !important;
                }

                app-chat .resize-handler {
                    display: none !important;
                }
            `;
            doc.head.appendChild(style);
        }
    };

    const handleLoad = () => {
        const doc = iframe.contentWindow?.document;
        if (!doc) {
            return;
        }

        injectSidebarHider(doc);

        if (iframe._chatSidebarObserver) {
            iframe._chatSidebarObserver.disconnect();
        }

        const observer = new MutationObserver(() => injectSidebarHider(doc));
        observer.observe(doc.body, { childList: true, subtree: true });
        iframe._chatSidebarObserver = observer;
    };

    if (!iframe.dataset.chatHooksAttached) {
        iframe.addEventListener('load', handleLoad);
        iframe.dataset.chatHooksAttached = 'true';
    }

    const doc = iframe.contentWindow?.document;
    if (doc && doc.readyState === 'complete') {
        handleLoad();
    }
}

function showImportModal() {
    document.getElementById('importAgentsModal').classList.remove('hidden');
}

function hideImportModal() {
    document.getElementById('importAgentsModal').classList.add('hidden');
    // Reset file input and preview
    const fileInput = document.getElementById('importFile');
    const preview = document.getElementById('importPreview');
    const submitBtn = document.getElementById('importSubmitBtn');
    if (fileInput) fileInput.value = '';
    if (preview) preview.classList.add('hidden');
    if (submitBtn) submitBtn.disabled = true;
}

// ============================================================================
// Agent Hierarchy Management
// ============================================================================

/**
 * Build agent hierarchy map
 * NOTE: This function needs access to the configs variable from the template
 */
function buildAgentHierarchy(configs) {
    const hierarchy = {};
    
    // First pass: identify all agents and their direct children
    configs.forEach(config => {
        hierarchy[config.name] = {
            config: config,
            children: []
        };
    });
    
    // Second pass: build parent-child relationships
    configs.forEach(config => {
        if (config.parent_agents && Array.isArray(config.parent_agents)) {
            config.parent_agents.forEach(parentName => {
                if (hierarchy[parentName]) {
                    hierarchy[parentName].children.push(config.name);
                }
            });
        }
    });
    
    // Ensure children arrays are sorted for consistent ordering
    Object.keys(hierarchy).forEach(name => {
        hierarchy[name].children.sort((a, b) => a.localeCompare(b));
    });
    
    return hierarchy;
}

/**
 * Get all descendants of an agent (recursive)
 */
function getAllDescendants(agentName, hierarchy, descendants = new Set()) {
    if (!hierarchy[agentName]) return descendants;
    
    descendants.add(agentName);
    
    const children = hierarchy[agentName].children;
    children.forEach(childName => {
        getAllDescendants(childName, hierarchy, descendants);
    });
    
    return descendants;
}

// ============================================================================
// Search, Filter, and Hierarchical Rendering
// ============================================================================

const agentTableState = {
    configMap: new Map(),
    hierarchy: {},
    parentMap: new Map(),
    rowState: new Map(),
    agentRowKeys: new Map(),
    rootAgentNames: [],
    rootRowKeys: [],
    manualExpanded: new Set(),
    allowedAgents: new Set(),
    highlightAgents: new Set()
};

function getParentList(config) {
    if (!config) return [];
    const rawParents = config.parent_agents;
    if (Array.isArray(rawParents)) {
        return rawParents.filter(Boolean);
    }
    if (typeof rawParents === 'string') {
        const trimmed = rawParents.trim();
        if (!trimmed) {
            return [];
        }
        try {
            const parsed = JSON.parse(trimmed);
            if (Array.isArray(parsed)) {
                return parsed.filter(Boolean);
            }
        } catch (error) {
            // Not valid JSON; fall through
        }
        return trimmed.split(',').map(p => p.trim()).filter(Boolean);
    }
    return [];
}

function toDatasetJSON(value, fallback = '{}') {
    if (value === null || value === undefined) {
        return fallback;
    }
    if (typeof value === 'string') {
        return value;
    }
    try {
        return JSON.stringify(value);
    } catch (error) {
        return fallback;
    }
}

function buildParentMap(configs) {
    const map = new Map();
    configs.forEach(config => {
        map.set(config.name, getParentList(config));
    });
    return map;
}

function computeFilterResult(agentConfigs, searchTerm, rootAgent) {
    const parentMap = buildParentMap(agentConfigs);

    const parentChildrenMap = new Map();
    parentMap.forEach((parents, childName) => {
        parents.forEach(parentName => {
            if (!parentChildrenMap.has(parentName)) {
                parentChildrenMap.set(parentName, []);
            }
            parentChildrenMap.get(parentName).push(childName);
        });
    });
    parentChildrenMap.forEach(children => children.sort((a, b) => a.localeCompare(b)));

    const hierarchy = {};
    agentConfigs.forEach(config => {
        const children = parentChildrenMap.get(config.name) || [];
        hierarchy[config.name] = { children };
    });

    let allowedByRoot = new Set();
    if (rootAgent) {
        allowedByRoot = getAllDescendants(rootAgent, hierarchy);
    } else {
        agentConfigs.forEach(config => allowedByRoot.add(config.name));
    }

    const highlightAgents = new Set();
    const autoExpandAgents = new Set();
    let searchMatches = null;

    if (searchTerm) {
        const searchLower = searchTerm.toLowerCase();
        searchMatches = new Set();
        const initialMatches = [];

        agentConfigs.forEach(config => {
            const name = (config.name || '').toLowerCase();
            const type = (config.type || '').toLowerCase();
            const model = (config.model_name || '').toLowerCase();
            const description = (config.description || '').toLowerCase();

            const matches = name.includes(searchLower) ||
                type.includes(searchLower) ||
                model.includes(searchLower) ||
                description.includes(searchLower);

            if (matches) {
                searchMatches.add(config.name);
                initialMatches.push(config.name);
                highlightAgents.add(config.name);
            }
        });

        const addAncestors = (agentName) => {
            const parents = parentMap.get(agentName) || [];
            parents.forEach(parent => {
                if (!searchMatches.has(parent)) {
                    searchMatches.add(parent);
                    addAncestors(parent);
                }
            });
        };

        initialMatches.forEach(name => {
            addAncestors(name);
            const descendants = getAllDescendants(name, hierarchy);
            descendants.forEach(descendant => searchMatches.add(descendant));
        });

        initialMatches.forEach(name => {
            const parents = parentMap.get(name) || [];
            parents.forEach(parent => autoExpandAgents.add(parent));
        });
    }

    const allowedAgents = new Set();
    allowedByRoot.forEach(name => {
        if (!searchMatches || searchMatches.has(name)) {
            allowedAgents.add(name);
        }
    });

    if (rootAgent) {
        allowedAgents.add(rootAgent);
        autoExpandAgents.add(rootAgent);
    }

    return {
        hierarchy,
        parentMap,
        parentChildrenMap,
        allowedAgents,
        highlightAgents,
        autoExpandAgents
    };
}

function createTypeBadgeClass(agentType) {
    switch ((agentType || '').toLowerCase()) {
        case 'llm':
            return 'inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200';
        case 'sequential':
            return 'inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200';
        case 'parallel':
            return 'inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-yellow-100 dark:bg-yellow-900 text-yellow-800 dark:text-yellow-200';
        case 'loop':
            return 'inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-orange-100 dark:bg-orange-900 text-orange-800 dark:text-orange-200';
        default:
            return 'inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200';
    }
}

function createStatusBadge(config) {
    const wrappers = [];
    
    const statusSpan = document.createElement('span');
    statusSpan.className = config.disabled
        ? 'inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200'
        : 'inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200';
    statusSpan.textContent = config.disabled ? 'Disabled' : 'Active';
    wrappers.push(statusSpan);
    
    if (config.hardcoded) {
        const hardcodedSpan = document.createElement('span');
        hardcodedSpan.className = 'inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-purple-100 dark:bg-purple-900 text-purple-800 dark:text-purple-200 ml-1';
        hardcodedSpan.innerHTML = '<i class="fas fa-code text-xs mr-1"></i>HC';
        wrappers.push(hardcodedSpan);
    }
    
    return wrappers;
}

function createActionButton({ title, icon, className, onClick }) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    button.title = title;
    button.innerHTML = `<i class="${icon}"></i>`;
    button.addEventListener('click', onClick);
    return button;
}

function createAgentRow(config, depth, parentName, hasChildren, isHighlighted, rowKey, parentKey) {
    const row = document.createElement('tr');
    row.className = 'agent-row cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700';
    row.dataset.rowKey = rowKey;
    row.dataset.parentKey = parentKey || '';
    row.dataset.name = config.name || '';
    row.dataset.type = config.type || '';
    row.dataset.model = config.model_name || '';
    row.dataset.description = config.description || '';
    row.dataset.instruction = config.instruction || '';
    row.dataset.roles = toDatasetJSON(config.allowed_for_roles, '[]');
    row.dataset.maxIterations = config.max_iterations || '';
    row.dataset.toolConfig = toDatasetJSON(config.tool_config);
    row.dataset.mcpServersConfig = toDatasetJSON(config.mcp_servers_config);
    row.dataset.plannerConfig = toDatasetJSON(config.planner_config);
    row.dataset.generateContentConfig = toDatasetJSON(config.generate_content_config);
    row.dataset.inputSchema = toDatasetJSON(config.input_schema);
    row.dataset.outputSchema = toDatasetJSON(config.output_schema);
    row.dataset.includeContents = config.include_contents || '';
    row.dataset.disabled = config.disabled ? 'true' : 'false';
    row.dataset.hardcoded = config.hardcoded ? 'true' : 'false';
    row.dataset.projectId = config.project_id !== undefined && config.project_id !== null ? String(config.project_id) : '';
    row.dataset.projectName = (config.project && config.project.name) || '';
    const parents = getParentList(config);
    const isRootAgent = parents.length === 0;
    row.dataset.parents = JSON.stringify(parents);

    row.addEventListener('click', () => {
        // Ensure openEditAgentModal is available (defined in agents.html inline script)
        if (typeof window.openEditAgentModal === 'function') {
            window.openEditAgentModal(row);
        } else if (typeof openEditAgentModal === 'function') {
            openEditAgentModal(row);
        } else {
            console.error('openEditAgentModal not available');
            // Fallback: try to use editAgentById if available
            if (typeof window.editAgentById === 'function' && config.id) {
                window.editAgentById(config.id);
            } else {
                showNotification('Error: Cannot open edit modal. Please refresh the page.', 'error');
            }
        }
    });

    const nameCell = document.createElement('td');
    nameCell.className = 'px-3 py-1.5 text-xs font-medium text-gray-900 dark:text-white truncate';
    const nameContainer = document.createElement('div');
    nameContainer.className = 'flex items-center space-x-2';
    nameContainer.style.marginLeft = `${depth * 1.25}rem`;

    let toggleButton = null;
    let toggleIcon = null;

    if (hasChildren) {
        toggleButton = document.createElement('button');
        toggleButton.type = 'button';
        toggleButton.className = 'toggle-children text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 focus:outline-none w-4 h-4 flex items-center justify-center';
        toggleButton.innerHTML = '<i class="fas fa-chevron-right text-xs"></i>';
        toggleIcon = toggleButton.querySelector('i');
        toggleButton.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleAgentChildren(rowKey);
        });
        nameContainer.appendChild(toggleButton);
    } else {
        const spacer = document.createElement('span');
        spacer.className = 'inline-block w-3';
        nameContainer.appendChild(spacer);
    }

    const nameLabel = document.createElement('span');
    nameLabel.textContent = config.name || '';
    if (isHighlighted) {
        nameLabel.className = 'bg-yellow-100 dark:bg-yellow-900/60 text-yellow-900 dark:text-yellow-100 px-1.5 py-0.5 rounded';
    }
    nameContainer.appendChild(nameLabel);
    nameCell.appendChild(nameContainer);
    row.appendChild(nameCell);

    const typeCell = document.createElement('td');
    typeCell.className = 'px-3 py-1.5';
    const typeBadge = document.createElement('span');
    typeBadge.className = createTypeBadgeClass(config.type);
    typeBadge.textContent = config.type || '';
    typeCell.appendChild(typeBadge);
    row.appendChild(typeCell);

    const modelCell = document.createElement('td');
    modelCell.className = 'px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 truncate';
    modelCell.textContent = config.model_name || 'N/A';
    row.appendChild(modelCell);

    const parentsCell = document.createElement('td');
    parentsCell.className = 'px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 truncate';
    parentsCell.textContent = parents.length ? parents.join(', ') : 'None';
    row.appendChild(parentsCell);

    const statusCell = document.createElement('td');
    statusCell.className = 'px-3 py-1.5';
    const statusBadges = createStatusBadge(config);
    statusBadges.forEach(badge => statusCell.appendChild(badge));
    row.appendChild(statusCell);

    const actionsCell = document.createElement('td');
    actionsCell.className = 'px-3 py-1.5 text-xs font-medium space-x-1';

    actionsCell.appendChild(createActionButton({
        title: 'Edit',
        icon: 'fas fa-edit',
        className: 'text-blue-600 dark:text-blue-400 hover:text-blue-900 dark:hover:text-blue-300',
        onClick: (event) => {
            event.stopPropagation();
            editAgentById(config.id);
        }
    }));

    actionsCell.appendChild(createActionButton({
        title: 'Copy',
        icon: 'fas fa-copy',
        className: 'text-purple-600 dark:text-purple-400 hover:text-purple-900 dark:hover:text-purple-300',
        onClick: (event) => {
            event.stopPropagation();
            copyAgentById(config.id);
        }
    }));

    actionsCell.appendChild(createActionButton({
        title: 'Reload',
        icon: 'fas fa-redo',
        className: 'text-yellow-600 dark:text-yellow-400 hover:text-yellow-900 dark:hover:text-yellow-300',
        onClick: (event) => {
            event.stopPropagation();
            reinitializeAgent(config.name);
        }
    }));

    actionsCell.appendChild(createActionButton({
        title: 'Delete',
        icon: 'fas fa-trash',
        className: 'text-red-600 dark:text-red-400 hover:text-red-900 dark:hover:text-red-300',
        onClick: (event) => {
            event.stopPropagation();
            deleteAgent(config.id, config.name, !!config.hardcoded);
        }
    }));

    if (isRootAgent) {
        actionsCell.appendChild(createActionButton({
            title: 'Chat',
            icon: 'fas fa-comments',
            className: 'text-green-600 dark:text-green-400 hover:text-green-900 dark:hover:text-green-300',
            onClick: (event) => {
                event.stopPropagation();
                openAgentChat(config.name);
            }
        }));

        actionsCell.appendChild(createActionButton({
            title: 'Widget Keys',
            icon: 'fas fa-code',
            className: 'text-teal-600 dark:text-teal-400 hover:text-teal-900 dark:hover:text-teal-300',
            onClick: (event) => {
                event.stopPropagation();
                if (typeof showWidgetKeysModal === 'function') {
                    showWidgetKeysModal(config.name, config.project_id);
                }
            }
        }));
    }

    // Check if agent has memory tools configured
    const toolConfig = config.tool_config;
    let hasMemoryTools = false;
    if (toolConfig) {
        try {
            const toolConfigObj = typeof toolConfig === 'string' ? JSON.parse(toolConfig) : toolConfig;
            if (toolConfigObj.memory_blocks) {
                if (toolConfigObj.memory_blocks === true) {
                    hasMemoryTools = true;
                } else if (typeof toolConfigObj.memory_blocks === 'object') {
                    hasMemoryTools = toolConfigObj.memory_blocks.enabled !== false;
                }
            }
        } catch (e) {
            // Invalid JSON, skip
            console.warn('Failed to parse tool_config for memory tools check:', e);
        }
    }
    
    if (hasMemoryTools) {
        actionsCell.appendChild(createActionButton({
            title: 'Memory Blocks',
            icon: 'fas fa-database',
            className: 'text-indigo-600 dark:text-indigo-400 hover:text-indigo-900 dark:hover:text-indigo-300',
            onClick: (event) => {
                event.stopPropagation();
                if (typeof showMemoryBlocksModal === 'function') {
                    showMemoryBlocksModal(config.name);
                } else {
                    console.error('showMemoryBlocksModal function not available');
                }
            }
        }));
    }

    row.appendChild(actionsCell);

    return {
        row,
        parent: parentName,
        depth,
        children: [],
        childRowKeys: [],
        agentName: config.name,
        key: rowKey,
        parentKey,
        toggleButton,
        toggleIcon,
        expanded: false
    };
}

function appendAgentRow(agentName, depth, parentKey, parentName, tableBody, highlightAgents, autoExpandSet, manualExpandedNext) {
    if (!agentTableState.allowedAgents.has(agentName)) {
        return null;
    }

    const config = agentTableState.configMap.get(agentName);
    if (!config) return null;

    const childNames = agentTableState.parentChildrenMap.get(config.name) || [];
    const hasChildren = childNames.length > 0;

    const rowKey = `${parentKey || 'ROOT'}>${agentName}`;
    const rowState = createAgentRow(config, depth, parentName, hasChildren, highlightAgents.has(agentName), rowKey, parentKey);
    tableBody.appendChild(rowState.row);

    const isManual = agentTableState.manualExpanded.has(rowKey);
    const shouldExpand = isManual || autoExpandSet.has(agentName);
    if (isManual) {
        manualExpandedNext.add(rowKey);
    }
    rowState.expanded = shouldExpand;

    agentTableState.rowState.set(rowKey, rowState);
    if (!agentTableState.agentRowKeys.has(agentName)) {
        agentTableState.agentRowKeys.set(agentName, []);
    }
    agentTableState.agentRowKeys.get(agentName).push(rowKey);

    childNames.forEach(childName => {
        const childKey = appendAgentRow(childName, depth + 1, rowKey, agentName, tableBody, highlightAgents, autoExpandSet, manualExpandedNext);
        if (childKey) {
            rowState.childRowKeys.push(childKey);
        }
    });

    return rowKey;
}

function updateToggleIcon(rowKey) {
    const state = agentTableState.rowState.get(rowKey);
    if (!state || !state.toggleIcon) return;
    state.toggleIcon.className = state.expanded ? 'fas fa-chevron-down text-xs' : 'fas fa-chevron-right text-xs';
}

function applyRowVisibility() {
    const visited = new Set();
    const visit = (rowKey) => {
        if (visited.has(rowKey)) return;
        visited.add(rowKey);

        const state = agentTableState.rowState.get(rowKey);
        if (!state) return;

        const row = state.row;
        if (!state.parentKey) {
            row.style.display = '';
        } else {
            const parentState = agentTableState.rowState.get(state.parentKey);
            if (parentState && parentState.row.style.display !== 'none' && parentState.expanded) {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        }

        updateToggleIcon(rowKey);
        state.childRowKeys.forEach(childKey => visit(childKey));
    };

    agentTableState.rootRowKeys.forEach(rootKey => visit(rootKey));
}

function renderAgentTable(agentConfigs, filterResult) {
    const tableBody = document.getElementById('agentsTableBody');
    if (!tableBody) return;

    tableBody.innerHTML = '';
    agentTableState.configMap = new Map(agentConfigs.map(config => [config.name, config]));
    agentTableState.hierarchy = filterResult.hierarchy;
    agentTableState.parentMap = filterResult.parentMap;
    agentTableState.parentChildrenMap = filterResult.parentChildrenMap || new Map();
    agentTableState.allowedAgents = filterResult.allowedAgents;
    agentTableState.highlightAgents = filterResult.highlightAgents;
    agentTableState.rowState = new Map();
    agentTableState.agentRowKeys = new Map();

    const autoExpandSet = filterResult.autoExpandAgents || new Set();
    const manualExpandedNext = new Set();

    const rootAgents = agentConfigs.filter(config => {
        const parents = getParentList(config);
        return parents.length === 0 && agentTableState.allowedAgents.has(config.name);
    }).sort((a, b) => a.name.localeCompare(b.name));

    agentTableState.rootAgentNames = rootAgents.map(config => config.name);

    if (agentTableState.allowedAgents.size === 0) {
        const emptyRow = document.createElement('tr');
        const emptyCell = document.createElement('td');
        emptyCell.colSpan = 6;
        emptyCell.className = 'px-3 py-3 text-center text-xs text-gray-500 dark:text-gray-400';
        emptyCell.textContent = 'No agents match the current filters.';
        emptyRow.appendChild(emptyCell);
        tableBody.appendChild(emptyRow);
        agentTableState.rootRowKeys = [];
        agentTableState.manualExpanded = manualExpandedNext;
        if (typeof updateGraphEditorButtonState === 'function') {
            updateGraphEditorButtonState();
        }
        return;
    }

    const rootRowKeys = [];
    rootAgents.forEach(config => {
        const rowKey = appendAgentRow(
            config.name,
            0,
            null,
            null,
            tableBody,
            agentTableState.highlightAgents,
            autoExpandSet,
            manualExpandedNext
        );
        if (rowKey) {
            rootRowKeys.push(rowKey);
        }
    });

    agentTableState.rootRowKeys = rootRowKeys;
    agentTableState.manualExpanded = manualExpandedNext;
    applyRowVisibility();

    if (typeof updateGraphEditorButtonState === 'function') {
        updateGraphEditorButtonState();
    }
}

function toggleAgentChildren(rowKey) {
    const state = agentTableState.rowState.get(rowKey);
    if (!state || state.childRowKeys.length === 0) {
        return;
    }

    state.expanded = !state.expanded;
    if (state.expanded) {
        agentTableState.manualExpanded.add(rowKey);

        if (!state.parentKey) {
            const rootFilter = document.getElementById('rootAgentFilter');
            if (rootFilter && !rootFilter.disabled) {
                const hasOption = Array.from(rootFilter.options).some(option => option.value === state.agentName);
                if (hasOption && rootFilter.value !== state.agentName) {
                    rootFilter.value = state.agentName;
                    try {
                        localStorage.setItem('agentRootFilter', state.agentName);
                    } catch (error) {
                        // Ignore storage errors
                    }
                    filterAgents();
                    return;
                }
            }
        }
    } else {
        agentTableState.manualExpanded.delete(rowKey);
    }
    updateToggleIcon(rowKey);
    applyRowVisibility();
}

function populateRootAgentFilter(configs) {
    const filterSelect = document.getElementById('rootAgentFilter');
    if (!filterSelect || filterSelect.disabled) {
        updateRootAgentChatButton();
        return;
    }

    const savedFilter = localStorage.getItem('agentRootFilter') || '';
    while (filterSelect.options.length > 1) {
        filterSelect.remove(1);
    }

    const rootAgents = configs.filter(config => getParentList(config).length === 0)
        .sort((a, b) => a.name.localeCompare(b.name));

    rootAgents.forEach(config => {
        const option = document.createElement('option');
        option.value = config.name;
        option.textContent = config.name;
        if (config.name === savedFilter) {
            option.selected = true;
        }
        filterSelect.appendChild(option);
    });

    updateRootAgentChatButton();
}

function openAgentChat(agentName) {
    if (!agentName) {
        return;
    }
    pendingChatAgentName = agentName;
    presentChatUserSelectionModal();
}

function openRootAgentChat() {
    const rootSelect = document.getElementById('rootAgentFilter');
    if (!rootSelect || rootSelect.disabled) {
        return;
    }

    const selectedAgent = rootSelect.value;
    openAgentChat(selectedAgent);
}

function updateRootAgentChatButton() {
    const rootSelect = document.getElementById('rootAgentFilter');
    const chatButton = document.getElementById('rootAgentChatBtn');

    if (!chatButton) {
        return;
    }

    const isEnabled = rootSelect && !rootSelect.disabled && rootSelect.value;
    if (isEnabled) {
        chatButton.classList.remove('hidden');
        chatButton.disabled = false;
        chatButton.title = `Open ${rootSelect.value} chat`;
    } else {
        if (!chatButton.classList.contains('hidden')) {
            chatButton.classList.add('hidden');
        }
        chatButton.disabled = true;
        chatButton.removeAttribute('title');
    }
}

function applyFiltersAndRender(configsParam) {
    const agentConfigs = configsParam || (typeof configs !== 'undefined' ? configs : []);
    const searchInput = document.getElementById('agentSearch');
    const rootSelect = document.getElementById('rootAgentFilter');

    const searchValue = searchInput ? searchInput.value : '';
    const rootValue = rootSelect ? rootSelect.value : '';

    try {
        localStorage.setItem('agentSearchFilter', searchValue);
        localStorage.setItem('agentRootFilter', rootValue);
    } catch (error) {
        // Ignore storage errors
    }

    const filterResult = computeFilterResult(agentConfigs, searchValue.toLowerCase(), rootValue);
    renderAgentTable(agentConfigs, filterResult);
}

function filterAgents(configsParam) {
    updateRootAgentChatButton();
    applyFiltersAndRender(configsParam);
}

function initializeSearchAndFilter(configs) {
    const filterSelect = document.getElementById('rootAgentFilter');
    const searchInput = document.getElementById('agentSearch');
    const chatButton = document.getElementById('rootAgentChatBtn');
    const chatBackdrop = document.getElementById('agentChatBackdrop');

    if (chatButton && !chatButton.dataset.chatListenerAttached) {
        chatButton.addEventListener('click', (event) => {
            event.preventDefault();
            openRootAgentChat();
        });
        chatButton.dataset.chatListenerAttached = 'true';
    }

    if (chatBackdrop && !chatBackdrop.dataset.listenerAttached) {
        chatBackdrop.addEventListener('click', () => hideAgentChatPanel());
        chatBackdrop.dataset.listenerAttached = 'true';
    }

    // Add backdrop click handlers for agent modals
    const editAgentModal = document.getElementById('editAgentModal');
    if (editAgentModal && !editAgentModal.dataset.listenerAttached) {
        editAgentModal.addEventListener('click', (event) => {
            if (event.target === editAgentModal) {
                hideEditAgentModal();
            }
        });
        editAgentModal.dataset.listenerAttached = 'true';
    }

    const createAgentModal = document.getElementById('createAgentModal');
    if (createAgentModal && !createAgentModal.dataset.listenerAttached) {
        createAgentModal.addEventListener('click', (event) => {
            if (event.target === createAgentModal) {
                hideCreateAgentModal();
            }
        });
        createAgentModal.dataset.listenerAttached = 'true';
    }

    const copyAgentModal = document.getElementById('copyAgentModal');
    if (copyAgentModal && !copyAgentModal.dataset.listenerAttached) {
        copyAgentModal.addEventListener('click', (event) => {
            if (event.target === copyAgentModal) {
                hideCopyAgentModal();
            }
        });
        copyAgentModal.dataset.listenerAttached = 'true';
    }

    initializeChatUserSelectionHandlers();
    initializeInstructionFieldModal();

    if (!document.body.dataset.chatPanelKeyListenerAttached) {
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                hideAgentChatPanel();
            }
        });
        document.body.dataset.chatPanelKeyListenerAttached = 'true';
    }

    if (!filterSelect || filterSelect.disabled) {
        if (searchInput) {
            searchInput.value = '';
        }
        updateRootAgentChatButton();
        return;
    }

    populateRootAgentFilter(configs);

    const savedSearch = localStorage.getItem('agentSearchFilter') || '';
    if (searchInput && savedSearch) {
        searchInput.value = savedSearch;
    }

    applyFiltersAndRender(configs);
    updateRootAgentChatButton();

    if (searchInput) {
        searchInput.addEventListener('input', () => filterAgents(configs));
    }
    filterSelect.addEventListener('change', () => filterAgents(configs));
}

// ============================================================================
// Field Visibility Toggles
// ============================================================================

function toggleMaxIterationsField(selectElement, fieldId) {
    const field = document.getElementById(fieldId);
    if (selectElement.value === 'root') {
        field.style.display = 'block';
    } else {
        field.style.display = 'none';
    }
}

